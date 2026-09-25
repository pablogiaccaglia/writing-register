# CLAUDE.md

This file guides an agent working in this repository.

## What this repository does

This repository holds a small Python package and a Claude Code plugin. Both rewrite prose so it reads as a person wrote it, and both follow the humanizer skill, a set of instructions that lists the patterns that make writing read as machine-made and says how to fix each one. The repository keeps its copy of the skill in `vendor/humanizer/`. [README.md](README.md) explains what the project is for and how to set it up, and [docs/USAGE.md](docs/USAGE.md) documents the command in full.

`src/writing_register/resources.py` says where the humanizer skill and the shipped voices are: the clone's `vendor/` and `voice/`, or the copy an installed package carries, which `setup.py` puts there at build time. `src/writing_register/patterns.py` builds the card of machine-writing patterns that the hooks and the output style carry, read from the vendored skill so it cannot drift. [docs/STEERING.md](docs/STEERING.md) is the reference for the other half of the project: steering how the model writes before the text exists, what each mechanism in Claude Code can reach, and what was measured about them.

The package installs the `wr` command. Its only rewriting command is `wr humanize`, which rewrites a file in one model call and then checks the rewrite against the repository's code in a second call. When the rewrite comes back, the command first runs the string checks in `src/writing_register/humanize.py` and refuses any rewrite that fails them, for example one that invents a number or changes a code block. These checks are plain string comparisons, so they cost nothing next to the model call. Then the checker in `src/writing_register/verify.py`, a second model call that can read a copy of the repository, judges every sentence the rewrite added or changed. For each sentence the code contradicts or cannot confirm, `src/writing_register/changes.py` puts back the original text around it. A refused rewrite leaves the file untouched and is saved next to it under a name ending in `.refused.md`, so someone can copy its good parts over by hand.

A user can also turn on a voice, a markdown file that says how the result should read. Where the voice and the skill disagree, the voice wins. One example is `voice/technical-colleague/`, which describes how one technical lead wants the text written for their team to read. It is a directory of rule files, evidence and decisions, and `voice/technical-colleague.md` beside it is generated from it by `wr voice build`, so edit the directory and never the generated file. No voice is active by default, so nobody gets someone else's voice without asking for it.

Each user names their voice in their own `~/.config/writing-register/config.toml`, a file that lives on their machine and is never committed to a repository. The same file holds the `auto` setting, which lists the kinds of text the plugin rewrites by itself: commit messages, pull request descriptions, markdown files, and text sent to Notion, mail or Discord. One module, `src/writing_register/config.py`, reads the configuration. It also decides which voice is active and refuses a file with an unknown setting, so a typo cannot turn the voice off without anyone noticing.

The plugin ships two skills, which an agent loads when it invokes them: the humanizer skill, and the plugin's own writing-register skill. What reaches every session and subagent without being asked for is the hooks' card of patterns and the voice. The writing-register skill tells the agent to pick up the user's voice before it starts and to run `wr humanize` on the file once it is finished. The agent picks up the voice by running `wr voice --core`, which prints the part of the voice file the model receives. That part is everything above the line `<!-- wr:end-of-core -->`, or the whole file when that line is missing. Anything below the line holds evidence and examples for the people who maintain the voice.

## How the hooks work

The plugin also ships hooks, which are commands Claude Code runs at fixed points in a session. The plugin's hooks run when the session starts, when a subagent starts, before Claude runs `git commit` or `gh pr`, before and after Claude writes or edits a file, before and after Claude calls a Notion, mail or Discord tool, when Claude's turn ends, and when the user sends a message. They are listed in `hooks/hooks.json`, and each one calls `wr hook <event>`. Every time a hook runs, the hook code in `src/writing_register/hooks.py` reads the `auto` setting from the user's configuration and uses it to decide whether to rewrite a commit message, a pull request description, a markdown file or text Claude sends through one of those tools. At the start of a session, a hook also gives Claude the voice and tells it which kinds of text wr rewrites, and a second hook gives the same voice to every subagent, which the session's own context does not reach. Claude Code shows the model at most about 10,000 characters of one hook's text and replaces anything longer with a 2KB preview, so both of these hooks are registered six times with `--part 1` to `--part 6`, and `split_parts` in `hooks.py` cuts the text at section boundaries; keep any new hook text under that limit or send it in parts. Each automatic rewrite also leaves one line in `src/writing_register/metrics.py`'s record of what it had to change, which `wr report` reads.

