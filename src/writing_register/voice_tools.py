"""Tools for voice directories: checking, building the readable view, showing a
rule with everything that points at it, and splitting a single-file voice.

None of this runs in a hook. The runtime (voice.py) reads only the manifest and
the rule files and stays lenient, so a formatting slip never leaves a session
without its voice; the checks live here and in the tests (2026-09-21).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import voice as v
from .config import CORE_MARKER, GENERATED

ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*$")
_MARK = re.compile(r"\{#([^\s}]+)(?:\s+refines=([^\s}]+))?\}")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_RELATIVE_LINK = re.compile(r"\]\((?!https?://|mailto:|#)[^)]+\)")
_DATE = re.compile(r"\b(20\d\d-\d\d(?:-\d\d)?)\b")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_WORD_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(20\d\d)\b", re.I)


def _dates_in(line: str) -> list[str]:
    """Dates in a line: 2026-09-21, 2026-09, or a month and year in words, as YYYY-MM."""
    found = _DATE.findall(line)
    found += [f"{year}-{_MONTHS.index(month.lower()) + 1:02d}"
              for month, year in _WORD_DATE.findall(line)]
    return found
_ISO_DAY = re.compile(r"^20\d\d-\d\d-\d\d$")
PEOPLE_AT_ROOT = {"about.md", "decisions.md", "maintaining.md"}
_STOP = set("a an and are as at be but by for from has have in is it its not of on or so "
            "that the their them then there this to was were when which who with".split())


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    level: str          # "error", "warning" or "advisory"
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.level}: {self.message}"


@dataclass
class Rule:
    id: str
    refines: tuple[str, ...]
    path: str           # relative to the voice root
    line: int
    text: str
    position: int       # order in which the model reads it


@dataclass
class Entry:
    path: str
    line: int
    heading: str
    rules: tuple[str, ...] | None       # None when the entry has no Rules: line
    decided: str = ""
    confirmed: str = ""
    status: str = ""
    retires: tuple[str, ...] = ()
    dates: list[str] = field(default_factory=list)
    body: str = ""


def _rules_in(root: Path, rel: str, start: int, findings: list[Finding]) -> list[Rule]:
    path = root / "rules" / rel
    shown = f"rules/{rel}"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rules, position = [], start
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        if number == 1 and raw.startswith("# "):
            continue
        body = _LIST.sub("", raw, count=1).lstrip()
        if not body.startswith("{#"):
            findings.append(Finding(shown, number, "error",
                                    f"{shown} line {number} is not a rule: every line of a rule "
                                    f"file is the title or starts with a {{#id}} marker"))
            continue
        if "<!--" in raw or "wr:end-of-core" in raw:
            findings.append(Finding(shown, number, "error",
                                    f"{shown} line {number} holds an HTML comment, which would "
                                    f"reach the model or cut the generated view short"))
        if _RELATIVE_LINK.search(raw):
            findings.append(Finding(shown, number, "error",
                                    f"{shown} line {number} has a relative link, which points "
                                    f"somewhere different from the generated view"))
        marks = list(_MARK.finditer(body))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
            text = body[m.end():end].strip()
            refines = tuple(r for r in (m.group(2) or "").split(",") if r)
            rules.append(Rule(m.group(1), refines, shown, number, text, position))
            position += 1
    return rules


def _entries(root: Path, rel: str) -> list[Entry]:
    path = root / rel
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    entries: list[Entry] = []
    current: Entry | None = None
    in_header = False
    for number, line in enumerate(lines, 1):
        if line.startswith("## "):
            current = Entry(rel, number, line[3:].strip(), None)
            entries.append(current)
            in_header = True
            continue
        if current is None:
            continue
        if in_header and not line.strip():
            in_header = False
            continue
        key, _, value = line.partition(":")
        if in_header and key in ("Rules", "Decided", "Confirmed", "Retires", "Status"):
            value = value.strip()
            if key == "Rules":
                current.rules = () if value.lower() == "none" else tuple(
                    r.strip() for r in value.split(",") if r.strip())
            elif key == "Decided":
                current.decided = value
            elif key == "Confirmed":
                current.confirmed = value
            elif key == "Status":
                current.status = value
            elif key == "Retires":
                current.retires = tuple(r.strip() for r in value.split(",") if r.strip())
            continue
        in_header = False
        current.body += line + "\n"
        current.dates += _dates_in(line)
    return entries


def _sentences(text: str) -> list[tuple[str, str]]:
    """Each sentence of three words or more, as (normalised, as written)."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[a-z0-9']+", s.lower())
        if len(words) >= 3:
            out.append((" ".join(words), s.strip()))
    return out


