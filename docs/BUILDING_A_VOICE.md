# Building a voice

A voice is a written specification of how one person wants to read the text a model writes for them. This guide explains how to build one from evidence of what that person corrects. It covers where the evidence comes from, how a correction becomes a rule, how rules are grouped and kept traceable, how much a voice may cost, and how to tell whether it works. [VOICE_FORMAT.md](VOICE_FORMAT.md) is the reference for the file format, and [`voice/technical-colleague/`](../voice/technical-colleague/) is a complete voice built this way, with the owner's private details removed.

The examples on this page come from an invented team that runs a network of weather stations.

## Contents

1. [What a voice is for](#1-what-a-voice-is-for)
2. [Collect corrections, not preferences](#2-collect-corrections-not-preferences)
3. [Turn a correction into a rule](#3-turn-a-correction-into-a-rule)
4. [Admit a rule only when it recurs](#4-admit-a-rule-only-when-it-recurs)
5. [Group rules by the question they answer](#5-group-rules-by-the-question-they-answer)
6. [Keep the evidence beside the rule](#6-keep-the-evidence-beside-the-rule)
7. [Record decisions where requests conflict](#7-record-decisions-where-requests-conflict)
8. [Decide how large the core may grow](#8-decide-how-large-the-core-may-grow)
9. [Measure whether it works](#9-measure-whether-it-works)
10. [Mining conversations at scale](#10-mining-conversations-at-scale)
11. [Changing a voice later](#11-changing-a-voice-later)

## 1. What a voice is for

A model already writes fluent text. What it cannot know is what one particular reader needs: which terms that reader knows, which details they consider noise, in which order they want a result, and how they want a formula explained. A voice writes that down once. writing-register then gives it to the model in every session, every subagent and every rewrite, so the reader no longer has to correct the same things by hand.

A voice works beside the humanizer skill that writing-register ships. The skill describes the marks of machine-written prose that almost every reader dislikes, such as stock phrases and dashes used as a universal connector. The voice describes one reader, and where the two disagree, the voice wins.

A voice describes what its owner wants to read, which is often different from how they write. Notes typed at speed for oneself (fragments, arrows, lowercase) are shorthand for a reader who already knows the context, and the voice is written for a reader who does not.

## 2. Collect corrections, not preferences

The raw material of a voice is the owner's reactions to real text, each kept word for word with its date. Asked in the abstract, most people describe the writing they admire, while the corrections they make show what they need.

Good sources, in order of strength:

- **Corrections to generated text**: messages to a coding assistant such as "what is this 4.2??? 4.2 of what", comments on a published page, and review notes. Each one reacts to a specific sentence, so the rule behind it can be recovered exactly.
- **Before-and-after edits**: a paragraph the owner rewrote, or asked to have rewritten, kept beside its replacement. The pair shows the rule at work.
- **Instructions the owner repeats**, such as "start with the result" or "no internal codes". The repetition is itself evidence, as section 4 explains.
- **Memory and instruction files** that assistants keep, where corrections were recorded when they happened. Read them as evidence of the rules and not as samples of the voice, because they often break the rules they record.
- **The owner's own writing**, used only for short messages where the voice should sound like them.

Keep each item with its date, where it was said, and what the owner was looking at when they said it. A quote without the text it reacted to is hard to turn into a rule, and a quote without a date cannot be weighed against a later one.

## 3. Turn a correction into a rule

A correction is about one sentence, and a rule is about every sentence like it. Four questions turn one into the other.

**What exactly was wrong?** The message "what is this 4.2??? 4.2 of what" objects to a number that arrived without a unit, before the reader knew what it counts.

**What would the fixed text look like?** "Pressure at the ridge station rose 4.2 hPa in an hour." If you cannot write the fixed version, you have not yet understood the correction.

**What is the general case?** Here it is "no number appears before the reader knows what it counts", which covers far more than units for pressure. State the case by role (a number, a symbol, a figure, a reader), never by the project, person or product it happened in.

**Could a reader point at a sentence that breaks it?** A rule that can only be followed in spirit, such as "be clear", cannot be checked and will not change anything. A rule such as "every symbol in a formula is defined in words where it first appears" can be checked.

Some patterns make rules work better:

- **Open a conditional rule with its trigger.** "When a text uses notation, each symbol names one quantity from start to finish" costs nothing in a text without notation, because the model sees at once that the rule does not apply.
- **Give a textbook example, never one from the work.** "Such as the angle between two vectors" teaches the rule. An example from last week's report leaks private detail and ties the rule to one case.
- **One idea per rule.** When a rule says three things, someone edits it for one of them and silently changes the other two.
- **Name what the rule rejects, when the name is recognisable.** "No coined phrases such as 'methods miss the answer'" is easier to follow than "avoid neologisms".
- **Keep measurements out of the rule.** A line such as "Replies ran at 8.95 bold spans per 1,000 words" is evidence, and inside the rule it would travel in every session. The rule says what to do, and the evidence says why.

## 4. Admit a rule only when it recurs

A single correction may be a reaction to one bad afternoon. A rule enters the voice only when the owner's words back it in **at least two separate conversations**, and a rule seen once goes to a ledger for the owner to decide. Two details govern how that bar is applied:

- **Count separate conversations, not messages.** Five complaints in one session are one sighting, however loud they are. A conversation forked from another counts as the same conversation.
- **Flag weak independence.** Two sightings in the same project, or on the same day, are weaker evidence than two sightings months apart on different work. Keep such a rule, but mark it so a later review knows.

The ledger opens with a table that gives each item one line and a recommendation, so the owner can reply in minutes with something like "accept all except L4 and L9", where the named lines are the ones to leave out. A useful default is to recommend a rule seen in one session when three or more of the owner's own remarks back it, and to hold the rest as evidence only.

## 5. Group rules by the question they answer

A voice of a hundred rules needs an order, so that the model reads them sensibly and a person can find the one to change. Group the rules by the question they answer. For technical writing, this starting set works:

| Group | The question it answers |
|---|---|
| reader | Who is reading, and what do they already know? |
| register | What should the prose sound like, and what marks does it avoid? |
| introduce | When may a term, number, label or person first appear? |
| order | In what order do ideas, paragraphs and sections come? |
| journey | Does the text describe the current state or the story of getting there? |
| content | Which details stay and which are noise? |
| claims | How are numbers, estimates, causes and negative claims worded? |
| references | How does the text point at sources and at its own parts? |
| kinds | What differs for each kind of text: documentation, cards, status updates, replies, commit messages? |
| rewriting | What may a rewrite change, and what must it keep? |

Add a group when the owner's corrections open a new area. The example voice has four such groups: mathematics and notation, figures, data and evidence that a method works, and long published pages. Rules for one kind of text go in their own file inside `kinds/`, and a single rule states that a kind's rule wins over a general rule where the two differ. When one rule is a specific case of another, mark it with `refines=` instead of restating the general rule.

## 6. Keep the evidence beside the rule

Every rule should be traceable to the words that justify it. In a voice directory, evidence lives under `evidence/`, in entries that name the rules they support. With `evidence = "required"` set in `voice.toml`, `wr voice check` refuses any rule that has no evidence behind it.

Evidence matters most on the day someone wants to change a rule. `wr voice show reports.results-first` prints the rule together with every evidence entry, decision and refining rule that points at it, and that list is what to read before editing. Without it, a rule that looks arbitrary gets deleted, and the correction it encoded comes back a month later.

Quotes keep the owner's words exactly, typos included, and replace other people with their role. The model never receives the evidence, which is written for people, so an entry may be as long as it needs to be.

## 7. Record decisions where requests conflict

An owner's requests do not always agree. One month they ask for the method before the results, and another month they keep a page that shows results first. Both were real preferences in their context. Record the question, what was chosen and the date in `decisions.md`:

```markdown
## Results or method first in a research document?
Rules: reports.results-first, reports.method
Decided: 2026-09-21

A short version of the results first, then the method in full, then the detailed results.
```

When new evidence arrives for a rule whose decision is older, `wr voice check` warns, and the owner either confirms the decision with a `Confirmed:` date or rewrites it. Decisions are the part of a voice most worth keeping as it grows, because they stop settled questions from being reopened by accident.

## 8. Decide how large the core may grow

The core of a voice travels in every session and every subagent, so each character costs context on every turn. Set `budget` in `voice.toml` and let the checker fail when the core outgrows it. The failure lists the size of each rule file, so raising the budget becomes a dated decision with a reason instead of something that drifted.

There are two ways to stay within a budget, and the first is preferred. Remove rules that restate other rules (`wr voice check --overlaps` lists candidates), then move measurements, dates and long examples into the evidence. Shortening a rule until it reads like a telegram usually costs more than it saves, because the model follows a clear sentence better than a compressed one.

## 9. Measure whether it works

A voice is an instruction, and whether an instruction changes behaviour can be measured. Take one corpus of text the model wrote before the voice existed and one written after, count a handful of patterns per 1,000 words in each, and compare the two. `scripts/tell_rates.py` does this over replies and files that `scripts/claude_prose.py` extracts from Claude Code transcripts, and over pairs of text before and after a rewrite.

The table below comes from the transcripts of the person the example voice belongs to, measured on 2026-09-16:

| Pattern, per 1,000 words of replies | Before the voice | After |
|---|---|---|
| Em and en dashes | 17.70 | 0.40 |
| Parenthetical glosses | 11.98 | 3.08 |
| Process vocabulary ("leverage", "robust") | 0.16 | 0.01 |

The measurements showed two things. The voice changes the patterns it names, so a pattern the owner never objected to stays where it was. And a rule can create its own signature: a rule permitting bold labels led to replies carrying 8.95 bold spans per 1,000 words, against at most 3.3 in documents people had edited, until the rule was tightened with the measurement in its evidence.

Keep track of what the owner still corrects as well. When the same correction keeps coming back after a rule exists, the rule is either unclear or unread, and it needs an example or a check instead of more words.

## 10. Mining conversations at scale

When the owner has months of conversations with a coding assistant, the corrections in them can be mined instead of collected by hand. writing-register ships the pipeline that was used to build the example voice. The scripts use the standard library only and call no model. Two steps are done by model agents: reading the packets, and synthesis, which merges the readers' points into candidate rules with the keys each one gathers.

| Step | Script | What it produces |
|---|---|---|
| Extract | `claude_prose.py --config mine.toml` | Every message the owner typed, every comment on a published page, every answer to a question, and every edit the assistant made, from the transcripts in a date window |
| Pair | `mine_revisions.py` and `mine_episodes.py` | Episodes: what the owner was looking at, what they said, and the before-and-after text of the change that followed |
| Triage | `mine_triage.py` | The episodes likely to be about writing, with no model call; its recall is measured against quotes you already know should survive |
| Read | `mine_packets.py build` | Packets small enough for a model to read carefully, split among several reader subagents |
| Verify | `mine_packets.py verify` | Every point a reader returns, checked mechanically: the quote must appear in the owner's own words and not in text they were looking at or pasted, and every before-and-after pair must exist |
| Agree | `mine_packets.py agree` | How far a blind second read of a random sample agrees with the first |
| Admit | `mine_admit.py` | For each candidate rule, the separate sessions behind it, a lint for a public rule line, and a ledger for the owner |

Three properties of the pipeline matter before you run it.

**Readers never retype evidence.** A reader returns a quote and the identifiers of before-and-after pairs, and a script checks each one against the source. A model asked to summarise corrections will sooner or later quote something the owner saw instead of something they said, and the check refuses that quote.

**Readers disagree on labels, not on substance.** A blind second read agreed with the first on whether a message contained a writing point in 53 of 55 packets, and on the point's area at 0.82 overlap. On the exact key the point was filed under, the overlap was only 0.23, because each reader names new ideas its own way. A point therefore counts for a rule only when two of three readings agree. The first reading is the reader's key after synthesis has merged similar keys, and the other two come from two further assigners that each map every point to the final list of rules.

**Write rules for a public reader from the start.** A lint in `mine_admit.py` refuses a rule line that contains a dash, a private name, a code identifier, a date or a number from the work. Rules that pass it can be shared as they are, and they generalise better because they cannot lean on a specific case.

## 11. Changing a voice later

- **A new correction.** Find the rule it belongs to with `wr voice show`. If the rule already covers it, add the quote as evidence, since a rule that gets broken after it exists needs an example and not more text. If no rule covers it, write one and hold it to the two-conversation bar.
- **A rule that no longer holds.** Delete it and record a decision with `Retires:` naming it, so evidence that mentions the rule stays valid.
- **A change of mind.** Rewrite the decision, change the rules it names, and let the checker point at every decision that has become stale.
- **After any change,** run `wr voice check` and `wr voice build`, and if the voice is used as a Claude Code output style, run `wr style` to regenerate it.