A commit message or a pull request description is rewritten before its command runs, so the command runs with the new text. If that rewrite takes longer than 90 seconds, or the checks refuse it, the hook gives up and the command runs as Claude wrote it.

Text Claude sends to Notion, mail or Discord is rewritten before the call runs, by `pre_publish` in `hooks.py`, with the passage protocol of the markdown path (`rewrite_marked` in `passages.py`). Only the prose passages that are new against the text the call replaces are sent; for a Notion update that is `new_str` against its `old_str`. Tables, HTML blocks and headings are never sent, each passage is checked on its own, and a passage whose Notion tags, attribute blocks or Discord mentions changed is refused. The note of what changed waits in the session's state under the call's `tool_use_id`, and `post_publish` hands it to Claude right after that call.

A markdown file is rewritten after Claude's turn ends, because its rewrite can take minutes, and the rewrite covers only the text Claude wrote. Right before each of Claude's edits of the file, a hook saves the file's text. Right after the edit, a hook compares the two versions and records the sentences and list items that edit added (`new_unit_keys` in `src/writing_register/passages.py`). Text Claude did not write is never recorded, so it is never rewritten.

Once the turn ends, the rewrite runs in the background on the recorded text, grouped into passages. A passage is a run of recorded sentences inside one paragraph, or of recorded items inside one list, that holds at least 8 words. The model reads the whole document for context but sends back only the passages, which are put back where they were; each passage is checked on its own, and one failing passage refuses the rewrite of the whole file. This rewrite changes prose only and gets no sources, because the model does not see the repository and Claude wrote those sentences minutes earlier with the repository open. Instead, when the user sends their next message, Claude is given each passage the rewrite changed, old and new, to check that each still says what Claude meant.

The hooks keep state for each session under `~/.cache/writing-register/auto/`, or wherever `$XDG_CACHE_HOME` or `$WR_STATE_DIR` puts it. That state holds a queue of the markdown files the turn touched, the text of a file right before each edit, the record of Claude's sentences and list items not yet rewritten, an edit log of when Claude edited each file, and the notes waiting for Claude's next message. The markdown rewrite runs in the background while Claude may keep editing and other hooks keep running, so two mechanisms keep it from losing anything:

- The rewrite goes into a temporary file, which is swapped with the document in one atomic step (`_write_unless_changed` in `humanize.py`). If the file was edited in the meantime, the swap is undone and the rewrite is refused. The hooks then check the edit log to see whether the edit came from Claude. If it did, the newer version is rewritten once that turn ends; if it did not, the refused rewrite is kept next to the file as usual.
- Each session's state folder has a lock, and a hook reads or changes the queue, the saved text, the record, the edit log and the notes only while it holds that lock (`_locked` in `hooks.py`). A background rewrite that finishes late reloads the record under the lock and removes only what it handled, so two hooks running at once cannot lose anything. The lock is never held during a model call.

## Rules for changing the code

### The vendored humanizer skill

Never edit `vendor/humanizer/`, because the directory is a whole, unchanged copy of the upstream humanizer repository on GitHub (blader/humanizer). The commit it was copied from is recorded in `vendor/UPSTREAM.md`, and a test (`tests/test_vendor.py`) checks that the copy's files, license and version are intact. To change what the rewrite does, change the voice or the instructions that go into the prompt along with the skill. The function that assembles the prompt, `build_prompt` in `humanize.py`, holds those instructions, and `build_passage_prompt` in `passages.py` holds them for the passage rewrite.

