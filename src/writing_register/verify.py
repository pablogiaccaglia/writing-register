"""The checker: verify what a rewrite added or changed against the code.

2026-09-15. `wr humanize` rewrites a document with a model that has the
document and a few of its sources but not the repository. In one repository
that day it wrote five sentences that were false and passed every string
check: "the report writes each finding to a database" (a different script
does), "snapshot folders are removed after 13 months"
(nothing removes them). A session found them by reading every diff against
the code. This module does that reading with a second call through spawn.py,
which can Read, Grep and Glob an export of the tracked files and nothing else.

Wrong sentences were under 1% of the changed ones, so a checker that is wrong
5% of the time in the other direction would revert far more good sentences
than bad. Every rule here keeps false reverts down:

- a TRUE or FALSE cites the line that decides the claim, and wr checks the
  file exists inside the export, is not a secrets file, is code when the claim
  is about what code does, and holds the quoted span within three lines;
- the checker is asked to cite the guard a line runs under, but the citation
  does not decide the verdict: checking that it sat in the same file above the
  effect caught no wrong sentence on 2026-09-15 and put back correct ones;
- a verdict resting on an absence names the patterns searched, and wr re-runs
  each one: a match rejects the verdict;
- SAME lets the checker pass a rephrase that claims nothing new, and a FALSE on
  a rephrase must name words the new sentence adds;
- ORIGINAL keeps a sentence that states only what the original document
  already stated, with spans quoted from it that wr finds verbatim; the
  author's own text is the author's to answer for, not the rewrite's;
- NOT_A_FACT keeps advice, an opinion, a lead-in or the document describing
  itself. It is the only verdict wr cannot check, so runs report its count.
  Both were added after the first replay (2026-09-15): on a runbook the first
  version reverted 13 of 24 correct sentences, nearly all advice, policy
  quoted from a document, or restatements;
- the checker's prompt asks it to cite and check the guard around a line, to
  treat a sentence saying something happens as a claim about code even when a
  document sets it as a rule, and to cite the code of the component a sentence
  names. wr enforced all three mechanically in v3 and v4, and measured on eight
  saved replies (2026-09-15) the enforcement caught nothing the checker had not
  and tripled the sentences reverted by mistake (6 of 6 wrong sentences caught
  either way; 42 of 347 other sentences reverted with the rules, 17 without).
  The instructions stay in the prompt and the enforcement is gone;
- a run that demonstrably did not look is refused whole: an omitted claim, too
  many claims unverifiable without a reason outside the repository, or a known
  definition from the export (a probe, marked only on wr's side) not confirmed."""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .changes import Change
from .spawn import SECRET_FILE, Spawn, SpawnFailed

CHECK_TIMEOUT = 600
# Files whose content is behaviour, so a claim about what the code does may
# cite them. The replay agreement run of 2026-09-15 reverted a true sentence
# three times out of three because `.jsx` was missing.
DOCUMENT_SUFFIXES = {".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc", ".org", ".tex",
                     ".pdf", ".docx", ".odt", ".rtf"}
_SECRET_NAME = SECRET_FILE
_SECRET_SHAPE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,})|(github_pat_[A-Za-z0-9_]{20,})|(sk-[A-Za-z0-9_-]{20,})"
    r"|(xox[abprs]-[A-Za-z0-9-]{10,})|(AKIA[0-9A-Z]{16})|(secret_[A-Za-z0-9]{8,})"
    r"|(ntn_[A-Za-z0-9]{20,})"
    # A long run mixing letters and digits; from a review: the old generic
    # pattern masked ordinary file names such as runbooks_station-export.
    r"|((?=[A-Za-z0-9+_-]*[0-9])(?=[A-Za-z0-9+_-]*[A-Za-z])[A-Za-z0-9+_-]{32,})")