def _content_trigrams(text: str) -> set[tuple[str, ...]]:
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOP]
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def check(root, overlaps: bool = False) -> list[Finding]:
    """Every problem with a voice directory, as findings."""
    root = Path(root)
    findings: list[Finding] = []
    try:
        manifest = v.load(root)
    except v.VoiceError as e:
        return [Finding(v.MANIFEST, 1, "error", str(e))]

    # Files: every listed file exists, every rule file is listed, nothing stray.
    listed = list(manifest.order)
    for rel in listed:
        if not (root / "rules" / rel).is_file():
            findings.append(Finding(v.MANIFEST, 1, "error",
                                    f"order lists rules/{rel}, which does not exist"))
    on_disk = sorted(p.relative_to(root / "rules").as_posix()
                     for p in (root / "rules").rglob("*.md")) if (root / "rules").is_dir() else []
    for rel in on_disk:
        if rel not in listed:
            findings.append(Finding(f"rules/{rel}", 1, "error",
                                    f"rules/{rel} is not in {v.MANIFEST}'s order, so the model "
                                    f"never receives it"))
    for p in sorted(root.glob("*.md")):
        if p.name not in PEOPLE_AT_ROOT:
            findings.append(Finding(p.name, 1, "error",
                                    f"{p.name} is not part of a voice; rules go under rules/, "
                                    f"and the people files are {', '.join(sorted(PEOPLE_AT_ROOT))}"))
    for i, rel in enumerate(listed):
        if "/" in rel:
            parent = rel.rsplit("/", 1)[0] + ".md"
            if parent not in listed or listed.index(parent) > i:
                findings.append(Finding(v.MANIFEST, 1, "error",
                                        f"rules/{rel} must come after rules/{parent} in order, "
                                        f"because it renders under that file's heading"))

    # Rules: markers, identifiers, refinements, duplicates.
    rules: list[Rule] = []
    for rel in listed:
        rules += _rules_in(root, rel, len(rules), findings)
    seen: dict[str, Rule] = {}
    stems: dict[str, str] = {}
    for rel in listed:
        stem = Path(rel).stem
        if stem in stems and stems[stem] != rel:
            findings.append(Finding(f"rules/{rel}", 1, "error",
                                    f"rules/{rel} and rules/{stems[stem]} share the name {stem}, "
                                    f"so their identifiers would clash"))
        stems.setdefault(stem, rel)
    for r in rules:
        stem = Path(r.path).stem
        if not ID.match(r.id):
            findings.append(Finding(r.path, r.line, "error",
                                    f"{r.id} is not an identifier of the form stem.slug"))
        elif r.id.split(".")[0] != stem:
            findings.append(Finding(r.path, r.line, "error",
                                    f"{r.id} in {r.path} should start with {stem}., the name of "
                                    f"its file"))
        if r.id in seen:
            findings.append(Finding(r.path, r.line, "error",
                                    f"{r.id} is defined twice, in {seen[r.id].path} line "
                                    f"{seen[r.id].line} and here"))
        else:
            seen[r.id] = r
    for r in rules:
        for target in r.refines:
            if target == r.id:
                findings.append(Finding(r.path, r.line, "error", f"{r.id} refines itself"))
            elif target not in seen:
                findings.append(Finding(r.path, r.line, "error",
                                        f"{r.id} refines {target}, which is not a rule"))
            elif seen[target].position > r.position:
                findings.append(Finding(r.path, r.line, "error",
                                        f"{r.id} refines {target}, which the model reads later; "
                                        f"the rule it refines must come earlier"))
    owner: dict[str, str] = {}
    for r in rules:
        for normal, written in _sentences(r.text):
            if normal in owner and owner[normal] != r.id:
                findings.append(Finding(r.path, r.line, "error",
                                        f"{r.id} repeats a sentence of {owner[normal]}: "
                                        f"\"{written[:100]}\""))
            owner.setdefault(normal, r.id)
    for r in rules:
        if _DATE.search(r.text) or "measured on" in r.text.lower():
            findings.append(Finding(r.path, r.line, "warning",
                                    f"{r.id} carries a date or a measurement, which travels in "
                                    f"every session; it belongs in the rule's evidence"))

    # Budget.
    if manifest.budget is not None and not any(f.level == "error" and f.path == v.MANIFEST
                                               for f in findings):
        try:
            core = v.assemble_core(manifest)
        except v.VoiceError as e:
            findings.append(Finding(v.MANIFEST, 1, "error", str(e)))
        else:
            if len(core) > manifest.budget:
                sizes = ", ".join(f"{rel} {len(v._render(manifest, rel)):,}"
                                  for rel in listed)
                findings.append(Finding(v.MANIFEST, 1, "error",
                                        f"the core is {len(core):,} characters, "
                                        f"{len(core) - manifest.budget:,} over the budget of "
                                        f"{manifest.budget:,}: {sizes}"))

    # Evidence and decisions.
    evidence = []
    if (root / "evidence").is_dir():
        for p in sorted((root / "evidence").rglob("*.md")):
            evidence += _entries(root, p.relative_to(root).as_posix())
    decisions = _entries(root, "decisions.md") if (root / "decisions.md").is_file() else []
    retired = {rid for d in decisions for rid in d.retires}
    for rid in sorted(retired & set(seen)):
        findings.append(Finding("decisions.md", 1, "error",
                                f"a decision retires {rid}, which is still a rule; delete the "
                                f"rule or the retirement"))
    for e in evidence + decisions:
        if e.rules is None:
            findings.append(Finding(e.path, e.line, "error",
                                    f"the entry \"{e.heading}\" has no Rules: line naming the "
                                    f"rules it supports"))
            continue
        for rid in e.rules:
            if rid not in seen and rid not in retired:
                findings.append(Finding(e.path, e.line, "error",
                                        f"the entry \"{e.heading}\" names {rid}, which is not a "
                                        f"rule and was never retired"))
    for e in evidence:
        if not e.dates:
            findings.append(Finding(e.path, e.line, "error",
                                    f"the evidence \"{e.heading}\" carries no date"))
    for d in decisions:
        if not _ISO_DAY.match(d.decided):
            findings.append(Finding(d.path, d.line, "error",
                                    f"the decision \"{d.heading}\" has no Decided: YYYY-MM-DD line"))
    if manifest.evidence == "required":
        cited = {rid for e in evidence + decisions for rid in (e.rules or ())}
        for r in rules:
            if r.id not in cited:
                findings.append(Finding(r.path, r.line, "error",
                                        f"{r.id} has no evidence and no decision behind it"))
    for d in decisions:
        if not d.rules or not _ISO_DAY.match(d.decided):
            continue
        when = max(d.decided, d.confirmed)
        newest = max((date for e in evidence if set(e.rules or ()) & set(d.rules)
                      for date in e.dates), default="")
        if newest > when:
            findings.append(Finding(d.path, d.line, "warning",
                                    f"the decision \"{d.heading}\" dates from {when}, but its "
                                    f"rules have evidence from {newest}; confirm or update it"))

    view = view_path(root)
    if view.is_file() and not any(f.level == "error" and f.path == v.MANIFEST for f in findings):
        try:
            fresh = render_view(root)
        except (v.VoiceError, OSError, UnicodeDecodeError):
            fresh = None
        if fresh is not None and view.read_text(encoding="utf-8") != fresh:
            findings.append(Finding(view.name, 1, "error",
                                    f"the view {view.name} is out of date; run `wr voice build`, "
                                    f"and edit the files in {root.name}/ rather than the view"))

    if overlaps:
        grams = {r.id: _content_trigrams(r.text) for r in rules}
        linked = {(r.id, t) for r in rules for t in r.refines}
        for i, a in enumerate(rules):
            for b in rules[i + 1:]:
                if (a.id, b.id) in linked or (b.id, a.id) in linked:
                    continue
                shared = grams[a.id] & grams[b.id]
                if shared:
                    phrase = " ".join(sorted(shared)[0])
                    findings.append(Finding(b.path, b.line, "advisory",
                                            f"{a.id} and {b.id} share \"{phrase}\"; one may "
                                            f"restate the other"))
    return findings


