# Steering how a model writes

wr does two different things. It rewrites text that already exists, which is
what `wr humanize` and the automatic rewrites do, and it steers the model
before the text exists, which is what this document is about. Steering is the
cheaper half: it costs no model call, it reaches the replies and messages no
rewriter touches, and it prevents what a rewrite can only repair.

This document records the mechanisms Claude Code offers, what each one can and
cannot reach, what was measured on one machine in September 2026, and which of
them wr uses.

## The mechanisms, and what each one reaches

Claude Code has four ways to put instructions in front of the model, and they
differ in force and in reach.

| Mechanism | Where the text goes | When it applies | Reaches subagents | Survives compaction |
|---|---|---|---|---|
| Output style | The instructions Claude Code gives Claude | Every request | No, only the main conversation and a fork | Yes |
| Hook context (`SessionStart`, `SubagentStart`, `UserPromptSubmit`) | The conversation, at most about 10,000 characters per hook | When the event fires | Only through `SubagentStart` | Only when the hook matches `compact` |
| `CLAUDE.md` and `.claude/rules/` | A user message after the system prompt | Every session | Yes, unless the agent opts out | Yes, for the project file |
| Skills | The conversation, when invoked | Only when something invokes it | Only when preloaded into an agent | Partly, within a token budget |

Three facts decide most designs. **An output style does not reach an ordinary
subagent**, because a subagent runs its own system prompt. **A skill loads only
when the model chooses it**, from a description of at most 1,536 characters, so
nothing guarantees it is ever read. **A hook's text is cut above about 10,000
characters**: Claude Code saves it to a file and shows the model a 2KB preview
with the file's path. The documentation does not state the limit; in the
transcripts the largest text delivered whole was 9.7KB and the smallest one
persisted was 9.9KB (2026-09-21).

There is a fifth mechanism that is not instruction at all: a `PreToolUse` hook
can replace what a tool is about to do, which is how wr rewrites a commit
message before `git commit` runs. That one is deterministic, and it is the only
one that does not depend on the model agreeing.

## What was measured

The numbers below come from every Claude Code transcript on one machine: 5,654
files holding 9.0 million words of replies, 3.5 million words of markdown
written through the file tools, and 371,000 words the user typed. Two scripts
in this repository produce them, with no model calls:

- `scripts/claude_prose.py` separates the three kinds of text by the fields of
  each transcript record, so a reply is never confused with a file the model
  wrote or with what a person typed.
- `scripts/tell_rates.py` scores candidate patterns over that corpus and over
  pairs of text before and after a rewrite (`--pairs`).

### A voice in the session start changes how the model writes

The session-start hook began injecting a voice on 2026-09-14. Splitting the
replies at that date, per 1,000 words:

| Signal | Before | After | What the person types |
|---|---|---|---|
| Em and en dashes | 17.70 | 0.40 | 1.58 |
| Parenthetical glosses | 11.98 | 3.08 | 3.39 |
| Back-referring openers | 0.21 | 0.03 | 0.14 |
| Process vocabulary | 0.16 | 0.01 | 0.33 |
| Closing offers | 0.26 | 0.04 | |
| First person | 8.38 | 1.12 | |

Nothing rewrites a reply in a terminal, so this is instruction alone, over 4.9
million words before and 467,000 after.

### A skill that is never invoked steers nothing

Across the same 5,654 transcripts, skills were invoked 2,210 times, and the
humanizer skill **zero** times. The plugin's own writing-register skill was
invoked three times. The skill's name appears in 528 transcripts only because
the injected text names it.

A `paths` pattern on a skill, which the documentation says loads it when the
model works on matching files, did not change that count.

### The machine-writing patterns everybody lists are not the ones that occur

Of the humanizer's 25 patterns, 18 fire between 0.00 and 0.03 times per 1,000
words in every corpus measured, including documents written before any voice
existed. Two fire more often in what the person types than in what the model
writes: the rule of three, at 0.78 against 0.33, and stock words such as
"pivotal" or "meticulous", at 0.11 against 0.01.

One pattern was different. Em and en dashes ran at 17.70 per 1,000 words, and
they are the one the voice names explicitly.

The conclusion wr draws from this: **measure the patterns your own text
actually contains before building a detector for the famous ones.**

### A voice's own permissions become the next signature

After the voice arrived, the model followed it into two new habits, measured
against the documents people here keep and edit:

| Signal, per 1,000 words | Replies | Subagent reports | Edited documents |
|---|---|---|---|
| Bold spans | 8.95 | 20.52 | 0.00 to 3.33 |
| List items | 10.90 | 27.29 | 6.9 to 9.0 |

`wr humanize` itself halves bold when it runs, from 6.66 to 3.33 per 1,000
words, which is the same judgement from a different direction. Both rules in
the voice these measurements come from were tightened on 2026-09-16, each carrying its measurement.