_WINDOW = 3
_SPAN_MIN = 8
_SPAN_REPORT = 160
_LAZY_SHARE = 0.3
# Seconds one search the checker says found nothing may take when wr re-runs it.
# Review 2026-09-15: `(a+)+$` took 3 s on 27 bytes in-process.
_NEGATIVE_TIMEOUT = 20
_NEGATIVE_SCAN = r"""
import os, re, sys
pattern, directory = sys.argv[1], sys.argv[2]
try:
    rx = re.compile(pattern)
except re.error:
    rx = re.compile(re.escape(pattern))
for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
    for name in filenames:
        path = os.path.join(dirpath, name)
        if os.path.islink(path) or os.path.getsize(path) > 2_000_000:
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                if rx.search(f.read()):
                    sys.exit(0)
        except OSError:
            continue
sys.exit(1)
"""
_STOP = set("""a an and are as at be been but by can could did do does for from had has have if in
into is it its may might must no not of on or our should so than that the their them then there these
they this those to was we were what when where which while who will with would you your""".split())
_PROBE_PATTERNS = {
    ".py": re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w{3,})\s*\(|^class\s+([A-Za-z_]\w{3,})"),
    ".sh": re.compile(r"^(?:function\s+)?([A-Za-z_]\w{3,})\s*\(\)\s*\{"),
    ".js": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w{3,})"),
    ".ts": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w{3,})"),
}

_CITATION = {"type": ["object", "null"],
             "properties": {"path": {"type": "string"}, "line": {"type": "integer"},
                            "span": {"type": "string"}},
             "required": ["path", "line", "span"]}
SCHEMA = json.dumps({
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["TRUE", "FALSE", "SAME", "ORIGINAL", "NOT_A_FACT", "UNVERIFIABLE"]},
            "claim_type": {"type": "string",
                           "enum": ["mechanism", "policy", "definition", "history", "none"]},
            "effect": _CITATION,
            "condition": _CITATION,
            "negative": {"type": "array", "items": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "dir": {"type": "string"}},
                "required": ["pattern", "dir"]}},
            "reason_code": {"type": ["string", "null"],
                            "enum": ["outside_repo", "not_found", "ambiguous", None]},
            "actor": {"type": ["string", "null"]},
            "delta": {"type": "string"},
            "original": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"}},
        "required": ["id", "verdict", "claim_type", "effect", "reason"]}}},
    "required": ["verdicts"]}, separators=(",", ":"))

SYSTEM = """You check sentences from a rewritten document against the code of the repository it describes. The repository's tracked files are in the directory named in the prompt; read them with Read, Grep and Glob. A document in the repository is not evidence of what code does, but a policy the repository's own documents set is evidence of that policy, and its guides and design notes are evidence of how the team works or what it has decided (claim_type history).

Give each numbered claim one verdict:
- TRUE: the code confirms the sentence. In `effect`, cite the line that performs or decides what the sentence says: the path relative to the repository directory, the line number, and a span copied exactly from that line. When it happens only under a condition, cite the guard in `condition`: same file, above the effect. Leave `condition` null when it is unconditional. A line that only mentions the words, such as a name in a list, a schema or a call that a guard never reaches, decides nothing.
- FALSE: the code contradicts the sentence. Cite the deciding line the same way. When the verdict rests on something being absent (nothing removes X, no module sets Y), list in `negative` each pattern you searched for and the directory you searched, relative to the repository directory. Each one is re-run, and a pattern that matches anything rejects the verdict.
- SAME: only for a changed sentence. The new wording asserts nothing the old wording did not, with the same actor, tense, modality and condition. No citation.
- ORIGINAL: the sentence states only what the original document (shown in the prompt) already states, with the same actor, tense, modality and condition. In `original`, quote exactly the passages of the original document that state it. A sentence that turns advice into a decision, a rule into something that happens by itself, or "should" into "does", is not ORIGINAL.
- NOT_A_FACT: the sentence asserts nothing about the system, the team, a decision or the world: advice to the reader, an opinion, a lead-in such as "The review is complete when:", or a description of the document's own layout. A sentence that says what the tool, a script, a person or a standard does or requires is a fact, however it is worded.
- UNVERIFIABLE: you searched the repository and it cannot decide the claim. Never give it for a claim not searched: search every claim, starting from the files, names and commands the sentence mentions, however broad the sentence is, and search every part of the repository it touches: front-end, browser-extension and deploy code, service files and configuration as well as the main source folder. Set `reason_code` to outside_repo when neither the code nor the repository's documents can show the fact (a decision, a plan, what happened), not_found when you searched and found nothing either way (list the patterns in `negative`), or ambiguous.

When the line that does it sits inside a guard (an if, a while, an except), cite the guard in `condition` and check whether anything can make the guard true. A sentence that says something happens (a folder is removed, the tool raises a finding) is a claim about what the code does even when a policy document sets it as a rule: the document shows the rule exists, not that anything carries it out.

When a sentence names who acts (the report, a script, a collector, the bot, a person), a TRUE must cite the code of the component it names, or code that uses that component. Code of another component does not confirm it, and may show the sentence is FALSE. In `actor`, give the path of the file that is the component the sentence names as acting, or null when it names no component or the component is not code.

For a FALSE on a changed sentence, put in `delta` the words of the new sentence that carry what it adds or changes. For a policy claim, cite the policy document's line in `effect`. `claim_type` is mechanism (what code does), policy (what a rule or document requires), definition (what a name is or where it is defined), history (what was decided or happened), or none. Answer every id exactly once, the last ones included. Never quote a secret. Judge the claims only; do not rewrite anything."""


