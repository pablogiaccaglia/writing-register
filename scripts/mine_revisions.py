#!/usr/bin/env python3
"""Every commit to a followed document, with the paragraphs it removed and
added, and the document as it stands at HEAD.

The repositories and their documents come from the `[[repositories]]` tables
of mine.toml, each a `path` and a list of `docs` globs relative to it, and the
dates from its `[window]`. One record is written per commit and document
(`kind: "commit"`), and one per document at HEAD (`kind: "final"`), which the
episode builder searches for the final version of a passage.

A paragraph is a block of a markdown file between blank lines, or the text of
one block element of an HTML file (scripts and styles dropped). The removed
paragraphs are those of the old version the new one no longer has, and the
added ones the reverse, so a paragraph that only moved is neither.

Only git is run, through subprocess; nothing here starts a model.
"""
import argparse
import datetime
import html.parser
import json
import pathlib
import re
import subprocess
import sys
import time
import tomllib

TEXT_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt", ".rst"}
MAX_BLOB = 8 * 1024 * 1024
RUN = 6
_WORD = re.compile(r"\w+(?:['’]\w+)*")


def load_config(path):
    data = tomllib.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    window = data.get("window") or {}
    repos = [{"path": str(pathlib.Path(r["path"]).expanduser()), "docs": list(r.get("docs") or [])}
             for r in data.get("repositories") or []]
    return {"repositories": repos, "since": str(window.get("since") or ""), "until": str(window.get("until") or "")}


# ---------- paragraphs ----------

_BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "section", "article",
           "figcaption", "caption", "table", "ul", "ol", "pre", "blockquote", "details", "summary", "br",
           "header", "footer", "main", "aside", "nav", "dt", "dd", "figure", "body", "html", "head", "title"}


