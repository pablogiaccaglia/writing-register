---
name: writing-register
description: Use when writing or editing prose in markdown, such as docs, a README, a report, release notes or a card. Applies the humanizer skill, plus the user's own voice when they have turned one on, and rewrites a finished file with `wr humanize`.
paths: "**/*.md"
allowed-tools: Bash(wr:*)
---

# Writing register

Prose in markdown files follows the humanizer skill, which ships in the same plugin as this skill. The humanizer skill lists the patterns that make text read as machine-written and explains how to fix each one. If the user has turned on a personal voice, you follow that voice too. When a file is finished, `wr humanize` rewrites it.

## Before you write

Users can turn on a personal voice in their own configuration. To check whether this user has one, run:

```
wr voice --core
```

If the command prints a voice, follow it while you write. Where the voice and the humanizer skill disagree, follow the voice. If the command prints nothing, the user has not set a voice, so follow the humanizer skill alone and do not imitate anyone's style. Do the same if `wr` is not installed.

## When a file is finished

Once a file is finished, run `wr humanize` on it. If the notes at the start of this session say that `wr` rewrites markdown files automatically, that rewrite changes only the prose you added, and your next prompt lists each passage it changed, old and new: check that each still says what you meant and fix any that does not. Run `wr humanize` by hand when a document needs background explained for a newcomer. The command has two forms:

```
wr humanize --dry-run <file>   rewrite and check, print the diff, write nothing
wr humanize <file>             the same, and write the file in place
```

The rewrite uses the humanizer skill, plus the user's voice if one is set. It also receives the files that the document links to or names by path, so it can add background from them. A second call then checks every sentence the rewrite added or changed against the repository's code, and wr puts back each paragraph or list item holding a sentence the code contradicts or cannot confirm. The output lists each one with the reason.

The command refuses a rewrite that invents a number, a name or a link, that changes code, or that cuts the document to under half its length, and one where the check puts back more than half of what changed. It saves a refused rewrite next to the original file as `<name>.refused.md`.

The check catches most wrong explanations but not all of them, and nothing judges whether the result reads well, so read the diff before you call a document finished.