# The generated view: one readable file beside the directory.

_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def view_path(root) -> Path:
    """Where the view of a voice directory goes: beside it, as <name>.md."""
    return Path(root).with_suffix(".md")


def _rebase(text: str, source_dir: Path, view_dir: Path) -> str:
    """Relative links rewritten so they point, from the view, where they pointed
    from the file they came from."""
    def fix(m):
        label, target = m.group(1), m.group(2)
        if re.match(r"(?:[a-z]+:|#|/)", target):
            return m.group(0)
        path_part, hash_, anchor = target.partition("#")
        if not path_part:
            return m.group(0)
        absolute = os.path.normpath(os.path.join(source_dir, path_part))
        return f"[{label}]({os.path.relpath(absolute, view_dir)}{hash_}{anchor})"
    return _LINK.sub(fix, text)


def _people_file(root: Path, name: str, view_dir: Path) -> str:
    path = root / name
    if not path.is_file():
        return ""
    return _rebase(path.read_text(encoding="utf-8").strip(), path.parent, view_dir)


def _short(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def render_view(root) -> str:
    """The whole voice as one file: a generated notice, the core, the core
    marker, and what people keep. It is a valid single-file voice whose core is
    exactly the directory's, which a test pins."""
    root = Path(root)
    manifest = v.load(root)
    core = v.assemble_core(manifest)
    view_dir = view_path(root).parent
    notice = f"{GENERATED} from {root.name}/ by wr voice build; edit the files there -->"
    parts = [f"{notice}\n{core}\n{CORE_MARKER}",
             f"This file is generated from the files in `{root.name}/` by `wr voice build`. "
             f"Edit those files, not this one."]
    about = _people_file(root, "about.md", view_dir)
    if about:
        parts.append(about)

    rules: list[Rule] = []
    ignored: list[Finding] = []
    for rel in manifest.order:
        rules += _rules_in(root, rel, len(rules), ignored)
    by_id = {r.id: r for r in rules}
    evidence: list[Entry] = []
    if (root / "evidence").is_dir():
        for p in sorted((root / "evidence").rglob("*.md")):
            evidence += _entries(root, p.relative_to(root).as_posix())
    if evidence:
        def position(e: Entry) -> int:
            return min((by_id[r].position for r in (e.rules or ()) if r in by_id), default=10**9)
        chunks = ["# The rules and their evidence"]
        for e in sorted(evidence, key=position):
            supports = ", ".join(f'{rid} ("{_short(by_id[rid].text)}")' if rid in by_id
                                 else f"{rid} (retired)" for rid in (e.rules or ()))
            body = _rebase(e.body.strip(), (root / e.path).parent, view_dir)
            chunks.append(f"## {e.heading}\n\nSupports: {supports}\n\n{body}".rstrip())
        parts.append("\n\n".join(chunks))

    decisions = _entries(root, "decisions.md") if (root / "decisions.md").is_file() else []
    if decisions:
        rows = ["# Decisions", "",
                "Where requests pulled in different directions, these are the decisions and "
                "the date each was taken.", "",
                "| Question | Decision | When |", "|---|---|---|"]
        for d in decisions:
            decision = " ".join(d.body.strip().split("\n\n")[0].split()).replace("|", "\\|")
            when = d.decided + (f", confirmed {d.confirmed}" if d.confirmed else "")
            if d.status:
                when += f", {d.status}"
            rows.append(f"| {d.heading.replace('|', chr(92) + '|')} | {decision} | {when} |")
        parts.append("\n".join(rows))

    maintaining = _people_file(root, "maintaining.md", view_dir)
    if maintaining:
        parts.append(maintaining)
    return "\n\n".join(parts) + "\n"


def build(root) -> Path:
    """Write the view beside the voice directory and return its path."""
    root = Path(root)
    target = view_path(root)
    target.write_text(render_view(root), encoding="utf-8")
    return target


def is_fresh(root) -> bool:
    target = view_path(root)
    return target.is_file() and target.read_text(encoding="utf-8") == render_view(root)


# Showing a rule with everything that points at it.

def show(root, name: str) -> str:
    """A rule, or every rule of one file, with what refines it, its evidence
    and its decisions. Raises KeyError for a name that is neither."""
    root = Path(root)
    manifest = v.load(root)
    rules: list[Rule] = []
    ignored: list[Finding] = []
    for rel in manifest.order:
        rules += _rules_in(root, rel, len(rules), ignored)
    evidence: list[Entry] = []
    if (root / "evidence").is_dir():
        for p in sorted((root / "evidence").rglob("*.md")):
            evidence += _entries(root, p.relative_to(root).as_posix())
    decisions = _entries(root, "decisions.md") if (root / "decisions.md").is_file() else []

    chosen = [r for r in rules if r.id == name] or [r for r in rules if Path(r.path).stem == name]
    if not chosen:
        raise KeyError(name)
    lines: list[str] = []
    for r in chosen:
        lines.append(f"{r.id} ({r.path}:{r.line})")
        lines.append(f"  {r.text}")
        for target in r.refines:
            lines.append(f"  refines {target}")
        for other in rules:
            if r.id in other.refines:
                lines.append(f"  refined by {other.id} ({other.path}:{other.line}): {_short(other.text)}")
        for e in evidence:
            if r.id in (e.rules or ()):
                lines.append(f"  evidence \"{e.heading}\" ({e.path}:{e.line})")
                lines += [f"    {line}" for line in e.body.strip().splitlines()]
        for d in decisions:
            if r.id in (d.rules or ()):
                when = d.decided + (f", confirmed {d.confirmed}" if d.confirmed else "")
                lines.append(f"  decision \"{d.heading}\" ({when})")
                lines += [f"    {line}" for line in d.body.strip().splitlines()]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Splitting a single-file voice into a directory.

_LABEL = re.compile(r"^\*\*([^*]+?)\*\*\s*(.*)$")


def _slug(title: str, words: int = 3) -> str:
    parts = re.findall(r"[a-z0-9]+", title.lower())[:words]
    return "-".join(parts) or "section"


def split(file, into, names: dict | None = None) -> Path:
    """Turn a single-file voice into a voice directory whose core is the same
    text, byte for byte.

    One rule file per `##` section and one rule per paragraph or list item. A
    paragraph opening with a bold label ending in punctuation ("**Replies.**")
    becomes a file of its own in the section's folder. The text before the
    first section becomes an untitled opening, scope.md, and everything below
    the core marker goes to about.md with its links rewritten for its new
    place. `names` maps a section title or a label to the file name to use."""
    file, into = Path(file), Path(into)
    names = names or {}
    text = file.read_text(encoding="utf-8")
    core, marker, rest = text.partition(CORE_MARKER)
    lines = core.rstrip("\n").splitlines()
    if not lines or not lines[0].startswith("# "):
        raise v.VoiceError(f"{file} does not open with a title such as \"# Voice: Name\"")
    name = lines[0][2:].strip()
    name = name.split(":", 1)[1].strip() if name.lower().startswith("voice:") else name

    files: dict[str, list[str]] = {}
    counters: dict[str, int] = {}
    current = "scope.md"
    files[current] = []
    section_stem = None

    def mark(rel: str) -> str:
        stem = Path(rel).stem
        counters[stem] = counters.get(stem, 0) + 1
        return f"{{#{stem}.{counters[stem]}}}"

    for line in lines[1:]:
        if line.startswith("## "):
            title = line[3:].strip()
            section_stem = names.get(title) or _slug(title)
            current = f"{section_stem}.md"
            files[current] = [f"# {title}"]
            continue
        label = _LABEL.match(line) if section_stem else None
        if label and label.group(1)[-1:] in ".!?:":
            raw = label.group(1)
            title = raw[:-1] if raw.endswith(".") else raw
            current = f"{section_stem}/{names.get(title) or _slug(title)}.md"
            files[current] = [f"# {title}", "", f"{mark(current)} {label.group(2)}".rstrip()]
            continue
        if not line.strip():
            files[current].append("")
        elif _LIST.match(line):
            prefix = _LIST.match(line).group(0)
            files[current].append(f"{prefix}{mark(current)} {line[len(prefix):]}")
        else:
            files[current].append(f"{mark(current)} {line}")

    if not any(l.strip() for l in files["scope.md"]):
        del files["scope.md"]
    into.mkdir(parents=True, exist_ok=False)
    for rel, body in files.items():
        target = into / "rules" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(body).strip("\n") + "\n", encoding="utf-8")
    order = ", ".join(f'"{rel}"' for rel in files)
    (into / v.MANIFEST).write_text(
        f'format = 1\nname = "{name}"\norder = [{order}]\n', encoding="utf-8")
    if marker and rest.strip():
        about = _rebase(rest.strip(), file.parent, into)
        (into / "about.md").write_text(about + "\n", encoding="utf-8")
    return into