@dataclass
class Verdict:
    number: int
    verdict: str                 # what the checker said, or OMITTED
    effective: str               # after wr's rules
    claim_type: str = "none"
    cited: str = ""
    citation_ok: bool = False
    reason: str = ""
    reason_code: str | None = None


@dataclass
class RunStats:
    sent: int = 0
    same: int = 0
    original: int = 0
    not_a_fact: int = 0
    true: int = 0
    false: int = 0
    unverifiable: int = 0
    omitted: int = 0
    turns: int = 0
    cost: float = 0.0


@dataclass
class Verification:
    verdicts: list[Verdict] = field(default_factory=list)
    by_number: dict = field(default_factory=dict)
    stats: RunStats = field(default_factory=RunStats)
    did_not_check: bool = False
    why: str = ""
    seconds: float = 0.0
    # The checker's reply per claim number, so the rules can be applied again
    # without another model call (`rejudge`).
    raw: dict = field(default_factory=dict)
    # Each probe and the checker's reply to it, to diagnose a probe refusal.
    probe_replies: list = field(default_factory=list)

    def failing(self) -> set[int]:
        """The claims to revert: contradicted or not verified."""
        return {v.number for v in self.verdicts if v.effective in ("FALSE", "UNVERIFIABLE")}


def _redact(text: str) -> str:
    return _SECRET_SHAPE.sub("[redacted]", text or "")


def _flat(text: str) -> str:
    return " ".join((text or "").split())


_PROBE_SKIP_DIRS = {"tests", "test", "testdata", "fixtures", "vendor", "node_modules", ".git", "third_party"}


def make_probes(root, count: int = 2) -> list[Change]:
    """Known-true claims from the export: `path` defines `name`, for a top-level
    function or class. A checker that does not confirm these did not look.

    Only names defined once, outside test, fixture and vendor folders: a probe
    whose name several files define, or that lives in a copied or test file, can
    be answered UNVERIFIABLE honestly, and one such answer refused a whole run
    on 2026-09-15."""
    root = Path(root)
    found, counts = [], {}
    for path in sorted(root.rglob("*")):
        pattern = _PROBE_PATTERNS.get(path.suffix)
        if not pattern or not path.is_file() or _PROBE_SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        names = []
        for line in lines:
            m = pattern.match(line)
            if m:
                names.append(next(g for g in m.groups() if g))
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        if names:
            found.append((path.relative_to(root).as_posix(), names[0]))
    unique = [(rel, name) for rel, name in found if counts.get(name) == 1]
    chosen = random.sample(unique, min(count, len(unique)))
    return [Change("added", "", f"`{rel}` defines `{name}`.") for rel, name in chosen]


