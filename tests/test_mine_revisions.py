"""Commits to the followed documents, in scripts/mine_revisions.py, and how
episodes are paired with them.

The repositories are synthetic, built with git in a temporary directory with
fixed commit dates. The content is an invented weather-station project."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cp = _load("claude_prose", SCRIPTS / "claude_prose.py")
me = _load("mine_episodes", SCRIPTS / "mine_episodes.py")
mr = _load("mine_revisions", SCRIPTS / "mine_revisions.py")

RAIN_V1 = "# Rain\n\nThe gauge tips every 0.2 mm.\n\nIt is emptied weekly.\n"
RAIN_V2 = "# Rain\n\nEach tip of the bucket is 0.2 mm of rain, and a tip is logged with its minute.\n\nIt is emptied weekly.\n"


def _git(repo, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Station", GIT_AUTHOR_EMAIL="station@example.test",
               GIT_COMMITTER_NAME="Station", GIT_COMMITTER_EMAIL="station@example.test")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def _commit(repo, files, subject, date):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject, date=date)


def _repo(tmp_path, name="station"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def test_a_commit_record_has_the_removed_and_added_paragraphs(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"docs/rain.md": RAIN_V1, "logger.py": "rate = 60\n"}, "Add the rain page",
            "2026-08-01T08:00:00+00:00")
    _commit(repo, {"docs/rain.md": RAIN_V2, "logger.py": "rate = 30\n"}, "Say what one tip measures",
            "2026-08-01T09:06:00+00:00")
    recs = list(mr.commits(repo, ["README.md", "docs/*.md"], since="2026-08-01", until="2026-08-01"))
    assert [r["subject"] for r in recs] == ["Add the rain page", "Say what one tip measures"]
    last = recs[-1]
    assert last["kind"] == "commit" and last["path"] == "docs/rain.md"
    assert last["abs_path"] == str(repo / "docs/rain.md")
    assert last["ts"] == "2026-08-01T09:06:00Z"
    assert last["removed"] == ["The gauge tips every 0.2 mm."]
    assert last["added"] == ["Each tip of the bucket is 0.2 mm of rain, and a tip is logged with its minute."]
    assert len(last["commit"]) == 40
    # logger.py is not a followed document
    assert all(r["path"] == "docs/rain.md" for r in recs)


def test_the_final_document_is_read_at_head(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"docs/rain.md": RAIN_V1}, "Add", "2026-08-01T08:00:00+00:00")
    _commit(repo, {"docs/rain.md": RAIN_V2}, "Change", "2026-08-01T09:00:00+00:00")
    (final,) = list(mr.finals(repo, ["docs/*.md"]))
    assert final["kind"] == "final" and final["path"] == "docs/rain.md"
    assert final["paragraphs"][1].startswith("Each tip of the bucket")


def test_html_paragraphs_are_the_text_of_block_elements():
    html = ("<html><head><style>p{color:red}</style></head><body><h1>Rain</h1>"
            "<p>The gauge <b>tips</b> every 0.2 mm.</p><script>var x=1;</script><li>Emptied weekly</li></body></html>")
    assert mr.paragraphs(html, "page.html") == ["Rain", "The gauge tips every 0.2 mm.", "Emptied weekly"]


def test_the_config_names_repositories_and_their_documents(tmp_path):
    cfg = tmp_path / "mine.toml"
    cfg.write_text('[window]\nsince = "2026-07-01"\nuntil = "2026-09-21"\n\n'
                   '[[repositories]]\npath = "~/station"\ndocs = ["README.md", "docs/*.md"]\n')
    conf = mr.load_config(cfg)
    assert conf["repositories"] == [{"path": str(Path.home() / "station"), "docs": ["README.md", "docs/*.md"]}]
    assert (conf["since"], conf["until"]) == ("2026-07-01", "2026-09-21")


# ---------- pairing episodes with commits ----------

def _user(uuid, text, minute, parent=None):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": "s-1",
            "timestamp": f"2026-08-01T09:{minute:02d}:00.000Z", "promptSource": "typed",
            "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}


def _edit_row(uuid, path, old, new, minute):
    return {"type": "assistant", "uuid": uuid, "parentUuid": None, "sessionId": "s-1",
            "timestamp": f"2026-08-01T09:{minute:02d}:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t-{uuid}", "name": "Edit",
                 "input": {"file_path": path, "old_string": old, "new_string": new}}]}}


def test_an_overlapping_commit_is_kept_and_a_concurrent_one_is_marked(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, {"docs/rain.md": RAIN_V1}, "Add the rain page", "2026-08-01T08:00:00+00:00")
    _commit(repo, {"docs/rain.md": RAIN_V2}, "Say what one tip measures", "2026-08-01T09:06:00+00:00")
    # after the next input plus the half hour of slack: nobody's episode
    _commit(repo, {"docs/rain.md": RAIN_V2 + "\nThe funnel is heated in winter.\n"}, "Heated funnel",
            "2026-08-01T10:05:00+00:00")
    other = _repo(tmp_path, "other")
    _commit(other, {"docs/wind.md": "# Wind\n\nGusts are logged.\n"}, "Add wind", "2026-08-01T08:00:00+00:00")
    # another session committed here while this one worked
    _commit(other, {"docs/wind.md": "# Wind\n\nGusts are logged every three seconds.\n"}, "Log gust interval",
            "2026-08-01T09:07:00+00:00")
    rows = [
        _user("u-1", "the tipping sentence is unclear", 3),
        _edit_row("a-1", str(repo / "docs/rain.md"), "The gauge tips every 0.2 mm.",
                  "Each tip of the bucket is 0.2 mm of rain, and a tip is logged with its minute.", 4),
        _user("u-2", "good, and the snow page?", 30),
    ]
    folder = tmp_path / "-home-weather-station"
    folder.mkdir()
    transcript = folder / "s-1.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows))
    revisions = []
    for r in (repo, other):
        revisions += list(mr.commits(r, ["docs/*.md"], since="2026-08-01", until="2026-08-01"))
        revisions += list(mr.finals(r, ["docs/*.md"]))
    records = list(cp.extract([transcript], kinds={"human", "reply", "edit", "publish"}))
    episodes = me.build_episodes(records, parents=me.parent_map([transcript]), revisions=revisions)
    ep = episodes[0]
    status = {c["subject"]: c["status"] for c in ep["response"]["commits"]}
    assert status == {"Say what one tip measures": "confirmed", "Log gust interval": "concurrent_unconfirmed"}
    assert {"label": "Say what one tip measures", "source": "commit"} in ep["fix_labels"]
    assert {"label": "Log gust interval", "source": "commit"} not in ep["fix_labels"]
    assert all(c["subject"] != "Heated funnel" for e in episodes for c in e["response"]["commits"])
    qualities = [p["pair_quality"] for p in ep["pairs"]]
    assert qualities == ["exact", "commit", "final"]
    final = ep["pairs"][2]
    assert final["version"] == "final" and final["after"].startswith("Each tip of the bucket")


def test_overlap_is_six_shared_words_or_a_whole_short_paragraph():
    assert mr.overlaps(["Each tip of the bucket is 0.2 mm of rain."],
                       ["x = 1\nEach tip of the bucket is 0.2 mm of rain.\n"])
    assert not mr.overlaps(["Each tip of the bucket is 0.2 mm of rain."], ["The wind is logged."])
    # a short paragraph overlaps when the edit contains it whole
    assert mr.overlaps(["Emptied weekly."], ["It is now: Emptied weekly."])
