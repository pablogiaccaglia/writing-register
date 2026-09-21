"""The scrub gate: nothing confidential reaches the public repository.

scripts/scrub_check.py scans a tree in three layers. The generic detectors
name nothing and always run: identifiers, addresses, home paths, token shapes
and a file policy. The keyed digests hold confidential terms as HMACs, so the
public repository can carry the list without revealing it. The plaintext mode
reads the terms themselves, for the owner's own pre-push hook.

Every term here is invented, and the strings that look like identifiers,
addresses or tokens are assembled at run time, so this file passes the scanner
it tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
SCRIPT = HERE / "scripts" / "scrub_check.py"
sys.path.insert(0, str(HERE / "scripts"))

import scrub_check  # noqa: E402

KEY = "a key only these tests use"
AT = "@"
DOT = "."


def _run(*args, env=None, cwd=None):
    base = {k: v for k, v in os.environ.items()
            if k not in ("WR_SCRUB_KEY", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH")}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env={**base, **(env or {})}, cwd=cwd)


def _tree(tmp_path, files):
    root = tmp_path / "tree"
    root.mkdir()
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _kinds(result):
    return sorted({line.split(":")[2] for line in result.stdout.splitlines() if line.count(":") >= 2})


# The generic layer


def test_a_clean_tree_exits_zero(tmp_path):
    root = _tree(tmp_path, {"README.md": "# stationlog\n\nPolls each station every ten minutes.\n",
                            "stationlog/collect.py": "def poll(station):\n    return station.read()\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == ""


def test_a_uuid_is_found_and_its_text_is_not_printed(tmp_path):
    uuid = "-".join(["3f2a9c1e", "8b4d", "4e7a", "9c21", "5d6e7f8a9b0c"])
    root = _tree(tmp_path, {"docs/setup.md": f"The tenant is\n{uuid}\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 1
    assert "docs/setup.md:2:uuid" in r.stdout
    assert uuid not in r.stdout


@pytest.mark.parametrize("address, found", [
    (DOT.join(["10", "20", "30", "40"]), True),
    (DOT.join(["172", "16", "4", "9"]), True),
    (DOT.join(["192", "0", "2", "15"]), False),        # documentation range
    (DOT.join(["198", "51", "100", "7"]), False),
    (DOT.join(["203", "0", "113", "250"]), False),
    (DOT.join(["127", "0", "0", "1"]), False),         # loopback
    (":".join(["2a01", "4f8", "c0c", "1234", "", "1"]), True),
    (":".join(["2001", "db8", "85a3", "", "8a2e", "370", "7334"]), False),   # documentation range
    ("::" + "1", False),
])
def test_addresses_outside_documentation_ranges_are_found(tmp_path, address, found):
    root = _tree(tmp_path, {"docs/net.md": f"The box answers on {address} today.\n"})
    r = _run("scan", "--tree", str(root))
    assert (r.returncode == 1) == found, r.stdout
    if found:
        assert _kinds(r) in (["ipv4"], ["ipv6"])


def test_a_placeholder_uuid_is_not_an_identifier(tmp_path):
    placeholder = "-".join(["11111111", "2222", "3333", "4444", "555555555555"])
    nil = "-".join(["00000000", "0000", "0000", "0000", "000000000000"])
    root = _tree(tmp_path, {"tests/t.py": f"THREAD = '{placeholder}'\nNIL = '{nil}'\n"})
    assert _run("scan", "--tree", str(root)).returncode == 0


def test_a_version_or_a_time_is_not_an_address(tmp_path):
    root = _tree(tmp_path, {"docs/notes.md": "Needs version 1.2.3 and runs at 10:30:00, x[::2] works.\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("email, found", [
    ("station.keeper" + AT + "weatherco.io", True),
    ("ops" + AT + "example.com", False),
    ("ops" + AT + "mail.example.org", False),
    ("12345+someone" + AT + "users.noreply.github.com", False),
    ("noreply" + AT + "anthropic.com", False),
    ("t" + AT + "t", False),
    ("a" + AT + "b.c", False),
    ("skills" + AT + "1.5.20", False),                 # a package at a version
])
def test_emails_outside_the_allowlist_are_found(tmp_path, email, found):
    root = _tree(tmp_path, {"docs/contact.md": f"Write to {email} for access.\n"})
    r = _run("scan", "--tree", str(root))
    assert (r.returncode == 1) == found, r.stdout
    if found:
        assert _kinds(r) == ["email"]


@pytest.mark.parametrize("text, found", [
    ("/" + "Users/keeper/stationlog/out", True),
    ("/" + "home/keeper/.config", True),
    ("~/.claude/" + "plans/notes.md", True),
    ("~/" + "Desktop/stationlog", True),
    ("/" + "home/user/.config", False),
    ("/" + "Users/you/stationlog", False),
    ("~/.config/writing-register", False),
])
def test_home_directory_paths_are_found(tmp_path, text, found):
    root = _tree(tmp_path, {"docs/paths.md": f"Run it from {text} first.\n"})
    r = _run("scan", "--tree", str(root))
    assert (r.returncode == 1) == found, r.stdout
    if found:
        assert _kinds(r) == ["home-path"]


@pytest.mark.parametrize("token", [
    "sk-" + "ant-" + "api03-" + "Zx9" * 10,
    "gh" + "p_" + "A1b2C3d4" * 5,
    "github" + "_pat_" + "11ABCDEFG0" * 4,
    "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "xo" + "xb-" + "1234567890-abcdefghij",
    "xo" + "xp-" + "1234567890-abcdefghij",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
])
def test_token_shapes_are_found(tmp_path, token):
    root = _tree(tmp_path, {"stationlog/config.py": f"TOKEN = '{token}'\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 1 and _kinds(r) == ["token"], r.stdout
    assert token not in r.stdout


def test_a_regular_expression_for_a_token_is_not_a_token(tmp_path):
    root = _tree(tmp_path, {"stationlog/redact.py": 'SHAPE = r"(gh[pousr]_[A-Za-z0-9]{20,})|(github' + '_pat_[A-Za-z0-9_]{20,})|(AKIA[0-9A-Z]{16})"\n'})
    assert _run("scan", "--tree", str(root)).returncode == 0


def test_a_long_base64_run_is_found(tmp_path):
    blob = "QmFzZTY0IGlzIG5vdCBhIHNlY3JldCBidXQg" + "aXQgbWF5IGhvbGQgb25lIGluc2lkZSBpdA" + "=="
    root = _tree(tmp_path, {"docs/blob.md": f"payload: {blob}\n", "docs/short.md": "id: QmFzZTY0IGlzIG5vdA==\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 1 and r.stdout.strip() == "docs/blob.md:1:base64"


def test_text_is_normalised_before_the_scan(tmp_path):
    """A fullwidth address and a zero-width space inside a path still count."""
    fullwidth = DOT.join("".join(chr(0xFF10 + int(d)) for d in part) for part in ("10", "20", "30", "40"))
    hidden = "/" + "Us" + chr(0x200B) + "ers/keeper/notes"
    root = _tree(tmp_path, {"a.md": f"box {fullwidth}\n", "b.md": f"see {hidden}\n"})
    r = _run("scan", "--tree", str(root))
    assert "a.md:1:ipv4" in r.stdout and "b.md:1:home-path" in r.stdout


# The file policy


def test_the_file_policy(tmp_path):
    root = _tree(tmp_path, {
        "LICENSE": "MIT\n", ".gitignore": "out/\n", "stationlog/run.sh": "echo ok\n",
        "notes.txt": "fine\n", "conf.yaml": "a: 1\n", "conf.yml": "a: 1\n", "pyproject.toml": "[x]\n",
        "data.json": "{}\n",
        ".DS_Store": "x", "analysis.ipynb": "{}", "report.html": "<p>", "shot.png": "x",
        "log.jsonl": "{}\n", "Makefile": "all:\n", ".claude/settings.json": "{}\n",
        "sub/.git/HEAD": "ref: refs/heads/main\n",
    })
    (root / "big.md").write_text("word " * 220_000)
    os.symlink(root / "notes.txt", root / "link.md")
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 1
    found = {tuple(line.split(":")[:3:2]) for line in r.stdout.splitlines()}
    assert found == {(".DS_Store", "file-type"), ("analysis.ipynb", "file-type"), ("report.html", "file-type"),
                     ("shot.png", "file-type"), ("log.jsonl", "file-type"), ("Makefile", "file-type"),
                     (".claude/settings.json", "claude-dir"), ("sub/.git", "nested-git"),
                     ("big.md", "file-size"), ("link.md", "symlink")}, r.stdout


def test_a_git_repository_is_scanned_by_what_it_would_publish(tmp_path):
    """Files git ignores never reach the public repository, so they are not scanned."""
    root = _tree(tmp_path, {".gitignore": "out/\n.venv/\n", "README.md": "# stationlog\n",
                            "out/readings.txt": "station at " + DOT.join(["10", "1", "1", "9"]) + "\n",
                            ".venv/x.pyc": "x"})
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 0, r.stdout


def test_exclude_skips_a_prefix(tmp_path):
    root = _tree(tmp_path, {"private/notes.md": "box " + DOT.join(["10", "0", "0", "8"]) + "\n",
                            "docs/a.md": "fine\n"})
    assert _run("scan", "--tree", str(root)).returncode == 1
    assert _run("scan", "--tree", str(root), "--exclude", "private/").returncode == 0


def test_the_allow_list_is_scoped_to_a_path_and_a_kind(tmp_path):
    address = DOT.join(["10", "0", "0", "8"])
    root = _tree(tmp_path, {"docs/lab.md": f"lab box {address}\n", "docs/other.md": f"box {address}\n",
                            "scrub/allow.txt": "# known good\ndocs/lab.md\tipv4\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 1 and r.stdout.strip() == "docs/other.md:1:ipv4"


# The keyed digests


def _digests(tmp_path, terms):
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text(terms)
    key_file = tmp_path / "key"
    key_file.write_text(KEY + "\n")
    r = _run("digest", "--terms", str(terms_file), "--key-file", str(key_file))
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_a_digest_line_holds_no_term(tmp_path):
    out = _digests(tmp_path, "# invented\nZorblax\nQuintessa  Labs\nvelmora*\n")
    assert "zorblax" not in out.lower() and "quintessa" not in out.lower() and "velmora" not in out.lower()
    rows = [line.split("\t") for line in out.splitlines() if line and not line.startswith("#")]
    assert all(len(r) == 3 and len(r[0]) == 64 for r in rows)
    assert sorted((r[1], r[2]) for r in rows) == [("1", "-"), ("1", "-"), ("1", "w"), ("2", "-")]


def test_the_digest_normalises_case_width_and_spacing():
    key = KEY.encode()
    assert scrub_check.term_digest(key, "Quintessa  Labs") == scrub_check.term_digest(key, "quintessa labs")
    assert scrub_check.term_digest(key, "ＺＯＲＢＬＡＸ") == scrub_check.term_digest(key, "zorblax")
    assert scrub_check.term_digest(key, "quintessa-labs") == scrub_check.term_digest(key, "Quintessa Labs")


def _digest_tree(tmp_path, files):
    root = _tree(tmp_path, files)
    (root / "scrub").mkdir(exist_ok=True)
    (root / "scrub" / "digests.txt").write_text(_digests(tmp_path, "Zorblax\nQuintessa Labs\nvelmora*\n"))
    return root


def test_the_digest_layer_finds_terms_and_never_prints_them(tmp_path):
    root = _digest_tree(tmp_path, {
        "docs/a.md": "We ship to ZORBLAX weekly.\n",
        "docs/b.md": "Built with\nquintessa_labs tooling.\n",
        "docs/c.md": "The megavelmoracloud bucket.\n",
        "docs/d.md": "zorblaxian is a longer word and not the term.\n",
        "docs/zorblax-notes.md": "nothing here\n",
    })
    r = _run("scan", "--tree", str(root), env={"WR_SCRUB_KEY": KEY})
    assert r.returncode == 1
    lines = r.stdout.splitlines()
    assert {tuple(line.split(":")[:3]) for line in lines} == {
        ("docs/a.md", "1", "digest"), ("docs/b.md", "2", "digest"),
        ("docs/c.md", "1", "digest"), ("docs/*-notes.md", "0", "digest")}, r.stdout
    for word in ("zorblax", "quintessa", "velmora"):
        assert word not in r.stdout.lower() and word not in r.stderr.lower()
    assert all(len(line.split(":")[3]) == 12 for line in lines)


def test_a_digest_hit_can_be_allowed_for_one_path(tmp_path):
    root = _digest_tree(tmp_path, {"docs/a.md": "Zorblax\n", "docs/b.md": "Zorblax\n"})
    first = _run("scan", "--tree", str(root), env={"WR_SCRUB_KEY": KEY})
    digest_id = first.stdout.splitlines()[0].split(":")[3]
    (root / "scrub" / "allow.txt").write_text(f"docs/a.md\t{digest_id}\n")
    r = _run("scan", "--tree", str(root), env={"WR_SCRUB_KEY": KEY})
    assert r.stdout.strip() == f"docs/b.md:1:digest:{digest_id}"


def test_a_commit_message_is_scanned(tmp_path):
    root = _digest_tree(tmp_path, {"docs/a.md": "fine\n"})
    message = tmp_path / "msg.txt"
    message.write_text("Speed up the push\n\nAsked for by Quintessa Labs.\n")
    r = _run("scan", "--tree", str(root), "--message", str(message), env={"WR_SCRUB_KEY": KEY})
    assert r.returncode == 1 and r.stdout.startswith("<message>:3:digest:")


def test_the_key_can_come_from_a_file(tmp_path):
    root = _digest_tree(tmp_path, {"docs/a.md": "Zorblax\n"})
    key_file = tmp_path / "key2"
    key_file.write_text(KEY)
    assert _run("scan", "--tree", str(root), "--key-file", str(key_file)).returncode == 1


def test_without_a_key_the_digest_layer_is_skipped(tmp_path):
    root = _digest_tree(tmp_path, {"docs/a.md": "Zorblax\n"})
    r = _run("scan", "--tree", str(root))
    assert r.returncode == 0 and "skipped" in r.stderr


def _event(tmp_path, head_repo):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": head_repo}}}}))
    return str(path)


@pytest.mark.parametrize("env, code", [
    ({"GITHUB_REPOSITORY": "stationlog-dev/writing-register", "GITHUB_EVENT_NAME": "push"}, 1),
    ({"GITHUB_REPOSITORY": "stationlog-dev/writing-register", "GITHUB_EVENT_NAME": "pull_request",
      "GITHUB_EVENT_PATH": "same"}, 1),
    ({"GITHUB_REPOSITORY": "stationlog-dev/writing-register", "GITHUB_EVENT_NAME": "pull_request",
      "GITHUB_EVENT_PATH": "fork"}, 0),
    ({"GITHUB_REPOSITORY": "someone-else/writing-register", "GITHUB_EVENT_NAME": "push"}, 0),
])
def test_the_owner_repository_cannot_skip_the_digest_layer(tmp_path, env, code):
    root = _tree(tmp_path, {"docs/a.md": "fine\n", "scrub/owner.txt": "# the public repository\nstationlog-dev/writing-register\n",
                            "scrub/digests.txt": _digests(tmp_path, "Zorblax\n")})
    env = dict(env)
    if env.get("GITHUB_EVENT_PATH") == "same":
        env["GITHUB_EVENT_PATH"] = _event(tmp_path, "stationlog-dev/writing-register")
    elif env.get("GITHUB_EVENT_PATH") == "fork":
        env["GITHUB_EVENT_PATH"] = _event(tmp_path, "a-contributor/writing-register")
    r = _run("scan", "--tree", str(root), env=env)
    assert r.returncode == code, r.stdout + r.stderr
    if code:
        assert "WR_SCRUB_KEY" in r.stderr


# The plaintext mode


def test_plaintext_terms_are_printed(tmp_path):
    root = _tree(tmp_path, {"docs/a.md": "Deployed for\nQuintessa\nLabs last week.\n",
                            "docs/b.md": "in the megavelmoracloud bucket\n",
                            "docs/c.md": "zor" + chr(0x200B) + "blax\n"})
    terms = tmp_path / "terms.txt"
    terms.write_text("Zorblax\nQuintessa Labs\nvelmora*\n")
    r = _run("scan", "--tree", str(root), "--terms", str(terms))
    assert r.returncode == 1
    assert set(r.stdout.splitlines()) == {"docs/a.md:2:term:quintessa labs", "docs/b.md:1:term:velmora",
                                          "docs/c.md:1:term:zorblax"}


# Usage


def test_usage_errors_exit_two(tmp_path):
    assert _run().returncode == 2
    assert _run("scan").returncode == 2
    assert _run("scan", "--tree", str(tmp_path / "missing")).returncode == 2
    assert _run("digest", "--terms", str(tmp_path / "missing"), "--key-file", str(tmp_path / "nope")).returncode == 2


# This repository


# Everything confidential lives under private/, which the public tree never holds.
REPORT_EXCLUDES = ["private/"]


def test_the_generic_layer_over_this_repository(tmp_path):
    """Reports what the generic layer finds here, without asserting it is clean:
    the pass that generalises the remaining files comes later. Set
    WR_SCRUB_REPORT to keep the report."""
    args = ["scan", "--tree", str(HERE)]
    for prefix in REPORT_EXCLUDES:
        args += ["--exclude", prefix]
    r = _run(*args)
    assert r.returncode in (0, 1), r.stderr
    report = Path(os.environ.get("WR_SCRUB_REPORT") or tmp_path / "scrub-report.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"excluded: {' '.join(REPORT_EXCLUDES)}\nexit: {r.returncode}\n\n{r.stdout}{r.stderr}")
    print(r.stdout)