def _numbered(claims: list[Change], probes: list[Change]):
    last = max((c.number for c in claims), default=0)
    return [(c.number, c) for c in claims], [(last + i, p) for i, p in enumerate(probes, 1)]


def build_check_prompt(document: str, claims: list[Change], root, probes: list[Change],
                       original: str = ""):
    """The prompt (claims, the document, the original document) and the system prompt."""
    real, fake = _numbered(claims, probes)
    lines = []
    for number, c in real + fake:
        item = {"id": number, "kind": c.kind, "new": _flat(c.new)}
        if c.kind == "changed":
            item["old"] = _flat(c.old)
        lines.append(json.dumps(item, ensure_ascii=False))
    prompt = "\n".join([
        f"The repository directory is {root}.",
        "",
        "# Claims",
        "",
        "Each line is one claim: `new` is the sentence as the rewrite wrote it, and for a changed sentence `old` is the wording it replaced.",
        "",
        *lines,
        "",
        "# The document",
        "",
        document,
        *(["", "# The original document, before the rewrite", "", original] if original else []),
    ])
    return prompt, SYSTEM


def _inside(root: Path, rel: str) -> Path | None:
    try:
        path = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    except (OSError, ValueError):
        return None
    return path if path.is_relative_to(root.resolve()) and path.is_file() else None


def _span_at(path: Path, line: int, span: str) -> bool:
    span = _flat(span)
    if len(span) < _SPAN_MIN:
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if not isinstance(line, int) or line < 1 or line > len(lines):
        return False
    window = _flat(" ".join(lines[max(0, line - 1 - _WINDOW):line + _WINDOW]))
    return span in window


def _citation(root: Path, cite, claim_type: str) -> tuple[bool, Path | None]:
    if not isinstance(cite, dict):
        return False, None
    path = _inside(root, str(cite.get("path", "")))
    if path is None or _SECRET_NAME.search(path.name):
        return False, None
    # A claim about what code does may cite any file but a document. The fresh gate
    # of 2026-09-15 put back confirmed sentences citing deploy/pulse-default.pa and
    # .gitignore under a list of code suffixes; rejecting documents only kept
    # every catch on the saved runs.
    if claim_type == "mechanism" and path.suffix.lower() in DOCUMENT_SUFFIXES:
        return False, None
    return _span_at(path, cite.get("line"), str(cite.get("span", ""))), path


def _negative_matches(root: Path, negative) -> bool:
    """Whether any pattern the checker says found nothing does find something.

    Each search runs in a child Python with a time limit, so a pattern that
    backtracks forever rejects its verdict instead of hanging wr. A malformed
    entry rejects the verdict too. The searches of one verdict also share a
    count and a time budget: a reply may carry any number of them, and without
    a budget a checker could keep wr busy long after its own call ended (audit
    2026-09-16).""" 
    items = list(negative or [])
    if len(items) > _NEGATIVE_MAX:
        return True
    deadline = time.monotonic() + _NEGATIVE_BUDGET
    for item in items:
        if not isinstance(item, dict):
            return True
        pattern, rel = str(item.get("pattern", "")), str(item.get("dir", "") or ".")
        if not pattern:
            continue
        try:
            directory = (root / rel).resolve()
        except (OSError, ValueError):
            return True
        if not directory.is_relative_to(root.resolve()) or not directory.exists():
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        try:
            found = subprocess.run([sys.executable, "-c", _NEGATIVE_SCAN, pattern, str(directory)],
                                   capture_output=True, timeout=min(_NEGATIVE_TIMEOUT, left))
        except subprocess.TimeoutExpired:
            return True
        if found.returncode != 1:
            return True
    return False