class _Blocks(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.skip = [], [], 0

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        if text:
            self.out.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"):
            self.skip += 1
        elif tag in _BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def paragraphs(text, path):
    """The paragraphs of a document, in order."""
    if pathlib.PurePath(path).suffix.lower() in (".html", ".htm"):
        parser = _Blocks()
        parser.feed(text)
        parser.close()
        parser._flush()
        return parser.out
    return [p.strip() for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


def _diff(old, new):
    old_set, new_set = set(old), set(new)
    removed = list(dict.fromkeys(p for p in old if p not in new_set))
    added = list(dict.fromkeys(p for p in new if p not in old_set))
    return removed, added


# ---------- overlap ----------

def _words(text):
    return [m.group(0).lower().replace("’", "'") for m in _WORD.finditer(text or "")]


def overlaps(added, edits, run=RUN):
    """True when the added paragraphs share a run of `run` normalised words
    with the new text of the edits, or a shorter paragraph appears whole in
    one of them."""
    edit_words = [_words(e) for e in edits if e]
    if not edit_words:
        return False
    grams = set()
    for w in edit_words:
        grams.update(tuple(w[i:i + run]) for i in range(len(w) - run + 1))
    joined = [" ".join(w) for w in edit_words]
    for para in added:
        w = _words(para)
        if len(w) >= run:
            if any(tuple(w[i:i + run]) in grams for i in range(len(w) - run + 1)):
                return True
        elif w and any(f" {' '.join(w)} " in f" {j} " for j in joined):
            return True
    return False


# ---------- git ----------

def _git(repo, *args, data=None):
    return subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, check=True).stdout


def _pathspecs(globs):
    return [f":(glob){g}" for g in globs]


def _is_text(path):
    return pathlib.PurePath(path).suffix.lower() in TEXT_SUFFIXES


def _blobs(repo, specs):
    """The content of `rev:path` for each spec, None where it does not exist,
    through one `git cat-file --batch`."""
    if not specs:
        return {}
    raw = _git(repo, "cat-file", "--batch", data=("\n".join(specs) + "\n").encode())
    out, pos = {}, 0
    for spec in specs:
        end = raw.index(b"\n", pos)
        header = raw[pos:end].decode(errors="replace")
        pos = end + 1
        if header.endswith(" missing") or header.endswith(" ambiguous"):
            out[spec] = None
            continue
        parts = header.rsplit(" ", 2)
        if len(parts) == 3 and parts[1] == "blob":
            size = int(parts[2])
            body = raw[pos:pos + size]
            pos += size + 1
            out[spec] = body.decode("utf-8", errors="replace") if size <= MAX_BLOB else None
        else:
            pos += int(parts[2]) + 1
            out[spec] = None
    return out


def _utc(stamp):
    value = datetime.datetime.fromisoformat(stamp)
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commits(repo, globs, since="", until=""):
    """One record per commit and followed document it changed, oldest first."""
    repo = pathlib.Path(repo)
    args = ["log", "--all", "--no-merges", "--no-renames", "--reverse", "--format=%x1e%H%x1f%P%x1f%cI%x1f%s",
            "--name-only"]
    if since:
        args.append(f"--since={since}T00:00:00")
    if until:
        day = datetime.date.fromisoformat(until) + datetime.timedelta(days=1)
        args.append(f"--until={day.isoformat()}T00:00:00")
    out = _git(repo, *args, "--", *_pathspecs(globs)).decode("utf-8", errors="replace")
    entries = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, names = chunk.partition("\n")
        sha, parents, stamp, subject = head.split("\x1f", 3)
        parent = parents.split()[0] if parents.split() else ""
        for name in names.split("\n"):
            name = name.strip()
            if name and _is_text(name):
                entries.append((sha, parent, stamp, subject, name))
    specs = []
    for sha, parent, _, _, name in entries:
        specs.append(f"{sha}:{name}")
        if parent:
            specs.append(f"{parent}:{name}")
    blobs = _blobs(repo, list(dict.fromkeys(specs)))
    seen = set()
    for sha, parent, stamp, subject, name in entries:
        if (sha, name) in seen:
            continue
        seen.add((sha, name))
        new = blobs.get(f"{sha}:{name}")
        old = blobs.get(f"{parent}:{name}") if parent else ""
        removed, added = _diff(paragraphs(old or "", name), paragraphs(new or "", name))
        if not removed and not added:
            continue
        yield {"kind": "commit", "repo": str(repo), "commit": sha, "ts": _utc(stamp), "subject": subject,
               "path": name, "abs_path": str(repo / name), "removed": removed, "added": added,
               "deleted": new is None}


def finals(repo, globs):
    """Each followed document as it stands at HEAD, split into paragraphs."""
    repo = pathlib.Path(repo)
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    names = [n for n in _git(repo, "ls-files", "-z", "--", *_pathspecs(globs)).decode().split("\0") if n]
    names = [n for n in names if _is_text(n)]
    blobs = _blobs(repo, [f"HEAD:{n}" for n in names])
    for name in names:
        text = blobs.get(f"HEAD:{name}")
        if text is None:
            continue
        yield {"kind": "final", "repo": str(repo), "commit": head, "path": name, "abs_path": str(repo / name),
               "paragraphs": paragraphs(text, name)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="mine.toml, with [[repositories]] and [window]")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    started = time.time()
    conf = load_config(args.config)
    n_commits, n_final = 0, 0
    with open(args.out, "w", encoding="utf-8") as out:
        for r in conf["repositories"]:
            if not (pathlib.Path(r["path"]) / ".git").exists():
                print(f"skipped, not a git repository: {r['path']}", file=sys.stderr)
                continue
            try:
                found = list(commits(r["path"], r["docs"], conf["since"], conf["until"]))
                docs = list(finals(r["path"], r["docs"]))
            except subprocess.CalledProcessError as exc:
                print(f"git failed in {r['path']}: {exc.stderr.decode(errors='replace').strip()}", file=sys.stderr)
                continue
            for rec in found + docs:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_commits += len(found)
            n_final += len(docs)
            hashes = len({c["commit"] for c in found})
            print(f"{r['path']}: {hashes} commits, {len(found)} commit-document records, "
                  f"{len(docs)} documents at HEAD", file=sys.stderr)
    print(f"{n_commits} commit records, {n_final} final documents, {time.time() - started:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
