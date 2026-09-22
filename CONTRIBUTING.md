# Contributing

Issues and pull requests are welcome. A change is easiest to review when it comes with a test that shows what it fixes or adds.

## Setting up

```bash
git clone https://github.com/pablogiaccaglia/writing-register
cd writing-register
./install.sh
.venv/bin/python -m pytest
```

The test suite runs without a model. The packaging test builds a wheel, which needs the package index, and skips itself when the index is unreachable. Tests that really call `claude` are skipped unless `WR_LIVE=1` is set.

## What a change needs

- **A test written first.** The test should fail before the change and pass after it. Most of the code exists to prevent a failure that happened once, and a test like this lets every later change check that the failure is still prevented.
- **No edits under `vendor/humanizer/`.** That folder is an unchanged copy of the humanizer repository at the commit recorded in `vendor/UPSTREAM.md`, and tests check that the whole repository and its license are there. Changes to the skill belong upstream.
- **Documentation that matches the code.** If a change alters what a command does, the same pull request updates README.md, docs/USAGE.md or docs/VOICE_FORMAT.md to match.
- **A clean scan.** CI runs `scripts/scrub_check.py scan --tree . --exclude private/`, which refuses things that should not be in a public repository, such as home-directory paths, IP addresses, email addresses outside an allowlist, token shapes and file types like notebooks or transcripts. Run it before you push.

## Voices

A voice describes one person. A pull request that changes `voice/technical-colleague/` should therefore fix a mistake in that description and should not add your own preferences. If you want to share a voice of your own, publish it in your own repository. [docs/BUILDING_A_VOICE.md](docs/BUILDING_A_VOICE.md) explains how to build one, and [docs/VOICE_FORMAT.md](docs/VOICE_FORMAT.md) explains how to lay it out.