### Subagents write differently, because nothing reached them

Until 2026-09-16 the voice reached the main conversation only. Per 1,000 words,
after the voice, main conversation against subagent: dashes 0.40 against 6.57,
parenthetical glosses 3.08 against 13.62, bold 8.95 against 20.52, list items
10.90 against 27.29.

## What wr uses, and why

**Both halves travel in every modality.** wr knows two sets of rules: the
humanizer skill, which says what machine writing looks like, and a voice, which
says how one person wants to read. A rewrite has always carried both in one
call, with the voice winning where they disagree. Steering now does the same.
Until 2026-09-16 it carried the voice alone, so a user with no voice was
steered by nothing at all, and nobody was ever told the patterns.

The skill is 28,728 characters, too much for every session and every subagent,
so what travels is a card of 1,115 characters built from the skill's own
numbered headings (`src/writing_register/patterns.py`). It cannot drift,
because it is read from the skill, and a test fails when the skill gains or
renames a pattern. The full text stays one tool call away as the
`writing-register:humanizer` skill, and `wr humanize` still sends it whole.

**The session-start hook carries the voice and the card.** It reaches every
session in every directory as soon as the plugin is enabled, it is read from
the voice file each time so an edit takes effect without restarting anything,
and it re-injects after compaction because the hook matches every start reason.
With no voice set, the card goes on its own.

**The subagent-start hook carries the same two things to subagents**, which no
output style can reach. The first line differs: a subagent is told that the
voice covers what a person will read and that the report it returns to its
caller stays plain.

**Both hooks deliver in parts.** A voice is longer than the 10,000 characters
Claude Code shows of one hook's text, so until 2026-09-21 every session-start
and subagent delivery reached the model as its first 2KB only: 227 deliveries
since 2026-09-15, none whole. The plugin now registers each of the two hooks
four times, and `wr hook ... --part K` sends only part K, cut where a section
starts, at most 9,000 characters, and labelled "part K of N" because Claude Code
may deliver the parts in any order. A live check in an interactive session
showed a new subagent receiving all three parts of the voice in full.

**`wr style` writes the same two things as an output style** for anyone who
wants them in the system prompt instead. It works with no voice, carrying the
card alone. The voice style is generated rather than shipped, because a plugin's
files are the same for everyone and a voice belongs to a person; the plugin does
ship the style without a voice, `writing-register:human-prose`, for anyone who
has none.
`keep-coding-instructions: true` leaves Claude Code's software-engineering
instructions in place, so the style changes how the model writes and nothing
about how it works. When the voice style is selected, the session-start hook
stops sending the voice and the card, so they never travel twice; with the
plugin's style it stops sending the card only. The hook reads the choice the way
Claude Code does: the project's `.claude/settings.local.json`, where `/config`
saves it, then the project's `.claude/settings.json`, then the user's settings.

**Hooks on tools do the deterministic half.** A commit message and a pull
request description are rewritten before their command runs, and the markdown
Claude wrote is rewritten when the turn ends. Those do not ask the model to
agree.

**Nothing depends on a skill being chosen.** The skills stay, because a person
can invoke one by name, but no part of the design assumes it.

## The line between people and agents

The voice is for text a person reads: replies in the conversation, documents
and READMEs, reports, cards, code comments and docstrings, commit messages and
pull request descriptions, and messages written to a person.

It stops at agent-to-agent traffic. A subagent's report to whoever called it, a
message to another session, a prompt written for a tool: a model reads those,
and a model reads a plain, dense, literal report better than a polished one.
Both the output style and the subagent hook say so in their first paragraph.

This is also why wr steers nothing about tool use, progress reporting or how
work is scoped. Those belong to Claude Code's own instructions, and a style
that replaced them would trade good writing for worse engineering.

## Measuring it again

```bash
.venv/bin/python scripts/claude_prose.py --out /tmp/prose.jsonl   # about 30 seconds
.venv/bin/python scripts/tell_rates.py --prose /tmp/prose.jsonl   # about 20 seconds
wr report                                                          # what the rewrites changed
```

`wr report` reads the record each automatic rewrite leaves behind. The share of
words it had to change is the number that should fall as steering works.

## What is not settled

- **Whether a style beats a hook in practice.** The style is in the system
  prompt and the hook's text is in the conversation, which should favour the
  style, but nothing here has measured the difference. Running with the style
  for a while and re-reading the same numbers is the experiment.
- **What the whole voice changes in subagents.** Until 2026-09-21 they received
  only the first 2KB of it, so the measurements above describe subagents with
  almost no voice. The rates script can answer the question once there is
  enough subagent text written with all parts delivered.
- **Whether the agent-to-agent line is in the right place.** A subagent's
  report often reaches a person second-hand, through whoever relays it.
