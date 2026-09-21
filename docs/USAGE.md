# Using wr humanize

writing-register rewrites a file so its prose reads as a person wrote it, and `wr humanize` is the command that does the rewriting. The command sends the file to the `claude` command line tool together with the humanizer skill. The skill is a set of instructions that lists the patterns that make text read as machine-written and says how to fix each one. This repository vendors the skill, which means it keeps an unchanged copy of the upstream humanizer repository. A user can also turn on a voice, which is a markdown file that says how the result should sound, and the call then includes the voice as well. When the reply comes back, the command checks it with string comparisons and refuses any rewrite that breaks something those comparisons can detect, such as changed code or an invented number.

The repository also ships a Claude Code plugin. While an agent writes markdown, the plugin loads the same skill, along with the voice when one is on. The plugin can also run the rewrite by itself on the commit messages, pull request descriptions and markdown files that Claude writes. The [README](../README.md) explains how to install both the command and the plugin.

## Contents

- [The command](#the-command)
- [The voice configuration](#the-voice-configuration)
- [What the call gets](#what-the-call-gets)
- [What is checked](#what-is-checked)
- [What the checker verifies](#what-the-checker-verifies)
- [The output](#the-output)
- [Automatic rewrites](#automatic-rewrites)
- [Starting in another repository](#starting-in-another-repository)
- [Writing a voice](#writing-a-voice)
- [Updating the humanizer](#updating-the-humanizer)

## The command

```
wr humanize <paths...> [--voice NAME|FILE|none] [--full-voice] [--root DIR]
                       [--no-sources] [--no-check] [--check-timeout SECONDS]
                       [--check-effort LEVEL] [--dry-run] [--model NAME] [--timeout SECONDS]
wr humanize --text [--kind prose|commit|pr] [--voice NAME|FILE|none] < text
wr voice [--core]
wr hook <event>
```

The command handles the paths one at a time and makes two model calls for each file. The first call rewrites the file, and the second checks the rewrite against the repository's code. A short file takes a few seconds and a long one can take several minutes. For example, when the documentation of one service was rewritten with this command on 2026-09-14, each document took between 46 seconds and a little over 8 minutes.

Before using the command, log in to the `claude` tool through `claude` itself. The command starts the tool with only basic environment variables such as `PATH`, `HOME`, the locale and the proxy settings, so a key or token kept in an environment variable such as `ANTHROPIC_API_KEY` never reaches it.

| Option | What it does |
|---|---|
| `--voice NAME\|FILE\|none` | The voice for this run, overriding the configuration. A name refers to `voice/<name>.md` in this clone, a value with a slash or ending in `.md` is a file path, and `none` sends the humanizer skill alone |
| `--full-voice` | Sends the whole voice file instead of only its core, the part of the file written for the model (see [Writing a voice](#writing-a-voice)) |
| `--root DIR` | The repository root. A path the document names in inline code, such as `src/job.py`, is looked up under it. The default is the current directory |
| `--no-sources` | Sends the document without the files it points at, so the rewrite cannot add any background. The check still runs |
| `--no-check` | Skips the checker and writes the rewrite once the string checks pass. The output line says it was not checked |
| `--check-timeout SECONDS` | How long the checker call may take. The default is 600 |
| `--check-effort LEVEL` | The effort level of the checker call. The default is `high`, which on 2026-09-15 stopped the checker answering claims it had not searched |
| `--dry-run` | Makes both calls, runs the checks, puts back what the checker rejected and prints the diff between the document and the result. It writes nothing, not even a refused rewrite |
| `--model NAME` | Passed to `claude --model` |
| `--timeout SECONDS` | How long one call may take before it fails. The default is 600 |
| `--text` | Reads a text from stdin and prints the rewrite on stdout, instead of rewriting files |
| `--kind prose\|commit\|pr` | With `--text`, the kind of text. The default is `prose`. `commit` keeps the subject on one line with its prefix, such as `fix(render):`, and keeps trailers such as `Co-Authored-By:` without sending them to the model. `pr` keeps the generated-with footer the same way |

When the command refuses a rewrite made with `--text`, it prints the original text on stdout and the reason on stderr, then exits with code 1. A script that pipes text through the command therefore always gets usable text back.

The last form, `wr hook <event>`, is what the plugin's hooks call, and nobody needs to run it by hand. Hooks are commands that Claude Code runs at fixed points in a session, and each hook passes its event's JSON to `wr hook` on stdin. The hooks share their code with the command, and [Automatic rewrites](#automatic-rewrites) describes what they do.

## The voice configuration

No voice is set by default, so a teammate who installs writing-register gets only the humanizer skill. Each user turns a voice on in their own configuration file:

```toml
# ~/.config/writing-register/config.toml
voice = "technical-colleague"
```

The command looks for the configuration file in three places, in order: `$WR_CONFIG`, then `$XDG_CONFIG_HOME/writing-register/config.toml`, then `~/.config/writing-register/config.toml`. The file has two settings:

- `voice` is either the name of a voice in this clone's `voice/` folder or a path to a voice file. A path may start with `~`, which is expanded, and a relative path is read from the configuration file's folder. `"none"`, an empty value and a missing file all mean no voice.
- `auto` lists what the Claude Code plugin rewrites by itself, as described in [Automatic rewrites](#automatic-rewrites).

The command checks the configuration before it makes any model call. The check is strict, so a typo such as `vocie` cannot turn the voice off without anyone noticing. The run stops with exit code 2 and a message naming the problem if the file is not valid TOML or contains any of these: an unknown setting, a voice name that does not exist, a voice file that is missing, or an `auto` value the plugin does not know.

To see which voice is active and where that choice came from, run `wr voice`. To see the part of the voice the model receives, run `wr voice --core`, which prints nothing when no voice is set. The plugin ships a writing-register skill that an agent loads while writing markdown. That skill runs `wr voice --core` before the agent starts, so the agent writes in the same voice the command uses.

## What the call gets

Each model call gets a single prompt made of four parts, in this order:

1. The instructions. They tell the model to use the skill's file mode, which is meant for rewriting a named file and lets only prose change. The model therefore keeps code, links and front matter exactly as they are and replies with nothing but the finished document. When the document has sources (the files it points at), the instructions also tell the model to write for a teammate who did not build the part being described.
2. The voice, if one is on. The prompt carries the voice's core, or the whole file with `--full-voice`. Wherever the voice differs from the skill, the voice wins.
3. The humanizer skill, read byte for byte from `vendor/humanizer/SKILL.md`. That directory is an unchanged copy of the upstream humanizer repository (see [Updating the humanizer](#updating-the-humanizer)).
4. The document, followed by its sources.

A document's sources are the local files it links to and any files it names by a path in inline code. The model gets them so it can explain what the document expects a new reader to know already, and it may take that background from those files and from nowhere else.

The command limits what it sends in three ways. It sends only text files, so a named `.env` never ends up in the prompt. It never sends a file that git ignores, because in a working checkout those files hold runtime data; in a service that records meetings, for example, that data can include real transcripts and what the service remembers about people. Outside a git repository, the text-file rule is the only filter. Finally, each source is cut off at 40,000 characters, and all the sources together at 160,000.

## What is checked

The command replaces the file only when the rewrite passes every check in the table. Each check is a string comparison, so checking needs no extra model calls.

| The rewrite is refused when it | Because |
|---|---|
| comes back empty | there is nothing to write |
| is under half the length of the original, or under a quarter for a commit message or PR description | a rewrite that short has dropped content. A commit message or PR description gets the lower floor because a body made of filler can legitimately shrink that much, and the 14 real commit messages sampled on 2026-09-15 all came out at least as long as before |
| changes a fenced code block | a rewrite may change prose only |
| loses or alters inline code | naming an existing identifier again, dropping its backticks, or rewriting a span as prose that keeps all its words in one paragraph (`owner = retention` as "whose owner is `retention`") is allowed |
| introduces inline code found in neither the document nor its sources | the name may be invented |
| loses or alters a link | a link is a fact about another file |
| adds a link to a file that does not exist, or to a URL | a link to an existing file or to a heading in the document is allowed |
| introduces a number found in neither the document nor its sources | this includes a number the model worked out, such as a sum or a unit conversion |

These checks catch invented names, numbers and links, but they cannot tell whether a sentence explains something wrongly. The checker described in [What the checker verifies](#what-the-checker-verifies) judges that against the code.

## What the checker verifies

Once the string checks pass, the command lists every sentence and list item the rewrite added or changed. It then asks a second model call, the checker, whether the repository's code bears each one out. The checker is needed because the rewriting model never sees the repository, so a wrong explanation it adds reads as well as a right one. On 2026-09-15, the rewrite of another repository's docs added three wrong sentences that all passed every string check: "the report writes each finding as a row in the Findings database" (a separate script does that), "snapshot folders are removed after 13 months" (nothing removes them) and "the tool raises `role-drift`" (a guard no plug-in sets stops it).

### What the checker gets

The checker is a `claude` call with `--safe-mode`, so no CLAUDE.md, skill, hook or plugin reaches it, and it has only the Read, Grep and Glob tools. It reads a copy of the repository made for the call, built from the files git tracks at the last commit, then the working tree's edited and new files, and then the document itself. The copy leaves out files git ignores, files named like secrets (such as `.env` files, `credentials.json` or private keys) and symlinks, and it is deleted when the call ends. The prompt holds the numbered sentences, each changed sentence with its original wording, the rewritten document and the original document.

### What it answers

The checker gives each sentence one of six verdicts:

| Verdict | Meaning | What happens to the sentence |
|---|---|---|
| TRUE | the code confirms it; the answer cites the file, the line and the words on that line that decide it, and the guard around that line when there is one | kept |
| FALSE | the code contradicts it, cited the same way; when the answer rests on something being absent, it lists the searches that found nothing | put back |
| SAME | a changed sentence says nothing its original did not | kept |
| ORIGINAL | the sentence only restates the original document; the answer quotes the passages | kept |
| NOT_A_FACT | advice, an opinion, a lead-in or the document describing itself | kept |
| UNVERIFIABLE | the checker searched and the repository cannot decide it | put back |

### What wr checks in the answer

The checker is a model and can be wrong. Wrong sentences were under 1% of the changed ones in the rewrites measured, so these rules exist mostly to keep correct sentences from being put back:

- A TRUE or FALSE citation must name a file inside the copy that is not a secrets file, the quoted words must be at least eight characters long and appear within three lines of the cited line, and a claim about what code does must cite code, not a document. A citation that fails any of these makes the verdict UNVERIFIABLE.
- wr runs again each search that the checker says found nothing, in a child process with a time limit. A search that finds something, or runs out of time, makes the verdict UNVERIFIABLE.
- A FALSE on a changed sentence must name words the new wording adds; a changed number, or a "not" added or dropped, counts on its own. Without that, the verdict becomes SAME.
- SAME on an added sentence counts as UNVERIFIABLE, and ORIGINAL quotes must appear word for word in the original document.
- Every call also carries up to two definitions taken from the copy, such as "`scripts/lib/report.py` defines `build`", and only wr records which ones they are. They are names defined once outside the test, fixture and vendor folders, so a checker that looks can always confirm them. A repository that has no such name gives no definitions, and then this check does not run. A checker that does not confirm them has not looked, and the refusal names the definitions it missed.

The prompt also asks the checker for three things: to cite and check the guard a line runs under, to treat a sentence saying something happens as a claim about code even when a policy sets it as a rule, and to cite the code of the component a sentence names. These three are instructions only. When wr enforced them mechanically and the enforcement was measured on saved runs, it caught no wrong sentence the checker had not already caught and put back about three times as many correct ones.

### What is put back, and when the rewrite is refused

wr takes back a sentence the checker rejects in one of two ways, depending on whether the rewrite added it or changed it:

- An added sentence is removed on its own. If it resembles a sentence the rewrite removed, the rewrite moved and reworded that sentence, and the original takes its place.
- A changed sentence puts back the whole paragraph or list item it came from, together with every block the rewrite merged into it or split it into, because putting back one sentence of a merge would repeat or lose text.

The string checks then run again on the result.

The whole rewrite is refused, and kept next to the file, when:

- the checker did not check: it left out a sentence, it called at least two sentences and more than 30% of them unverifiable without saying the fact lies outside the repository, it did not confirm the definitions it was given, or it used no tool at all while judging six or more sentences that needed looking at, which is every sentence except those it called the same, already in the original, or not a fact
- more blocks would be put back than the document allows: more than half of the changed blocks when at least three changed, and in no case more than 8 blocks, or a quarter of them in a document that changes more than 32 blocks
- a paragraph could not be put back without repeating text the rewrite kept elsewhere
- the copy of the repository could not be made, or the checker call failed

A rewrite kept this way has not passed the check, and the output line says so. Sometimes there is nothing to check against, because the file is outside a git repository, the repository has no commits, or the document is outside the repository. The rewrite is then written after the string checks alone, and the output line says it was not checked and why.

### How it was measured

The checker was measured on the text before and after 19 real rewrites in two private repositories, together with the sentences people found wrong or verified true against the code. That corpus stays private, and `tests/fixtures/sample/` holds an invented one of the same shape, with one planted wrong sentence for each kind of error below. `scripts/replay_check.py` runs the checker on a document of a corpus and saves its answer, and `scripts/rejudge_replay.py` and `scripts/agreement.py` apply the current rules to saved answers without a model call. On 2026-09-15 the final checker ran three times on each of 9 of the real documents, 27 runs in all, with these results:

- It caught the labelled wrong sentences in 18 of 21 chances. One miss was a checker call that failed, which refuses the rewrite. The other two were runs that read a function calling a helper and stopped there, missing what the helper did, so a claim that can only be judged by reading through a helper is caught only some of the time.
- It kept the labelled true sentences in 66 of 69 chances.
- It put back 2.3% of the other changed sentences, and several of those were real errors that the people reading the diffs had kept, among them a sentence that gave an action to the wrong component, a workaround the code does not support, and a rule stated for everyone when the code exempts some people.
- A check took between one and three minutes for most documents and about seven for a 217-sentence operations guide, and cost between $0.40 and $3 per document.

## The output

The command prints one line for each file, and each line names the voice that was used. In this example the first file was rewritten and the second was refused:

```
docs/ARCHITECTURE.md: rewritten in 84s, checked in 131s (two model calls, voice technical-colleague (config), 3 sources): 1 sentence put back. Read the diff: git diff docs/ARCHITECTURE.md
  put back (FALSE) "The summariser then sends the report rows to the dashboard.": stationlog/push.py:24 `urllib.request.urlopen(request, timeout=30)`
docs/OPS.md: refused after 312s (one model call, no voice, 5 sources), file unchanged: a code block changed, and the rewrite may change prose only
  the rewrite is kept in docs/OPS.refused.md to read or use
```

Each sentence that was put back gets its own indented line with the checker's verdict and its citation or reason. With `--dry-run`, the command prints the diff between the document and the result under each line and saves nothing. The line for a rewrite refused by the check says that the kept copy has not passed the check, and the line for a rewrite that could not be checked says why.

Without `--dry-run`, a refused rewrite leaves the original file as it was and is saved next to it, as `OPS.refused.md` is in the example. A refused rewrite usually goes wrong in only one place, so most of it can still be used. To recover it, read the reason at the end of the line, compare `OPS.refused.md` with `OPS.md`, copy the good parts over by hand, and then delete the refused file.

The exit code tells a script whether every file went through:

| Exit code | Meaning |
|---|---|
| 0 | every file was rewritten, or needed nothing |
| 1 | a rewrite was refused by the string checks or the check against the code, or a model call failed |
| 2 | a path is not a file, the voice or the configuration cannot be used, or wr cannot find the skill it reads from its clone (install it with `install.sh`) |

## Automatic rewrites

The plugin's hooks let Claude Code rewrite text as Claude writes it, so nobody has to run the command by hand. Each hook calls `wr hook <event>`, and the hooks do nothing until the configuration says what they should rewrite:

```toml
auto = ["markdown", "commit", "pr"]
```

Each value turns on one kind of rewrite:

| Value | When it runs | What happens |
|---|---|---|
| the voice and the patterns | When a session starts, and when a subagent starts | Claude is given the voice, a card naming the machine-writing patterns, and what wr rewrites by itself. Without a voice the card goes on its own, so a session is never left with no guidance. A subagent gets the voice too, because the session's own context does not reach it: measured across 5,654 transcripts, a subagent wrote 6.57 em or en dashes per 1,000 words where the main conversation wrote 0.40 |
| `commit` | Before Claude runs `git commit` | The message is rewritten, and the command runs with the new message. The subject keeps its prefix, and the trailers stay as they are |
| `pr` | Before Claude runs `gh pr create` or `gh pr edit` | The same, for the description after `--body`. The generated-with footer is never sent to the model, so it stays as it is. The model is asked to keep the headings and checklists, but no check refuses a rewrite that drops one |
| `markdown` | Before and after Claude writes or edits a `.md` file, and when Claude's turn ends | The hook before an edit saves the file's text, and the hook after it records the sentences and list items that edit added. When the turn ends, a hook running in the background rewrites that recorded prose, with the same string checks as `wr humanize` but no sources and no check against the code (see [Only new prose is rewritten](#only-new-prose-is-rewritten)). At your next message, Claude is given each passage the rewrite changed, old and new, and asked to check that each still says what it meant; files kept as written are named too |

### Commit messages and pull request descriptions

The hooks rewrite a commit message or a PR description only when Claude wrote it in one of the two shapes Claude normally uses:

- a quoted heredoc, such as `-m "$(cat <<'EOF' ...)"` or `-F -` followed by one
- a double-quoted message containing no `$` or backticks, because the shell would expand those

The message also has to belong to the `git commit` or `gh pr` command itself. Text inside a heredoc body or inside quotes never counts, and neither does a message given to another command on the same line, such as `git tag -m` after a commit or `pytest -m` before one. Any other command runs unchanged, for example one with `--no-edit`, with two `-m` options, or with `--body-file`.

When one command holds both a commit message and a PR description, both are rewritten, the two model calls run side by side, and a single notice reports each result. A commit written as `git -C path commit` is handled like any other. The hook starts `wr` only when the command mentions a commit or `gh pr`, so other commands run no slower.

Rewriting a short message took between 4 and 14 seconds when measured on 2026-09-15. The hook gives up after 90 seconds and lets the command run as Claude wrote it. The command also runs as Claude wrote it when the checks refuse the rewrite, and Claude Code then shows a one-line notice with the reason.

### Only new prose is rewritten

The automatic markdown rewrite only ever touches text that Claude wrote. Right before each Write, Edit or MultiEdit of a markdown file (Claude Code's file-editing tools), a hook saves the file's text. Right after the edit, another hook compares the two versions and records the sentences and list items the edit added. A sentence or item counts as added when its text is not already in the file. A paragraph Claude only moves is therefore not recorded, and neither is a sentence that only gained a word or two, unless Claude added that sentence earlier. Text typed in an editor, written by a shell command, or already in the file before Claude touched it is never recorded, so it is never rewritten, no matter when it was typed.

When the turn ends, the recorded text is rewritten in passages. A passage is a run of recorded sentences inside one paragraph, or of recorded items inside one list, that holds at least 8 words, not counting bullets and item numbers. Tables, fenced and indented code, headings, quotes, HTML, rules and front matter are never sent. The threshold of 8 words comes from 5,292 real edits to markdown files in Claude sessions, counted on 2026-09-15. Edits that added 7 words or fewer were small changes such as a renumbered step, a table cell or a single word, while edits that added 8 or more were new content.

The model gets the whole document so it can read the passages in context, with each passage marked by a token that changes on every call. It sends back only those passages, and they are put back where they were. The rest of the file, including the rest of any paragraph or list a passage sits in, never passes through the model, and every byte outside the passages stays as it was, line endings included. Each passage is checked on its own and is refused unless it meets all of these conditions:

- it does not come back empty
- it keeps its shape: sentences stay in their paragraph and keep the punctuation that separates them from the next sentence, a list passage keeps its number of items and each item's indentation and marker, and no heading, code, quote or table appears in it
- it keeps at least a quarter of its length, the same floor a commit message gets
- it passes the usual checks, with the rest of the document counting as a source

A rewritten passage is done: its text is no longer recorded, so a later turn does not rewrite it again. A passage refused on its content is done too. A reply that does not follow the format is refused, and its passages stay recorded for up to 3 attempts, one after each later turn that touches the file. Recorded text that is no longer in the file is forgotten, and so is everything recorded for a file that was deleted or skipped, for example because its repository did not yet ignore `*.refused.md`. The rewrite runs in the background, so it can finish after Claude has already edited the file again; in that case it removes only what it handled, and nothing the newer edit recorded is lost.

This rewrite changes prose only. Its prompt tells the model not to add a fact, and it gets no sources, because the model does not see the repository and Claude wrote these sentences minutes earlier with the repository open. It is not checked against the code either, because that check takes minutes per file and would run after every turn. Instead, the note Claude gets with your next message lists each passage the rewrite changed, old and new, up to 20 of them, with each side cut around its first difference when it is long, and asks Claude to check that each still says what it meant.

Running `wr humanize` by hand still rewrites the whole file, adds background from the sources, and checks the result against the code.

### Which markdown files are skipped

The hooks leave a markdown file alone when:

- it is outside a git repository, or git ignores it
- it sits under a `vendor/`, `node_modules/`, `tests/`, `test/`, `fixtures/`, `testdata/` or `.claude/` folder, because those folders hold copies of other projects, files that tests compare byte for byte, and instructions for agents
- its name starts with `CHANGELOG`
- it is itself a refused rewrite

The hooks also skip every file in a repository that does not ignore `*.refused.md`, and they tell Claude to add that line. Refused rewrites are saved next to their files, so without it they would show up as new files in `git status`. The check asks git, not the repository's own `.gitignore`, so a line in your global ignore file (`~/.config/git/ignore`, unless `core.excludesFile` says otherwise) covers every repository at once.

### Edits made while a rewrite runs

A rewrite never overwrites an edit made while it was running. The command writes the rewrite into a temporary file next to the document and swaps the two files in one atomic step. After the swap it checks the text it swapped out. If that text is not what the rewrite started from, someone edited the file in the meantime, so the command undoes the swap and refuses the rewrite. Claude Code's own Write and Edit tools replace a file by renaming a new one into place, and the swap catches that case too. On a system that cannot swap atomically, the command instead compares the file just before an ordinary write.

What happens next depends on who made the edit. The edit hook records any edit Claude makes, so when Claude made it, the newer version is rewritten at the end of that turn and no refused copy is kept. When the file was changed outside Claude, the refused rewrite is kept next to it as usual.

### Where the hooks keep their state

The hooks keep separate state for each session: the list of noted files, the text of a file right before each of Claude's edits, the record of the sentences and list items Claude added that have not been rewritten yet, a log of when Claude edited each file, and the messages waiting for Claude. The state lives under `~/.cache/writing-register/auto/`, under `$XDG_CACHE_HOME/writing-register/auto/` when that variable is set, and under `$WR_STATE_DIR` when that one is, which wins over both.

### Rules that hold for every hook

- No hook runs in a scripted `claude -p` session. The call that `wr humanize` makes is such a session, so a rewrite never starts another one.
- No hook grants a permission. A rewritten command goes through the same permission rules as the command Claude wrote.
- If `wr` is not installed, the hooks exit without doing anything.
- No hook fails silently. When a hook hits an error, it shows a one-line message naming the error and lets Claude carry on. When the configuration has a mistake, the session-start hook reports it, and the voice and the automatic rewrites stay off until the mistake is fixed.
- No hook rewrites chat replies, because no hook can change a reply before it is shown. Instead, at the start of each interactive session, a hook gives Claude the voice (when one is on) and tells it which kinds of text wr rewrites.

## The voice as an output style

An output style is Claude Code's own way to change how the model writes: its text goes into the instructions Claude Code gives Claude, it is sent with every request, and it survives compaction. `wr style` writes one from the voice that is active:

```bash
wr style            # writes ~/.claude/output-styles/writing-register.md
wr style --enable   # writes it and selects it in ~/.claude/settings.json
wr style --print    # prints it instead
```

The style carries the machine-writing patterns and, when one is set, the voice, with the voice winning where they disagree. It is generated rather than shipped, because a plugin's files are the same for everyone and a voice belongs to a person. It sets `keep-coding-instructions: true`, so Claude Code's software-engineering instructions stay as they are and only the writing changes. Run it again after editing the voice, and restart Claude Code to pick the change up.

A style reaches the main conversation and a fork, never an ordinary subagent, which is why the plugin also gives the voice to each subagent through a hook. When the style is selected, the session-start hook stops sending the voice, so it never travels twice. [docs/STEERING.md](STEERING.md) covers the trade-off in full.

## What the rewrites have had to change

Each automatic rewrite leaves one line in `~/.cache/writing-register/auto/metrics/`, naming the file, how many words it sent to the model, how many came back changed, how long it took and whether it was refused. `wr report` reads them:

```
$ wr report
48 rewrites recorded, the first on 2026-09-16.
31 passage rewrites of what Claude wrote: 12% of the words it sent were changed, 44 passages in all.
17 commit messages and pull request descriptions: 31% of the words changed.
2 were refused and kept beside their file.
```

The number worth watching is the share of words changed. It says how far what Claude writes by itself still sits from what you want to read, so it should fall as the voice does its work. Set `metrics = false` in the configuration to keep no record at all.

## Starting in another repository

1. Make sure `*.refused.md` is ignored, either by the repository's `.gitignore` or by your global ignore file, which covers every repository. The line keeps refused rewrites out of version control, and the automatic markdown rewrite stops skipping the repository.
2. From the repository root, run `wr humanize --dry-run` on one document and read the diff it prints. If you want the rewrite, run the command again without `--dry-run`.
3. Rewrite the other documents one at a time, and read each diff before you start the next one.

The checks say nothing about whether the new text reads better, so reading the diff is the only way to judge a rewrite. Reading each diff before moving on keeps that work to one document at a time, whereas if forty documents are rewritten before anyone reads the first diff, someone then has forty diffs to judge at once.

## Writing a voice

A voice file is a markdown file that tells the model how the text should read. The model receives it word for word, so the part it receives should be written as instructions to a writer: who the reader is, how a paragraph opens, which details belong, and what to avoid.

A voice can be long, because the model gets only its core unless `--full-voice` is given. The core is everything above a line that reads `<!-- wr:end-of-core -->`, and a file without that line is all core. Below the line go the evidence, examples and reasoning that the people who maintain the voice need, along with any passage that explains a rule rather than stating it. Keep the core short enough that the model still gives weight to the skill's own patterns.

To share a new voice through the clone, put it in `voice/<name>.md`. You can also keep it anywhere on your machine and point the configuration at its path.

### When a voice outgrows one file

A voice that keeps growing becomes hard to change without breaking something: rules of different kinds end up side by side, and a new rule can land in the core with nothing behind it. A voice can then become a directory, which `wr voice split voice/<name>.md --into voice/<name>` does without changing a byte of what the model receives:

```
voice/<name>/
  voice.toml      the name, the order the rule files are read in, and a budget in characters
  rules/          what the model receives; one rule per line, each opening with a marker such as {#register.no-dashes}
  evidence/       dated quotes, before-and-after pairs and measurements, each naming the rules it supports
  decisions.md    where requests pulled in different directions, and what was decided
  about.md        how the voice was built
  maintaining.md  how to change it
```

The markers let evidence and decisions point at a rule, and they never reach the model. A rule may refine an earlier one with `{#kinds.no-bold refines=register.bold}`, which means the more specific rule wins where both apply. Three commands work on a directory:

- `wr voice check` finds what the runtime tolerates but should not: a rule file missing from the manifest, a refinement pointing at nothing, a rule with no evidence when evidence is required, a measurement pasted into a rule, a decision older than the evidence for its rule, a core over budget, and a generated file that is out of date.
- `wr voice build` writes `voice/<name>.md` beside the directory: the whole voice in one readable file, which is itself a valid single-file voice with the same core. Never edit it; edit the directory and build again.
- `wr voice show register.bold` prints a rule with everything that points at it, which is what to read before changing it.

[`voice/technical-colleague/`](../voice/technical-colleague/) is a working example, [VOICE_FORMAT.md](VOICE_FORMAT.md) is the full reference, and [BUILDING_A_VOICE.md](BUILDING_A_VOICE.md) explains how to build a voice from evidence.

## Updating the humanizer

The directory `vendor/humanizer/` is an unchanged copy of the upstream humanizer repository, blader/humanizer on GitHub, and nobody edits it in this repository. The file `vendor/UPSTREAM.md` records the upstream commit and version the copy came from. The tests in `tests/test_vendor.py` check that the copy's files, license and version are intact, and that the version written in that record matches the skill's own version. Because of that version check, the record has to change whenever the copy does.

To move to a newer version, replace the directory with a fresh clone, write the new commit and version into [`vendor/UPSTREAM.md`](../vendor/UPSTREAM.md), and run the tests.
