# writing-register

writing-register rewrites prose so it reads as a person wrote it. It gives a model the [humanizer](https://github.com/blader/humanizer) skill, a set of instructions that lists the patterns that make text read as machine-written and says how to fix each one. This repository vendors the skill whole, which means it keeps an unchanged copy of the upstream repository. A user can also turn on a voice, which is a file that describes how their own text should read. Wherever the voice differs from the skill, the voice wins. No voice is used until the user turns one on. The repository ships one example voice, `technical-colleague`, and a guide to building your own from the corrections you already make to generated text.

All rewriting goes through one command, `wr humanize`. The command sends the skill, the voice when one is on, and a file to the model in a single call. It then checks the reply with string comparisons and refuses any rewrite that breaks something those comparisons can detect, such as changed code or an invented number.

You can use writing-register from any repository in two ways. The first is the command itself, which you run on a file once it is finished. The second is a Claude Code plugin, which loads the same skill while an agent writes markdown, along with the user's voice if they turned one on. You can also set the plugin to run the rewrite on its own for the commit messages, pull request descriptions and markdown files that Claude writes.

## Contents

- [Setting it up](#setting-it-up)
- [Rewriting a file](#rewriting-a-file)
- [Turning on a voice](#turning-on-a-voice)
- [Automatic rewrites in Claude Code](#automatic-rewrites-in-claude-code)
- [What is in the repository](#what-is-in-the-repository)
- [License](#license)

## Setting it up

These steps set everything up on a new machine. Steps 1 and 2 are enough to use the command by hand. Steps 3 to 6 add the Claude Code plugin, a voice and the automatic rewrites.

### 1. Check the requirements

You need git, Python 3.11 or newer, and the `claude` command line tool, installed and logged in. Log in through `claude` itself, because the `wr humanize` command starts `claude` with only basic environment variables such as `PATH` and `HOME`. A login kept in an environment variable such as `ANTHROPIC_API_KEY` therefore does not reach it.

### 2. Install the command

```bash
git clone https://github.com/pablogiaccaglia/writing-register ~/writing-register
~/writing-register/install.sh
```

The installer creates a virtual environment inside the clone, installs the package into it, and links `wr` into `~/.local/bin`. The command reads the skill and the voices from the clone, which is why the installer uses editable mode: a `git pull` then updates the command, the skill and the voices together. If the installer says `~/.local/bin` is not on your `PATH`, add it in your shell profile. Then check that the command answers:

```bash
wr voice    # says there is no voice until you turn one on
```

### 3. Install the Claude Code plugin

```bash
claude plugin marketplace add ~/writing-register
claude plugin install writing-register@writing-register
```

Inside Claude Code, `/plugin marketplace add ~/writing-register` and `/plugin install writing-register@writing-register` do the same. Restart Claude Code afterwards, because a session loads its plugins when it starts.

The plugin ships two skills, and both are available as soon as it is installed. One is the humanizer skill. The other is the writing-register skill, which tells an agent that writes markdown to run `wr voice --core` before it starts, to print the part of the user's voice meant for the model (nothing, when no voice is on), and to run `wr humanize` on the file once it is finished.

The plugin also has hooks, which are commands Claude Code runs at fixed points in a session, such as when a session or a subagent starts, before a tool runs, or when Claude's turn ends. At the start of a session, and of each subagent, they give Claude the machine-writing patterns to avoid and the voice when one is set. [docs/STEERING.md](docs/STEERING.md) explains what each way of instructing the model can reach, what was measured about it, and why wr uses the ones it does. The hooks read your configuration each time they run, and they do nothing until step 4 turns something on.

### 4. Choose a voice and the automatic rewrites

The voice and the automatic rewrites are both optional. You set both in one configuration file that lives on your machine and is never committed to a repository:

```toml
# ~/.config/writing-register/config.toml
voice = "technical-colleague"
auto = ["markdown", "commit", "pr"]
```

The `voice` setting takes either the name of a voice in this clone's `voice/` folder or a path to your own voice file, and `"none"` means no voice. The `auto` setting lists the kinds of text the plugin rewrites on its own, and you can leave out any of its three values. The sections [Turning on a voice](#turning-on-a-voice) and [Automatic rewrites in Claude Code](#automatic-rewrites-in-claude-code) explain each setting in full.

After editing the file, run `wr voice`. If the file has a mistake, such as a misspelled setting, `wr` stops with a message naming the problem. The plugin shows the same message at the start of each Claude Code session, and the voice and the automatic rewrites stay off until you fix the mistake.

### 5. Prepare each repository

Add `*.refused.md` to your own global git ignore file, which is `~/.config/git/ignore` unless `core.excludesFile` says otherwise. When a rewrite fails its checks, wr saves it next to the original file under a name that this pattern matches, and the automatic markdown rewrite skips any repository that does not ignore those files. One line in the global file covers every repository on the machine, including the ones you have not cloned yet. Put the line in a repository's own `.gitignore` instead when your team should have it too.

### 6. Check that it works

Start a new interactive Claude Code session in a git repository. The hooks do not run in a scripted `claude -p` session, so a scripted session cannot serve as the test.

1. At the start of each interactive session, the plugin gives Claude the voice, when one is on, and a note saying which kinds of text wr rewrites. Ask Claude whether its context has a note starting "wr rewrites these automatically". With `auto` set, it does. If you only set a voice, ask whether Claude was given the voice instead.
2. Ask Claude to commit a small change. Claude Code shows a line such as `wr rewrote the commit message in 4s`, or a line saying why wr kept the message as it was.
3. Ask Claude to write a short markdown file. The rewrite runs in the background after the turn ends. When you send your next message, Claude is told which file wr rewrote or kept, and you can ask Claude what it was told.

### Updating

```bash
git -C ~/writing-register pull
claude plugin marketplace update writing-register
claude plugin update writing-register@writing-register
```

The pull updates the command right away. Claude Code keeps its own copy of the plugin's skills and hooks, and the other two commands refresh that copy, so the update reaches the sessions you start afterwards. Run `install.sh` again only when the pull changes `pyproject.toml`.

### Turning it off

- To stop an automatic rewrite, remove its value from `auto`, or remove the whole line. The hooks read the file each time they run, so you do not need to restart.
- To stop using the voice, set `voice = "none"` or remove the line. A session that is already running keeps the voice it was given when it started.
- To turn the plugin off entirely, run `claude plugin disable writing-register@writing-register` and restart Claude Code.

## Rewriting a file

Run the command from the root of the repository whose prose you want to rewrite, then look at what changed. The directory matters because the command looks up the paths a document names under the current directory. To look them up somewhere else, pass that directory with `--root`.

```bash
wr humanize --dry-run docs/SETUP.md   # print the diff, write nothing
wr humanize docs/SETUP.md
git diff docs/SETUP.md
```

For each file, the command makes two model calls. The first rewrites the document. It gets the humanizer skill, the voice when one is on, the document, and the document's sources, which are the files the document links to or names by path. The model may use the sources to explain things the document assumes the reader already knows, and it may not take background from anywhere else.

The command does not send every file a document points at. It sends only text files, and it never sends a file that git ignores, because in a working checkout such files hold runtime data. In a service that records meetings, for example, the ignored files can be real transcripts.

The second call, the checker, checks the rewrite against the repository's code. It exists because the model that rewrites has not seen the code: in one repository's docs on 2026-09-15 it added sentences that were false and passed every string check, such as a claim that a report wrote each finding to a database, when a separate script does that. The checker is a second `claude` call that can read and search a copy of the repository's tracked files and nothing else. For every sentence the rewrite added or changed, it has to cite the line of code that confirms or contradicts it, or say why the code cannot decide it. wr checks each citation and puts back the paragraph or list item around any sentence the code contradicts or cannot confirm. [docs/USAGE.md](docs/USAGE.md#what-the-checker-verifies) explains the rules.

The file is replaced only if the rewrite passes the string checks, and, when there is code to check it against, the checker's verdicts. With `--no-check`, or outside a git repository, the string checks alone decide and the line says the rewrite was not checked against the code. The string checks refuse, for example, a rewrite that changes code, alters a link, or introduces a name or number found in neither the document nor its sources. [docs/USAGE.md](docs/USAGE.md#what-is-checked) lists all of them. A refused rewrite is saved next to the file as `SETUP.refused.md`, so you can compare it with the original and copy the good parts over by hand.

The rewrite of a short file takes a few seconds and a long one can take several minutes. The check then took between about one and seven minutes on the documents measured on 2026-09-15. Read the diff when the command finishes, because the checker catches most wrong explanations but not all of them, and nothing judges whether the new text reads better. To skip the check, pass `--no-check`.

## Turning on a voice

Without a configuration file, the rewrite follows the humanizer skill alone, so nobody ends up writing in someone else's voice by accident. You turn a voice on with the `voice` setting shown in [step 4](#4-choose-a-voice-and-the-automatic-rewrites).

A name refers to a voice in this clone's `voice/` folder: a directory such as `voice/<name>/`, or a single file such as `voice/<name>.md`, and a directory wins over a file of the same name. The value can also be a path to your own voice file or voice directory. In a path, `~` is expanded, and a relative path is read from the folder that holds the configuration file. A voice can be a single markdown file or, once it grows, a directory of rule files with the evidence and decisions behind each rule; [docs/VOICE_FORMAT.md](docs/VOICE_FORMAT.md) describes both, and [docs/BUILDING_A_VOICE.md](docs/BUILDING_A_VOICE.md) explains how to build one from evidence. [`voice/technical-colleague/`](voice/technical-colleague/) is a complete example, and `wr voice check` checks a voice directory's structure.

Running `wr voice` shows which voice is active and where that choice came from. To run without the voice once, pass `wr humanize --voice none`. When the plugin is installed, it also gives the voice to Claude at the start of every interactive session, so Claude's chat replies follow the voice as well.

## Automatic rewrites in Claude Code

The plugin can rewrite text as Claude writes it, so you do not have to run the command each time. It does this through its hooks, which do nothing until the `auto` setting lists the kinds of text they should rewrite. Each value turns on one kind of rewrite:

- `commit`: when Claude runs `git commit`, the plugin rewrites the message before the command runs. A short message takes a few seconds. Trailers at the end of the message, such as `Co-Authored-By:`, stay as they are. If the rewrite takes longer than 90 seconds, the hook gives up and the command runs as Claude wrote it.
- `pr`: the plugin does the same for the description passed to `gh pr create` or `gh pr edit`. When one command both commits and opens a pull request, both texts are rewritten.
- `markdown`: the plugin notes each markdown file Claude writes or edits, and after Claude's turn ends it rewrites, in the background, the prose Claude added.

The markdown rewrite sends only the sentences and list items Claude added to the model. The rest of the file, including the rest of a paragraph or list Claude added to, never passes through the model, and text you type yourself, even between turns, is never rewritten. A change that adds fewer than 8 words, such as swapping one word, is left as Claude wrote it, and the file keeps its line endings. This rewrite changes prose only and adds no background, because the model that rewrites does not see the repository. When you send your next message, the plugin gives Claude each passage it changed, old and new, so that Claude, who wrote the text with the repository open, can check that each still says what it meant. It also tells Claude which files it kept as they were. If Claude edits a file while its rewrite is still running, wr refuses the rewrite and keeps Claude's edit, then rewrites the newer version once that turn ends.

All three kinds run the same string checks as `wr humanize`, with two differences: a commit message, a pull request description and a rewritten passage may shrink to a quarter of the original, where a whole document may only shrink to half, and a passage must also keep its shape, so a sentence stays one paragraph and a list keeps its items. When the checks refuse a rewrite, the text stays as Claude wrote it. Only `wr humanize` run by hand also checks against the code, because that check takes minutes.

Some markdown files are never rewritten: files outside a git repository, files git ignores, changelogs, and files under `vendor/`, `node_modules/`, `tests/`, `test/`, `fixtures/`, `testdata/` or `.claude/`. Those folders hold copies of other projects, files that tests compare byte for byte, and instructions for agents. The plugin also skips every file in a repository whose `.gitignore` does not list `*.refused.md`, and it tells Claude to add that line. Without the line, a refused rewrite saved next to its file would show up as a new file in `git status`.

The hooks also have limits that no setting changes:

- No hook runs in a scripted `claude -p` session. The call that `wr humanize` makes is such a session, so one rewrite never starts another.
- A rewritten command still needs the same permission as the command Claude wrote.
- Chat replies are not rewritten, because no hook can change a reply before it is shown.

[docs/USAGE.md](docs/USAGE.md#automatic-rewrites) describes which command shapes the hooks rewrite, where they keep their state, and the remaining details.

## What is in the repository

| Path | What it is |
|---|---|
| [`vendor/humanizer/`](vendor/humanizer/) | The humanizer repository, copied unchanged. [`vendor/UPSTREAM.md`](vendor/UPSTREAM.md) records the commit |
| [`voice/technical-colleague/`](voice/technical-colleague/) | The example voice, as a directory of rules, evidence and decisions. [`voice/technical-colleague.md`](voice/technical-colleague.md) is generated from it for reading. A voice is used only by someone who turns it on in their configuration |
| [`src/writing_register/`](src/writing_register/) | `humanize.py` builds the prompt and runs the checks, `config.py` reads the user's configuration, `spawn.py` runs `claude` in a sandbox, `cli.py` is the command, `hooks.py` answers the plugin's hooks, `passages.py` finds and rewrites the prose that changed in a turn, `changes.py` lists what a rewrite changed sentence by sentence and puts text back, `verify.py` is the checker, `voice.py` and `voice_tools.py` read and check voice directories |
| [`scripts/`](scripts/) | `replay_check.py` runs the checker on a corpus of real rewrites described by a `corpus.toml`, such as the invented one in `tests/fixtures/sample/`, and `rejudge_replay.py` and `agreement.py` measure a rule change on saved runs without a model call. `claude_prose.py`, `mine_*.py` and `tell_rates.py` mine Claude Code transcripts for a voice and measure what it changed |
| [`plugin/skills/writing-register/`](plugin/skills/writing-register/SKILL.md) | The skill an agent loads while writing markdown |
| [`hooks/hooks.json`](hooks/hooks.json) | The plugin's hooks. Each one calls `wr hook` with the name of its event |
| [`.claude-plugin/`](.claude-plugin/) | The plugin manifest, which ships that skill and the humanizer skill |
| [`docs/USAGE.md`](docs/USAGE.md) | Every option, what the output means, the automatic rewrites in detail, and how to adopt this in another repository |
| [`docs/VOICE_FORMAT.md`](docs/VOICE_FORMAT.md), [`docs/BUILDING_A_VOICE.md`](docs/BUILDING_A_VOICE.md) | The voice format, and the method for building a voice |
| [`docs/STEERING.md`](docs/STEERING.md) | How each way of instructing Claude Code reaches the model, and what was measured about it |
| [`scrub/`](scrub/), [`scripts/scrub_check.py`](scripts/scrub_check.py) | The check that keeps confidential text out of this public repository |

## License

writing-register is released under the MIT license, Copyright (c) 2026 Pablo Giaccaglia; see [LICENSE](LICENSE). [CONTRIBUTING.md](CONTRIBUTING.md) explains how to propose a change. The humanizer skill under `vendor/humanizer/` is MIT licensed by its authors. The license text is in [its license file](vendor/humanizer/LICENSE).
