"""The humanizer is vendored whole and used unchanged.

On 2026-09-14 github.com/blader/humanizer was cloned and integrated in full."""
import json
import re
from pathlib import Path

from writing_register.humanize import SKILL, build_prompt, load_skill

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "humanizer"


def test_the_whole_upstream_repository_is_there():
    for rel in ("SKILL.md", "README.md", "LICENSE", "AGENTS.md",
                ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
                "agents/openai.yaml", "scripts/validate-package.py"):
        assert (VENDOR / rel).is_file(), rel


def test_the_mit_license_is_intact():
    text = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text and "Permission is hereby granted" in text


def test_the_upstream_commit_and_version_are_recorded_and_agree():
    upstream = (ROOT / "vendor" / "UPSTREAM.md").read_text(encoding="utf-8")
    assert re.search(r"commit [0-9a-f]{40}", upstream)
    skill_version = re.search(r'version: "([\d.]+)"', load_skill()).group(1)
    manifest = json.loads((VENDOR / ".claude-plugin" / "plugin.json").read_text())
    assert skill_version == manifest["version"]
    assert f"version {skill_version}" in upstream


def test_the_prompt_carries_the_skill_byte_for_byte():
    assert SKILL == VENDOR / "SKILL.md"
    assert load_skill() in build_prompt("doc\n", load_skill())
