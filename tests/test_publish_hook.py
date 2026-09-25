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
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from writing_register import hooks

REPO = Path(__file__).resolve().parent.parent
LONG = ("The alerting subsystem is a pivotal component that seamlessly empowers operators "
        "to stay on top of their weather stations and never miss an outage.")
BETTER = "The alerting subsystem tells operators when a weather station stops responding."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def configure(auto='["notion", "mail", "discord"]'):
        cfg = tmp_path / "config.toml"
        cfg.write_text(f'voice = "none"\nauto = {auto}\n')
        monkeypatch.setenv("WR_CONFIG", str(cfg))
    monkeypatch.setenv(hooks.STATE_ENV, str(tmp_path / "state"))
    seen = []

    def fake(text, **kw):
        seen.append(text)
        if "REFUSE" in text:
            return SimpleNamespace(refused="a link was altered", changed=False, text=text, seconds=1.0)
        return SimpleNamespace(refused="", changed=True, text=text.replace(LONG, BETTER), seconds=2.0)
    monkeypatch.setattr(hooks, "humanize_text", fake)
    configure()
    return SimpleNamespace(configure=configure, seen=seen)


def _pre(tool, tool_input):
    return hooks.pre_publish({"session_id": "s", "tool_name": tool, "tool_input": tool_input})


def _updated(reply):
    return reply["hookSpecificOutput"]["updatedInput"]


NOTION_CREATE = "mcp__plugin_Notion_notion__notion-create-pages"
NOTION_UPDATE = "mcp__plugin_Notion_notion__notion-update-page"


def test_a_new_notion_page_is_rewritten_and_nothing_else_changes(setup):
    tool_input = {"parent": {"page_id": "p"}, "pages": [{"properties": {"title": "Alerts"},
                                                         "content": f"# Alerts\n\n{LONG}\n"}]}
    reply = _pre(NOTION_CREATE, tool_input)
    new = _updated(reply)
    assert new["pages"][0]["content"] == f"# Alerts\n\n{BETTER}\n"
    assert new["parent"] == tool_input["parent"] and new["pages"][0]["properties"] == {"title": "Alerts"}
    assert tool_input["pages"][0]["content"].endswith(f"{LONG}\n"), "the original input is not mutated"


def test_a_notion_update_rewrites_the_new_text_and_never_the_text_it_replaces(setup):
    tool_input = {"page_id": "p", "command": "update_content",
                  "content_updates": [{"old_str": LONG, "new_str": f"{LONG} Updated."}]}
    new = _updated(_pre(NOTION_UPDATE, tool_input))
    assert new["content_updates"][0]["old_str"] == LONG, "old_str must still match the page"
    assert new["content_updates"][0]["new_str"] == f"{BETTER} Updated."


def test_mail_and_discord_bodies_are_rewritten(setup):
    assert _updated(_pre("mcp__plugin_apple-mail_apple-mail__compose_email",
                         {"to": "a@example.com", "subject": "Alerts", "body": LONG}))["body"] == BETTER
    assert _updated(_pre("mcp__plugin_apple-mail_apple-mail__reply_to_email",
                         {"message_id": "m", "reply_body": LONG}))["reply_body"] == BETTER
    assert _updated(_pre("mcp__discord__discord_send_message",
                         {"channel_id": "c", "content": LONG}))["content"] == BETTER


def test_nothing_happens_unless_auto_names_the_destination(setup):
    setup.configure('["notion"]')
    assert _pre("mcp__discord__discord_send_message", {"channel_id": "c", "content": LONG}) is None
    setup.configure('["markdown"]')
    assert _pre(NOTION_CREATE, {"pages": [{"content": LONG}]}) is None
    assert setup.seen == []


def test_a_refused_rewrite_sends_the_text_as_claude_wrote_it(setup):
    reply = _pre(NOTION_CREATE, {"pages": [{"content": f"{LONG} REFUSE"}]})
    assert "hookSpecificOutput" not in (reply or {})
    assert "kept" in reply["systemMessage"]


def test_short_text_is_left_alone(setup):
    assert _pre("mcp__discord__discord_send_message", {"channel_id": "c", "content": "Done, thanks."}) is None
    assert setup.seen == []


def test_claude_sees_each_change_right_after_the_call(setup):
    _pre(NOTION_CREATE, {"pages": [{"content": LONG}]})
    reply = hooks.post_publish({"session_id": "s", "tool_name": NOTION_CREATE})
    text = reply["hookSpecificOutput"]["additionalContext"]
    assert reply["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "old -> new" in text and "pivotal" in text and "stops responding" in text
    assert hooks.post_publish({"session_id": "s", "tool_name": NOTION_CREATE}) is None, "told once"


def test_the_plugin_registers_both_hooks_for_these_tools():
    import re
    registered = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    for event, name in (("PreToolUse", "pre-publish"), ("PostToolUse", "post-publish")):
        groups = [g for g in registered[event] if any(f"hook {name}" in h["command"] for h in g["hooks"])]
        assert len(groups) == 1, event
        matcher = re.compile(f"^(?:{groups[0]['matcher']})$")
        for tool in (NOTION_CREATE, NOTION_UPDATE, "mcp__plugin_apple-mail_apple-mail__compose_email",
                     "mcp__plugin_apple-mail_apple-mail__reply_to_email", "mcp__discord__discord_send_message",
                     "mcp__discord__discord_send_dm", "mcp__claude_ai_Notion__notion-update-page"):
            assert matcher.match(tool), (event, tool)
        for tool in ("mcp__plugin_Notion_notion__notion-fetch", "Bash", "mcp__discord__discord_read_messages"):
            assert not matcher.match(tool), (event, tool)


def test_the_configuration_accepts_the_new_destinations(tmp_path, monkeypatch):
    from writing_register import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('auto = ["notion", "mail", "discord"]\n')
    monkeypatch.setenv("WR_CONFIG", str(cfg))
    assert set(config.load_config().auto) == {"notion", "mail", "discord"}
