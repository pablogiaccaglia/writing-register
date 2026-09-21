#!/usr/bin/env python3
"""Keep confidential text out of a public repository.

The scan runs in three layers over every file a tree would publish.

1. Generic detectors, always on, that name nothing: UUIDs, IP addresses outside
   the documentation ranges, email addresses outside a short allowlist, home
   directory paths, token shapes, long base64 runs, and a file policy (allowed
   suffixes, a size cap, no symlinks, no nested repository, no `.claude/`).
2. Keyed digests. `scrub/digests.txt` holds HMAC-SHA256(key, term) for each
   confidential term, so the list can sit in the public repository without
   revealing it. Every run of one to four words of every file, every file path
   and optionally a commit message is hashed with the same key and looked up.
   The key comes from `WR_SCRUB_KEY` or `--key-file`. Without it the layer is
   skipped, so a fork or a contributor can run the scan, except on the owner's
   repository (`scrub/owner.txt`) for a push or a pull request from a branch of
   the same repository, where a missing key fails the scan.
3. Plaintext terms (`--terms FILE`) for the owner's own pre-push hook. This is
   the only mode that prints a term.

Output is one line per finding, `path:line:kind` or `path:line:kind:id`, and
never the matched text outside the plaintext mode: the logs of a public
repository's Actions runs are public. Exit 1 on any finding, 0 when clean, 2 on
a usage error.

    scrub_check.py scan --tree . [--exclude PREFIX ...] [--terms FILE] [--message FILE]
    scrub_check.py digest --terms FILE --key-file FILE [--out scrub/digests.txt]

A terms file holds one term per line; a trailing `*` also matches the term
inside a longer word (`acme*` finds "acmecloud"). Lines starting with `#` are
comments. `scrub/allow.txt` lists known-good findings, one per line as
`path<TAB>kind` or `path<TAB>digest-id`, where path may be a glob.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

ALLOWED_SUFFIXES = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".sh", ".txt"}
ALLOWED_NAMES = {"LICENSE", ".gitignore"}
MAX_BYTES = 1_000_000
MAX_TOKENS = 4
INWORD_MIN = 3
ID_CHARS = 12

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e"))

_UUID = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])")
_IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w]|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:])")
_EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)")
_HOME = re.compile(r"(?:/Users|/home)/([A-Za-z0-9._-]+)|(?:~|\$HOME)/(?:\.claude/plans|Desktop)")
_TOKENS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
    r"|xox[bp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")
_WORD = re.compile(r"[^\W_]+")

_DOC_NETS = [ipaddress.ip_network(n) for n in
             ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]
_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
_EMAIL_TLDS = (".example", ".test", ".invalid", ".localhost")
_EMAIL_EXACT = {"noreply@anthropic.com", "git@github.com"}
# Placeholder names a guide may use in a home path.
_HOME_NAMES = {"user", "username", "you", "me", "name", "runner", "example"}
_KEY_ENV = "WR_SCRUB_KEY"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    ident: str = ""      # a digest id or, in plaintext mode, the term

    def __str__(self):
        return f"{self.path}:{self.line}:{self.kind}" + (f":{self.ident}" if self.ident else "")


class UsageError(Exception):
    pass


# Normalisation


def normalise_text(text: str) -> str:
    """NFKC with zero-width characters removed, so a lookalike or a hidden
    character cannot split a name the scan would otherwise see."""
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def words(text: str) -> list[str]:
    """The words of a text, case folded; every character that is not a letter
    or a digit separates words, `-_./` among them."""
    return _WORD.findall(normalise_text(text).casefold())


def normalise_term(term: str) -> str:
    return " ".join(words(term))


def term_digest(key: bytes, term: str) -> str:
    return hmac.new(key, normalise_term(term).encode(), hashlib.sha256).hexdigest()


# The generic layer


def _address_ok(addr) -> bool:
    return addr.is_loopback or addr.is_unspecified or any(addr in net for net in _DOC_NETS)


def _email_ok(local: str, domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    if f"{local.lower()}@{domain}" in _EMAIL_EXACT:
        return True
    last = domain.rsplit(".", 1)[-1]
    if "." not in domain or len(last) < 2:
        return True                      # a stub such as t@t or a@b.c
    if not any(c.isalpha() for c in last):
        return True                      # a package at a version, skills@1.5.20
    return any(domain == d or domain.endswith("." + d) for d in _EMAIL_DOMAINS) or domain.endswith(_EMAIL_TLDS)


def generic_line(line: str) -> list[str]:
    """The kinds of generic finding on one normalised line."""
    kinds = []
    # A placeholder such as 11111111-2222-... repeats one character per group.
    if any(len(set(g)) > 1 for m in _UUID.finditer(line) for g in m.group().split("-")):
        kinds.append("uuid")
    for m in _IPV4.finditer(line):
        try:
            addr = ipaddress.IPv4Address(m.group(1))
        except ValueError:
            continue
        if not _address_ok(addr):
            kinds.append("ipv4")
            break
    for m in _IPV6.finditer(line):
        text = m.group(1)
        if sum(1 for g in text.split(":") if g) < 3:
            continue
        try:
            addr = ipaddress.IPv6Address(text)
        except ValueError:
            continue
        if not _address_ok(addr):
            kinds.append("ipv6")
            break
    for m in _EMAIL.finditer(line):
        if not _email_ok(m.group(1), m.group(2)):
            kinds.append("email")
            break
    for m in _HOME.finditer(line):
        if m.group(1) is None or m.group(1).lower() not in _HOME_NAMES:
            kinds.append("home-path")
            break
    if _TOKENS.search(line):
        kinds.append("token")
    if _BASE64.search(line):
        kinds.append("base64")
    return kinds


def file_policy(root: Path, rel: str) -> str:
    """The file-policy finding for one published path, or ""."""
    path = root / rel
    parts = rel.split("/")
    if ".claude" in parts[:-1]:
        return "claude-dir"
    if path.is_symlink():
        return "symlink"
    if parts[-1] == ".git" or ".git" in parts[:-1]:
        return "nested-git"
    if path.is_dir():
        return "nested-git" if (path / ".git").exists() else ""
    name = parts[-1]
    if name not in ALLOWED_NAMES and Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        return "file-type"
    if name.startswith(".") and name not in ALLOWED_NAMES:
        return "file-type"
    if path.stat().st_size > MAX_BYTES:
        return "file-size"
    return ""


# Which files a tree publishes


def _git_top(root: Path) -> bool:
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == root.resolve()


def published(root: Path) -> list[str]:
    """Every path the tree would publish, relative and with `/` separators.

    In a git repository that is what git tracks or would add (nothing it
    ignores); elsewhere every entry, where a nested `.git` is reported and
    not entered."""
    if _git_top(root):
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                                 "--exclude-standard"], capture_output=True, timeout=120, check=True)
        out = []
        for raw in listed.stdout.split(b"\0"):
            rel = raw.decode("utf-8", "surrogateescape").rstrip("/")
            if rel and rel not in out and (os.path.lexists(root / rel)):
                out.append(rel)
        return sorted(out)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for d in list(dirnames):
            if d == ".git" or (here / d).is_symlink():
                out.append((here / d).relative_to(root).as_posix())
                dirnames.remove(d)
        for f in filenames:
            out.append((here / f).relative_to(root).as_posix())
    return sorted(out)


# The term layers


@dataclass
class Terms:
    """Terms to look for, as digests (with a key) or plaintext."""
    key: bytes | None = None
    digests: dict | None = None        # digest bytes -> id, n-gram terms
    inword: dict | None = None         # digest bytes -> id, in-word terms
    plain: dict | None = None          # normalised term -> itself
    plain_inword: list | None = None

    def match(self, gram: str) -> str | None:
        if self.plain is not None and gram in self.plain:
            return gram
        if self.digests:
            d = hmac.digest(self.key, gram.encode(), "sha256")
            return self.digests.get(d)
        return None

    def match_inword(self, word: str) -> list[str]:
        found = []
        for term in self.plain_inword or ():
            if term in word:
                found.append(term)
        if self.inword and len(word) >= INWORD_MIN:
            seen = set()
            for size in range(INWORD_MIN, len(word) + 1):
                for start in range(0, len(word) - size + 1):
                    piece = word[start:start + size]
                    if piece in seen:
                        continue
                    seen.add(piece)
                    ident = self.inword.get(hmac.digest(self.key, piece.encode(), "sha256"))
                    if ident:
                        found.append(ident)
        return found


def _word_lines(text: str) -> list[tuple[str, int]]:
    out = []
    for number, line in enumerate(normalise_text(text).split("\n"), 1):
        out += [(w, number) for w in _WORD.findall(line.casefold())]
    return out


def term_findings(name: str, text: str, terms: Terms, kind: str, line_zero: bool = False) -> list[Finding]:
    """Findings for every run of one to four words that is a term, and every
    word holding an in-word term."""
    found = []
    tokens = _word_lines(text)
    for i in range(len(tokens)):
        for n in range(1, MAX_TOKENS + 1):
            if i + n > len(tokens):
                break
            ident = terms.match(" ".join(w for w, _ in tokens[i:i + n]))
            if ident:
                found.append(Finding(name, 0 if line_zero else tokens[i][1], kind, ident))
    if terms.inword or terms.plain_inword:
        cache: dict = {}
        for w, number in tokens:
            if w not in cache:
                cache[w] = terms.match_inword(w)
            for ident in cache[w]:
                found.append(Finding(name, 0 if line_zero else number, kind, ident))
    return found


def masked_path(rel: str, terms: Terms) -> str:
    """`rel` with every word that forms a digest term replaced by `*`, so a
    finding can be located without printing the term its path holds."""
    text = normalise_text(rel)
    spans = [(m.start(), m.end(), m.group().casefold()) for m in _WORD.finditer(text)]
    hit = set()
    for i in range(len(spans)):
        for n in range(1, MAX_TOKENS + 1):
            if i + n <= len(spans) and terms.match(" ".join(w for _, _, w in spans[i:i + n])):
                hit.update(range(i, i + n))
        if terms.match_inword(spans[i][2]):
            hit.add(i)
    for i in sorted(hit, reverse=True):
        start, end, _ = spans[i]
        text = text[:start] + "*" + text[end:]
    return text


def read_terms(path: Path) -> list[tuple[str, bool]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise UsageError(f"cannot read the terms file: {e.strerror}") from e
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        inword = line.endswith("*")
        term = normalise_term(line.rstrip("*"))
        if term:
            out.append((term, inword))
    return out


def digest_lines(key: bytes, terms: list[tuple[str, bool]]) -> list[str]:
    """`digest<TAB>word count<TAB>flag` per term, sorted so the file keeps no order.
    An in-word term gets a second line, flag `w`, over its words run together."""
    lines = set()
    for term, inword in terms:
        lines.add(f"{term_digest(key, term)}\t{len(term.split())}\t-")
        if inword:
            joined = term.replace(" ", "")
            lines.add(f"{hmac.new(key, joined.encode(), hashlib.sha256).hexdigest()}\t1\tw")
    return sorted(lines)


def load_digests(path: Path) -> tuple[dict, dict]:
    ngram, inword = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        digest = bytes.fromhex(parts[0].strip())
        flag = parts[2].strip() if len(parts) > 2 else "-"
        (inword if flag == "w" else ngram)[digest] = parts[0].strip()[:ID_CHARS]
    return ngram, inword


# The key and the owner's repository


def _read_key(key_file: str | None) -> bytes | None:
    if key_file:
        try:
            return Path(key_file).read_bytes().rstrip(b"\r\n")
        except OSError as e:
            raise UsageError(f"cannot read the key file: {e.strerror}") from e
    value = os.environ.get(_KEY_ENV)
    return value.encode() if value else None


def _owner(scrub_dir: Path) -> str:
    try:
        lines = (scrub_dir / "owner.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return next((line.strip() for line in lines if line.strip() and not line.startswith("#")), "")


def key_required(scrub_dir: Path) -> bool:
    """Whether this run is the owner's repository checking its own code, where
    skipping the digest layer would let a confidential term through."""
    repo, owner = os.environ.get("GITHUB_REPOSITORY", ""), _owner(scrub_dir)
    if not repo or not owner or not (repo == owner or ("/" not in owner and repo.startswith(owner + "/"))):
        return False
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "push":
        return True
    if event in ("pull_request", "pull_request_target"):
        try:
            payload = json.loads(Path(os.environ.get("GITHUB_EVENT_PATH", "")).read_text())
            head = payload["pull_request"]["head"]["repo"]["full_name"]
        except (OSError, ValueError, KeyError, TypeError):
            return True                  # cannot tell, so the stricter answer
        return head == repo
    return False


# Allowed findings


def load_allow(scrub_dir: Path) -> list[tuple[str, str]]:
    try:
        lines = (scrub_dir / "allow.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        if not line.strip() or line.startswith("#") or "\t" not in line:
            continue
        path, what = line.split("\t", 1)
        out.append((path.strip(), what.strip()))
    return out


def allowed(finding: Finding, allow: list[tuple[str, str]]) -> bool:
    for pattern, what in allow:
        if not (finding.path == pattern or fnmatch.fnmatchcase(finding.path, pattern)):
            continue
        if what == finding.kind or (finding.ident and finding.ident.startswith(what[:ID_CHARS])
                                    and finding.kind == "digest"):
            return True
    return False


# The scan


def scan(root: Path, excludes=(), terms_file=None, message=None, key_file=None,
         scrub_dir: Path | None = None) -> tuple[list[Finding], list[str]]:
    """The findings in a tree and the notes for stderr."""
    root = root.resolve()
    scrub_dir = (scrub_dir or root / "scrub").resolve()
    notes = []
    key = _read_key(key_file)
    digests_file = scrub_dir / "digests.txt"
    layers: list[tuple[Terms, str]] = []
    if digests_file.is_file():
        if key:
            ngram, inword = load_digests(digests_file)
            layers.append((Terms(key=key, digests=ngram, inword=inword), "digest"))
        elif key_required(scrub_dir):
            raise KeyMissing(f"{_KEY_ENV} is not set, and this repository must not skip the digest layer")
        else:
            notes.append(f"digest layer skipped: no key ({_KEY_ENV} or --key-file)")
    if terms_file:
        plain = read_terms(Path(terms_file))
        layers.append((Terms(plain={t: t for t, _ in plain},
                             plain_inword=[t.replace(" ", "") for t, w in plain if w]), "term"))
    try:
        digests_rel = digests_file.relative_to(root).as_posix()
    except ValueError:
        digests_rel = None

    findings: list[Finding] = []
    for rel in published(root):
        if any(rel.startswith(prefix) for prefix in excludes):
            continue
        for terms, kind in layers:
            findings += term_findings(rel, rel, terms, kind, line_zero=True)
        problem = file_policy(root, rel)
        if problem:
            findings.append(Finding(rel, 0, problem))
            continue
        if rel == digests_rel or (root / rel).is_dir():
            continue
        text = normalise_text((root / rel).read_bytes().decode("utf-8", "replace"))
        for number, line in enumerate(text.split("\n"), 1):
            findings += [Finding(rel, number, kind) for kind in generic_line(line)]
        for terms, kind in layers:
            findings += term_findings(rel, text, terms, kind)
    if message:
        try:
            text = normalise_text(Path(message).read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            raise UsageError(f"cannot read the message: {e.strerror}") from e
        for number, line in enumerate(text.split("\n"), 1):
            findings += [Finding("<message>", number, kind) for kind in generic_line(line)]
        for terms, kind in layers:
            findings += term_findings("<message>", text, terms, kind)
    allow = load_allow(scrub_dir)
    unique = list(dict.fromkeys(f for f in findings if not allowed(f, allow)))
    # A path that holds a term is printed with that term masked, whatever the
    # finding on it: the path is text the public log would otherwise carry.
    masks = {}
    for terms, kind in layers:
        if kind == "digest":
            for f in unique:
                if f.kind == "digest" and f.line == 0 and f.path not in masks:
                    masks[f.path] = masked_path(f.path, terms)
    unique = [replace(f, path=masks.get(f.path, f.path)) for f in unique]
    return unique, notes


class KeyMissing(Exception):
    pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", help="scan a tree")
    s.add_argument("--tree", required=True)
    s.add_argument("--exclude", action="append", default=[], metavar="PREFIX")
    s.add_argument("--terms", help="plaintext terms, for the owner's own use; prints the term")
    s.add_argument("--message", help="a commit message to scan too")
    s.add_argument("--key-file")
    s.add_argument("--scrub-dir", help="where digests.txt, allow.txt and owner.txt live (default TREE/scrub)")
    d = sub.add_parser("digest", help="print digests.txt lines for a terms file")
    d.add_argument("--terms", required=True)
    d.add_argument("--key-file", required=True)
    d.add_argument("--out")
    args = ap.parse_args(argv)
    try:
        if args.command == "digest":
            key = _read_key(args.key_file)
            if not key:
                raise UsageError("the key file is empty")
            lines = digest_lines(key, read_terms(Path(args.terms)))
            header = "# HMAC-SHA256 of each term<TAB>word count<TAB>w when it also matches inside a word\n"
            body = header + "".join(line + "\n" for line in lines)
            if args.out:
                Path(args.out).write_text(body)
            else:
                sys.stdout.write(body)
            return 0
        tree = Path(args.tree)
        if not tree.is_dir():
            raise UsageError(f"{args.tree} is not a directory")
        findings, notes = scan(tree, args.exclude, args.terms, args.message, args.key_file,
                               Path(args.scrub_dir) if args.scrub_dir else None)
    except UsageError as e:
        print(f"scrub_check: {e}", file=sys.stderr)
        return 2
    except KeyMissing as e:
        print(f"scrub_check: {e}", file=sys.stderr)
        return 1
    for note in notes:
        print(f"scrub_check: {note}", file=sys.stderr)
    for f in findings:
        print(f)
    if findings:
        print(f"scrub_check: {len(findings)} finding{'s' if len(findings) != 1 else ''}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