To take a newer version of the skill, replace the directory with a fresh clone, record the new commit and version in `vendor/UPSTREAM.md`, and run the tests. [docs/USAGE.md](docs/USAGE.md#updating-the-humanizer) gives the same steps in its last section.

### The model call

Every model call goes through the `claude` command line tool, and only one module, `spawn.py`, may start that process, because the sandbox around the call is built there and nowhere else. The module sends the prompt on stdin, gives the child process an empty MCP configuration and a neutral working directory, and passes it an allowlisted environment. A second call site that forgot one of those flags would run without the sandbox and raise no error, so a test (`tests/test_no_second_spawn.py`) fails when any other file builds a `claude` command. The test only recognizes a command written out as a literal, which means a reviewer still has to watch for a command built from a variable or passed as a shell string.

Besides the rewrite, the checker also calls `Spawn.run`. With `read_root`, the command adds `--safe-mode`, `--tools Read,Grep,Glob`, `--permission-prompts none`, `--add-dir` on a copy of the repository and `--output-format json`, with the checker's instructions as the system prompt and its answer format as a JSON schema. Safe mode keeps any CLAUDE.md, skill, hook or plugin from reaching the checker. The copy is made by `export_tracked` in `spawn.py`: the tracked files at the last commit, the working tree's edited and new files, no ignored files, no files named like secrets and no symlinks. What those flags do was measured with real calls and is kept in `tests/test_spawn_live.py`, which runs only with `WR_LIVE=1` because it costs model calls.

### Hooks

Every hook follows three rules:

- A hook does nothing in a scripted run, which Claude Code marks by setting `CLAUDE_CODE_ENTRYPOINT` to a value starting with `sdk`. Each rewrite that `wr humanize` makes runs as a scripted `claude -p` session, so a hook that acted there would start another rewrite from inside the first one.
- A hook never answers with a permission decision. When it rewrites a command it only changes the command, and Claude Code still applies the user's permission rules to the changed command.
- A hook exits 0 whatever goes wrong, because a failing hook would get in the way of the user's work. The failure still reaches the user, because the function that answers every hook event (`run`) turns an error into a one-line message. The session-start hook reports a configuration mistake the same way.

### The plugin version

When you change the hooks or the skills, raise `version` in `.claude-plugin/plugin.json`. Claude Code installs its own copy of the plugin into a folder under `~/.claude/plugins/cache/` named after the version, and `claude plugin update` installs a change when it finds a new version.

### The checks

Change the checks in `humanize.py` only when a real failure calls for it. [docs/USAGE.md](docs/USAGE.md#what-is-checked) lists every check and the reason for it. Three checks were each loosened after they refused a correct rewrite: the inline-code check, the link check, and the length floor for commit messages and PR descriptions. The length floor is the shortest share of the original a rewrite may keep, which is a quarter for those texts against half for a document. Each loosened check carries a comment naming the case and the date of that refusal. If you add a check or loosen one, start from a failing test built from the real case, and leave the same kind of comment.

The checker is a model and can be wrong, so wr applies rules to its answers before acting on them, for example that a cited line really holds the words the checker quoted. Those rules live in `verify.py` (`_judge` and `_conclude`). Wrong sentences were under 1% of the changed ones in the rewrites measured, so a rule that puts back correct sentences costs more than it catches. On 2026-09-15, three rules were measured that enforced mechanically what the prompt asks of the checker: to cite the guard a line runs under, to treat a sentence saying something happens as a claim about code even when a policy sets it as a rule, and to cite the code of the component a sentence names. They caught no wrong sentence the checker had missed and put back about three times as many correct ones, so they were removed and only their instructions stay in the prompt.

Before changing a rule, measure it on the saved runs with `scripts/rejudge_replay.py` and `scripts/agreement.py`, which need no model call. Changing the checker's prompt, or how `changes.py` pairs original and rewritten sentences, means the saved answers no longer line up, so run fresh replays with `scripts/replay_check.py`.

### Tests

Work test first: write the test, watch it fail for the reason you expect, and only then change the code.

## Writing documentation in this repository

Documentation in this repository is written in the same voice the command applies: explain a thing before naming it, keep a detail only where the reader uses it, and use no em dashes or en dashes. Before committing a finished document, run it through `wr humanize` and read the diff. The string checks catch invented names, numbers and links, and the checker catches most wrong explanations, but only a person reading the diff will notice the rest.

## Commands

The first command runs the test suite. The second rewrites and checks one document and prints the diff without writing anything. The third applies the checker's current rules to saved replay runs.

```bash
.venv/bin/python -m pytest                          # the suite
.venv/bin/wr humanize --dry-run docs/USAGE.md       # rewrite and check, diff printed, nothing written
.venv/bin/python scripts/agreement.py --corpus tests/fixtures/sample RECORD.json ...   # the checker's rules on saved runs
```

The second command makes real model calls, so the `claude` command line tool must be installed and logged in through `claude` itself. A key kept in an environment variable such as `ANTHROPIC_API_KEY` does not reach it, because the command passes `claude` only basic environment variables such as `PATH` and `HOME`.

Judge a test run by its exit code, because in some terminals `pytest -q` omits the summary line.