# At most this many searches per verdict, and this many seconds for all of them.
_NEGATIVE_MAX = 20
_NEGATIVE_BUDGET = 60

_NEGATIONS = {"not", "no", "never", "none", "nothing", "nobody", "cannot", "without", "neither", "nor"}


def _tokens(text: str) -> set:
    """Words and numbers, lower case, with negations kept.

    Review 2026-09-15: counting only words that start with a letter
    and skipping stop words hid a changed number and a dropped "not"."""
    words = set(re.findall(r"[a-z0-9][a-z0-9_'-]*", text.lower().replace("n't", " not")))
    return {w for w in words if w not in _STOP or w in _NEGATIONS}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9_'-]{2,}", text.lower()) if w not in _STOP}


def _judge(root: Path, change: Change, raw: dict | None, original: str = "") -> Verdict:
    if raw is None:
        return Verdict(change.number, "OMITTED", "UNVERIFIABLE", reason="the checker gave no verdict")
    verdict = str(raw.get("verdict", "")).upper()
    claim_type = str(raw.get("claim_type") or "none")
    v = Verdict(change.number, verdict, "UNVERIFIABLE", claim_type,
                reason=_redact(str(raw.get("reason", "")))[:400], reason_code=raw.get("reason_code"))
    if verdict == "SAME":
        v.effective = "SAME" if change.kind == "changed" else "UNVERIFIABLE"
        if change.kind != "changed":
            v.reason = "an added sentence cannot be the same as the original; " + v.reason
        return v
    if verdict == "NOT_A_FACT":
        v.effective = "NOT_A_FACT"
        return v
    if verdict == "ORIGINAL":
        quotes = [_flat(q) for q in raw.get("original") or [] if isinstance(q, str)]
        flat_original = _flat(original)
        if quotes and all(len(q) >= _SPAN_MIN and q in flat_original for q in quotes):
            v.effective = "ORIGINAL"
            v.cited = " / ".join(f"`{_redact(q)[:_SPAN_REPORT]}`" for q in quotes[:3])
            v.citation_ok = True
        else:
            v.reason = "the passages it quoted are not in the original document; " + v.reason
        return v
    if verdict not in ("TRUE", "FALSE"):
        return v
    if verdict == "FALSE" and change.kind == "changed":
        delta = _tokens(str(raw.get("delta", "")))
        new_tokens, old_tokens = _tokens(change.new), _tokens(change.old)
        negation_changed = bool(_NEGATIONS & (new_tokens ^ old_tokens))
        if not negation_changed and not (delta & (new_tokens - old_tokens)):
            v.effective = "SAME"
            v.reason = "the checker named nothing the new wording adds; " + v.reason
            return v
    ok, path = _citation(root, raw.get("effect"), claim_type)
    if not ok:
        v.reason = "the citation did not check out; " + v.reason
        return v
    if _negative_matches(root, raw.get("negative")):
        v.reason = "a search it said found nothing does find something; " + v.reason
        return v
    effect = raw["effect"]
    span = _redact(_flat(str(effect.get("span", ""))))[:_SPAN_REPORT]
    v.cited = f"{effect.get('path')}:{effect.get('line')} `{span}`"
    v.citation_ok, v.effective = True, verdict
    return v


def _parse(stdout: str):
    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    if data.get("is_error"):
        raise ValueError(str(data.get("result", "the call reported an error"))[:300])
    structured = data.get("structured_output")
    if not isinstance(structured, dict):
        structured = json.loads(data.get("result") or "")
    if not isinstance(structured, dict):
        raise ValueError("the reply is not an object")
    items = structured.get("verdicts")
    if not isinstance(items, list):
        raise ValueError("no verdicts list")
    by_id = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise ValueError("a verdict without an id")
        if item["id"] in by_id:
            raise ValueError(f"id {item['id']} answered twice")
        by_id[item["id"]] = item
    return by_id, int(data.get("num_turns") or 0), float(data.get("total_cost_usd") or 0)


