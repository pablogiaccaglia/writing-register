"""Text Claude publishes through a tool is rewritten before it goes out (2026-09-25).

The output style shapes how Claude writes, and nothing checked the result on
its way out: on 2026-09-21 a Notion card went out with a garbled sentence
that only the owner caught. wr's hooks covered commit messages, pull request
descriptions and markdown files, never text passed straight to a tool. An
evaluation on ten real Notion payloads rewrote all ten with every mention,
table and embed intact and no fact added, in 12 to 21 s per page update and
33 to 67 s per new page. So Notion pages, mail and Discord messages are
rewritten before the call runs, opt-in through `auto`, and Claude is shown
each change right after the call so it can correct a shifted meaning.

The closing audit of 2026-09-25 found that the first version rewrote each
field whole: an update that appended one line to a teammate's paragraph
rewrote the paragraph, nothing checked that Notion tags and Discord mentions
survived, and the note showed only the first change in each field. These
tests run the real passage protocol and checks, with only the model faked.
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from writing_register import hooks, metrics
from writing_register.spawn import Answer

REPO = Path(__file__).resolve().parent.parent
LONG = ("The alerting subsystem is a pivotal component that seamlessly empowers operators "
        "to stay on top of their weather stations and never miss an outage.")
BETTER = "The alerting subsystem tells operators when a weather station stops responding."
OTHER = ("The logger service is a robust foundation that seamlessly writes one log per "
         "station into the out folder every night.")
OTHER_BETTER = "The logger service writes one log per station into the out folder every night."


class Model:
    """A model that answers each marked passage with `edit` applied to it."""

    def __init__(self, edit=None, delay=0.0):
        self.edit = edit or (lambda body: body.replace(LONG, BETTER).replace(OTHER, OTHER_BETTER))
        self.delay, self.prompts, self.active, self.most = delay, [], 0, 0
        self.lock = threading.Lock()

    def run(self, prompt, **kw):
        with self.lock:
            self.prompts.append(prompt)
            self.active += 1
            self.most = max(self.most, self.active)
        try:
            time.sleep(self.delay)
            token = re.search(r"\[\[(wr-[0-9a-f]+) passage 1\]\]", prompt).group(1)
            found = {}
            for m in re.finditer(rf"\[\[{token} passage (\d+)\]\]\n(.*?)\n\[\[/{token} passage \1\]\]",
                                 prompt, re.S):
                found[int(m.group(1))] = m.group(2)
            reply = "\n".join(f"[[{token} passage {n}]]\n{self.edit(found[n])}\n[[/{token} passage {n}]]"
                              for n in sorted(found))
            return Answer(stdout=reply, command=("claude",), duration_seconds=0.1)
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def configure(auto='["notion", "mail", "discord"]'):
        cfg = tmp_path / "config.toml"
        cfg.write_text(f'voice = "none"\nauto = {auto}\n')
        monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv(hooks.STATE_ENV, str(tmp_path / "state"))
    configure()
    return SimpleNamespace(configure=configure, model=Model())


def _pre(setup, tool, tool_input, use="t1", model=None):
    return hooks.pre_publish({"session_id": "s", "tool_name": tool, "tool_input": tool_input,
                              "tool_use_id": use}, spawn=model or setup.model)


def _post(tool, use="t1"):
    return hooks.post_publish({"session_id": "s", "tool_name": tool, "tool_use_id": use})


def _updated(reply):
    return reply["hookSpecificOutput"]["updatedInput"]


NOTION_CREATE = "mcp__plugin_Notion_notion__notion-create-pages"
NOTION_UPDATE = "mcp__plugin_Notion_notion__notion-update-page"
COMPOSE = "mcp__plugin_apple-mail_apple-mail__compose_email"
REPLY = "mcp__plugin_apple-mail_apple-mail__reply_to_email"
DISCORD = "mcp__discord__discord_send_message"


def test_a_new_notion_page_is_rewritten_and_nothing_else_changes(setup):
    tool_input = {"parent": {"page_id": "p"}, "pages": [{"properties": {"title": "Alerts"},
                                                         "content": f"# Alerts\n\n{LONG}\n"}]}
    new = _updated(_pre(setup, NOTION_CREATE, tool_input))
    assert new["pages"][0]["content"] == f"# Alerts\n\n{BETTER}\n"
    assert new["parent"] == tool_input["parent"] and new["pages"][0]["properties"] == {"title": "Alerts"}
    assert tool_input["pages"][0]["content"].endswith(f"{LONG}\n"), "the original input is not mutated"


def test_an_update_rewrites_only_the_paragraph_claude_added(setup):
    tool_input = {"page_id": "p", "command": "update_content",
                  "content_updates": [{"old_str": LONG, "new_str": f"{LONG}\n\n{OTHER}"}]}
    new = _updated(_pre(setup, NOTION_UPDATE, tool_input))
    assert new["content_updates"][0]["old_str"] == LONG, "old_str must still match the page"
    assert new["content_updates"][0]["new_str"] == f"{LONG}\n\n{OTHER_BETTER}", \
        "the teammate's paragraph is left as it was"


def test_a_small_edit_to_existing_text_is_not_rewritten(setup):
    tool_input = {"page_id": "p", "command": "update_content",
                  "content_updates": [{"old_str": LONG, "new_str": f"{LONG} Updated today."}]}
    assert _pre(setup, NOTION_UPDATE, tool_input) is None
    assert setup.model.prompts == []


def test_tables_html_and_headings_are_never_sent(setup):
    content = (f"# Alerts\n\n<table>\n<tr><td>{LONG}</td></tr>\n</table>\n\n"
               f"| a | b |\n|---|---|\n| {OTHER} | x |\n\n{LONG}\n")
    new = _updated(_pre(setup, NOTION_CREATE, {"pages": [{"content": content}]}))
    assert new["pages"][0]["content"] == content.replace(f"\n\n{LONG}\n", f"\n\n{BETTER}\n")
    assert f"<td>{LONG}</td>" in new["pages"][0]["content"]


@pytest.mark.parametrize("markup", ['<mention-user url="user://abc"/>', '<mention-page url="https://x"/>',
                                    '{color="red"}', "<@998877665544>", "<#112233>"])
def test_a_rewrite_that_drops_notion_or_discord_markup_is_refused(setup, markup):
    text = f"{LONG} Ask {markup} before the next deploy of the station firmware."
    model = Model(edit=lambda body: BETTER)
    reply = _pre(setup, NOTION_CREATE, {"pages": [{"content": text}]}, model=model)
    assert "hookSpecificOutput" not in (reply or {}), "the text goes out as Claude wrote it"
    assert "kept" in reply["systemMessage"]


def test_mail_and_discord_bodies_are_rewritten(setup):
    assert _updated(_pre(setup, COMPOSE, {"to": "a@example.com", "subject": "Alerts",
                                          "body": LONG}))["body"] == BETTER
    assert _updated(_pre(setup, REPLY, {"message_id": "m", "reply_body": LONG}))["reply_body"] == BETTER
    assert _updated(_pre(setup, DISCORD, {"channel_id": "c", "content": LONG}))["content"] == BETTER


def test_an_email_with_an_html_body_is_left_whole_and_claude_is_told(setup):
    for tool, field in ((COMPOSE, "body"), (REPLY, "reply_body")):
        reply = _pre(setup, tool, {"to": "a@example.com", field: LONG, "body_html": f"<p>{LONG}</p>"})
        assert "hookSpecificOutput" not in reply
        assert "HTML" in reply["systemMessage"]
    assert setup.model.prompts == []


def test_a_discord_message_is_not_pushed_over_the_length_limit(setup):
    text = LONG + " " + "word " * 360
    model = Model(edit=lambda body: body + " " + "more " * 200)
    reply = _pre(setup, DISCORD, {"channel_id": "c", "content": text.strip()}, model=model)
    assert len(text) <= 2000
    assert "hookSpecificOutput" not in reply and "2000" in reply["systemMessage"]


def test_only_the_two_discord_send_tools_match(setup):
    assert _pre(setup, "mcp__discord__discord_send_message_scheduled", {"content": LONG}) is None
    assert _pre(setup, "mcp__discord__discord_send_dm", {"content": LONG}) is not None


def test_leading_blank_lines_survive(setup):
    new = _updated(_pre(setup, NOTION_UPDATE, {"page_id": "p", "command": "insert_content",
                                               "content": f"\n\n{LONG}\n"}))
    assert new["content"] == f"\n\n{BETTER}\n"


def test_nothing_happens_unless_auto_names_the_destination(setup):
    setup.configure('["notion"]')
    assert _pre(setup, DISCORD, {"channel_id": "c", "content": LONG}) is None
    setup.configure('["markdown"]')
    assert _pre(setup, NOTION_CREATE, {"pages": [{"content": LONG}]}) is None
    assert setup.model.prompts == []


def test_a_refused_rewrite_sends_the_text_as_claude_wrote_it(setup):
    model = Model(edit=lambda body: "")
    reply = _pre(setup, NOTION_CREATE, {"pages": [{"content": LONG}]}, model=model)
    assert "hookSpecificOutput" not in (reply or {})
    assert "kept" in reply["systemMessage"]


def test_short_text_is_left_alone(setup):
    assert _pre(setup, DISCORD, {"channel_id": "c", "content": "Done, thanks."}) is None
    assert setup.model.prompts == []


def test_claude_sees_every_changed_passage_right_after_the_call(setup):
    content = "\n\n".join([LONG, "A plain paragraph that nobody needs to change at all today."] * 3 + [OTHER])
    _pre(setup, NOTION_CREATE, {"pages": [{"content": content}]})
    reply = _post(NOTION_CREATE)
    text = reply["hookSpecificOutput"]["additionalContext"]
    assert reply["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    pairs = [line for line in text.splitlines() if line.startswith('  "')]
    assert len(pairs) == 4, "one pair per changed passage, the last one included"
    assert "stops responding" in text and "every night" in text
    assert _post(NOTION_CREATE) is None, "told once"


def test_the_note_goes_to_its_own_call(setup):
    _pre(setup, NOTION_CREATE, {"pages": [{"content": LONG}]}, use="a")
    _pre(setup, DISCORD, {"channel_id": "c", "content": OTHER}, use="b")
    b = _post(DISCORD, use="b")["hookSpecificOutput"]["additionalContext"]
    assert "every night" in b and "stops responding" not in b
    a = _post(NOTION_CREATE, use="a")["hookSpecificOutput"]["additionalContext"]
    assert "stops responding" in a and "every night" not in a


def test_a_long_note_keeps_whole_pairs_and_says_where_the_rest_is(setup):
    paragraphs = [LONG.replace("outage", f"outage number {i}") for i in range(60)]
    model = Model(edit=lambda body: "Rewritten: " + body.replace("pivotal", "central"))
    _pre(setup, NOTION_CREATE, {"pages": [{"content": "\n\n".join(paragraphs)}]}, model=model)
    text = _post(NOTION_CREATE)["hookSpecificOutput"]["additionalContext"]
    assert len(text) <= hooks.PART_LIMIT
    pairs = [line for line in text.splitlines() if line.startswith('  "')]
    assert pairs and all(json.loads(line.split(" -> ")[1]) for line in pairs), "every pair is whole"
    full = re.search(r"is in (\S+\.txt)", text).group(1)
    assert sum(line.startswith('  "') for line in Path(full).read_text().splitlines()) == 60


def test_the_model_calls_are_capped(setup):
    model = Model(delay=0.2)
    updates = [{"old_str": f"x{i}", "new_str": f"x{i}\n\n{LONG}"} for i in range(10)]
    _pre(setup, NOTION_UPDATE, {"page_id": "p", "command": "update_content",
                                "content_updates": updates}, model=model)
    assert len(model.prompts) == 10 and model.most <= hooks._PUBLISH_WORKERS


def test_the_report_names_what_was_published(setup, monkeypatch):
    _pre(setup, DISCORD, {"channel_id": "c", "content": LONG})
    _pre(setup, NOTION_CREATE, {"pages": [{"content": LONG}]}, model=Model(edit=lambda b: ""))
    text = metrics.report()
    assert "commit messages" not in text
    assert "2 texts sent to Notion, mail or Discord" in text
    assert "kept beside their file" not in text and "went out as written" in text


def test_the_plugin_registers_both_hooks_for_these_tools():
    registered = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    for event, name in (("PreToolUse", "pre-publish"), ("PostToolUse", "post-publish")):
        groups = [g for g in registered[event] if any(f"hook {name}" in h["command"] for h in g["hooks"])]
        assert len(groups) == 1, event
        matcher = re.compile(f"^(?:{groups[0]['matcher']})$")
        for tool in (NOTION_CREATE, NOTION_UPDATE, COMPOSE, REPLY, DISCORD,
                     "mcp__discord__discord_send_dm", "mcp__claude_ai_Notion__notion-update-page"):
            assert matcher.match(tool), (event, tool)
        for tool in ("mcp__plugin_Notion_notion__notion-fetch", "Bash", "mcp__discord__discord_read_messages"):
            assert not matcher.match(tool), (event, tool)


def test_every_hook_finds_wr_in_local_bin_when_it_is_not_on_path(tmp_path):
    """The publish hooks escaped the quotes of their fallback path, so without
    `wr` on PATH they exited without a word (audit 2026-09-25)."""
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    log = tmp_path / "ran"
    fake = bin_dir / "wr"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncat > /dev/null\n')
    fake.chmod(0o755)
    registered = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for groups in registered.values() for g in groups for h in g["hooks"]]
    payload = json.dumps({"tool_input": {"command": "git commit -m x", "file_path": "a.md"}})
    for command in commands:
        log.unlink(missing_ok=True)
        subprocess.run(["/bin/sh", "-c", command], input=payload, text=True, timeout=10,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
        assert log.exists() and "hook" in log.read_text(), command


def test_the_configuration_accepts_the_new_destinations(tmp_path, monkeypatch):
    from writing_register import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('auto = ["notion", "mail", "discord"]\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    assert set(config.load_config().auto) == {"notion", "mail", "discord"}
