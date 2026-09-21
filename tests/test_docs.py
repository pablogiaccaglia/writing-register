"""The documentation, checked for the two ways it rots.

A link that points at nothing, and an anchor that points at a heading which was
renamed. Both are invisible to a reader until they follow one, and both are
cheap to catch."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(p for p in ROOT.rglob("*.md")
              if ".venv" not in p.parts and ".git" not in p.parts
              and ".pytest_cache" not in p.parts
              and "vendor" not in p.parts
              # Replay fixtures are other repositories' docs; their links
              # point into those repositories, not this one.
              and "fixtures" not in p.parts)
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


def anchors(text: str) -> set[str]:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to dashes."""
    out = set()
    for line in text.split("\n"):
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            slug = re.sub(r"[^\w\s-]", "", title.lower())
            out.add(re.sub(r"[\s_]+", "-", slug).strip("-"))
    return out


def test_there_are_documents_to_check():
    assert len(DOCS) >= 4


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_relative_link_points_at_something(doc):
    broken = []
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path = target.split("#", 1)[0]
        if not path:
            continue
        if not (doc.parent / path).exists():
            broken.append(target)
    assert not broken, f"{doc.name} links to nothing: {broken}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_anchor_points_at_a_heading_that_exists(doc):
    broken = []
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")) or "#" not in target:
            continue
        path, anchor = target.split("#", 1)
        other = (doc.parent / path) if path else doc
        if not other.exists():
            continue
        if anchor not in anchors(other.read_text(encoding="utf-8")):
            broken.append(target)
    assert not broken, f"{doc.name} links to a heading that does not exist: {broken}"