def verify_claims(document: str, claims: list[Change], root, *, spawn=None, model: str | None = None,
                  timeout: int = CHECK_TIMEOUT, probes: list[Change] | None = None,
                  original: str = "", effort: str | None = None) -> Verification:
    """Ask the checker about every claim and apply wr's rules to its answer."""
    root = Path(root)
    probes = make_probes(root) if probes is None else probes
    real, fake = _numbered(claims, probes)
    prompt, system = build_check_prompt(document, claims, root, probes, original)
    result = Verification()
    started = time.monotonic()
    try:
        answer = (spawn or Spawn()).run(prompt, model=model, effort=effort, timeout=timeout, read_root=root,
                                        system_prompt=system, json_schema=SCHEMA)
        by_id, turns, cost = _parse(answer.stdout)
    except SpawnFailed as e:
        result.did_not_check, result.why = True, f"the checker call failed: {e}"
        return result
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        result.did_not_check = True
        result.why = f"the checker's reply did not follow the format ({_redact(str(e))[:200]})"
        return result
    finally:
        result.seconds = time.monotonic() - started
    try:
        concluded = _conclude(result, by_id, real, fake, root, original, turns, cost)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        # Review 2026-09-15: a malformed verdict used to crash here.
        concluded = Verification(seconds=result.seconds, did_not_check=True)
        concluded.why = f"the checker's reply could not be judged ({_redact(str(e))[:200]})"
    concluded.probe_replies = [{"probe": p.new, "reply": by_id.get(number)} for number, p in fake]
    return concluded


def _conclude(result: Verification, by_id: dict, real, fake, root: Path, original: str,
              turns: int, cost: float) -> Verification:
    stats = RunStats(sent=len(real), turns=turns, cost=cost)
    result.raw = {number: by_id[number] for number, _ in real if number in by_id}
    for number, change in real:
        v = _judge(root, change, by_id.get(number), original)
        result.verdicts.append(v)
        result.by_number[number] = v
        stats.omitted += v.verdict == "OMITTED"
        stats.same += v.effective == "SAME"
        stats.original += v.effective == "ORIGINAL"
        stats.not_a_fact += v.effective == "NOT_A_FACT"
        stats.true += v.effective == "TRUE"
        stats.false += v.effective == "FALSE"
        stats.unverifiable += v.effective == "UNVERIFIABLE"
    result.stats = stats
    lazy = sum(1 for v in result.verdicts
               if v.verdict == "UNVERIFIABLE" and v.reason_code != "outside_repo")
    unconfirmed = [p.new.strip(".") for number, p in fake
                   if _judge(root, p, by_id.get(number)).effective != "TRUE"]
    looked_at = stats.sent - stats.same - stats.original - stats.not_a_fact
    if stats.omitted:
        result.did_not_check = True
        result.why = f"the checker omitted {stats.omitted} of {stats.sent} claims"
    elif lazy >= 2 and lazy > _LAZY_SHARE * stats.sent:
        result.did_not_check = True
        result.why = (f"the checker called {lazy} of {stats.sent} claims unverifiable without "
                      "saying the fact lies outside the repository")
    elif unconfirmed:
        result.did_not_check = True
        result.why = (f"the checker did not confirm {len(unconfirmed)} of {len(fake)} probe definitions "
                      f"taken from the repository ({'; '.join(unconfirmed)}), so it did not look")
    elif looked_at >= 6 and turns <= 2:
        result.did_not_check = True
        result.why = f"the checker judged {looked_at} claims without using a tool"
    return result


def rejudge(raw: dict, claims: list[Change], root, *, original: str = "") -> Verification:
    """Apply wr's rules again to a saved reply, without a model call. Probes and
    the tool-use count are not in a saved reply, so those two checks are skipped."""
    root = Path(root)
    by_id = {int(k): v for k, v in raw.items()}
    real = [(c.number, c) for c in claims]
    return _conclude(Verification(), by_id, real, [], root, original, turns=99, cost=0.0)
