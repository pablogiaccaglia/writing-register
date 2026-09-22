"""The command line: `wr humanize` and `wr voice`.

Exit 0 when every file was rewritten or needed nothing, 1 when a rewrite was
refused or the model call failed, 2 when a file, the voice or the configuration
could not be read."""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .config import ConfigError, config_path, load_config, resolve_voice
from .voice import read_core, read_full
from .humanize import DEFAULT_TIMEOUT, SetupError, humanize, humanize_text
from .spawn import SpawnFailed



def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n

def active_voice(flag: str | None) -> tuple[Path | None, str]:
    """The voice file to use and where that choice came from.

    `--voice` wins, then the user's configuration; with neither there is no
    voice. Raises ConfigError for a voice or a configuration that cannot be used."""
    if flag is not None:
        return resolve_voice(flag, Path.cwd()), "--voice"
    cfg = load_config()
    return cfg.voice, "config"


def _voice_label(path: Path | None, source: str) -> str:
    return f"voice {path.stem} ({source})" if path else "no voice"


def cmd_humanize(args, out, spawn=None) -> int:
    try:
        voice_path, source = active_voice(args.voice)
        voice = ((read_full(voice_path) if args.full_voice else read_core(voice_path))
                 if voice_path else "")
    except (ConfigError, OSError) as e:
        print(f"wr cannot pick a voice: {e}", file=out)
        return 2
    label = _voice_label(voice_path, source)
    if args.text:
        return _humanize_stdin(args, out, spawn, voice)
    if not args.paths:
        print("nothing to rewrite: give file paths, or --text to read stdin", file=out)
        return 2
    root = Path(args.root)
    worst = 0
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            print(f"{raw}: not a file", file=out)
            worst = max(worst, 2)
            continue
        old = path.read_text(encoding="utf-8")
        try:
            r = humanize(path, spawn=spawn, voice=voice, write=not args.dry_run,
                         model=args.model, timeout=args.timeout, root=root,
                         sources=not args.no_sources, check=not args.no_check,
                         check_timeout=args.check_timeout, check_effort=args.check_effort)
        except SetupError as e:
            print(f"wr is not set up: {e}", file=out)
            return 2
        except SpawnFailed as e:
            print(f"{raw}: the model call failed ({e}); nothing was written",
                  file=out)
            worst = max(worst, 1)
            continue
        how = (f"{'two model calls' if r.verification is not None else 'one model call'}, {label}"
               f"{' in full' if voice_path and args.full_voice else ''}"
               f"{f', {len(r.sources)} sources' if r.sources else ''}")
        checked = (f", checked in {r.check_seconds:.0f}s" if r.verification is not None
                   else f", not checked against the code: {r.check_skipped}" if r.check_skipped
                   else ", not checked against the code" if args.no_check else "")
        reverted = (f": {len(r.reverted)} sentence{'s' if len(r.reverted) != 1 else ''} "
                    "put back" if r.reverted and not r.refused else "")
        if r.refused:
            print(f"{raw}: refused after {r.seconds:.0f}s{checked} ({how}), file unchanged: "
                  f"{r.refused}", file=out)
            if r.kept:
                unchecked = ("; it has not passed the check against the code, so read it "
                             "against the code before using any of it") if r.kept_unchecked else ""
                print(f"  the rewrite is kept in {r.kept} to read or use{unchecked}", file=out)
            worst = max(worst, 1)
        elif r.written:
            print(f"{raw}: rewritten in {r.seconds:.0f}s{checked} ({how}){reverted}. "
                  f"Read the diff: git diff {raw}", file=out)
        elif args.dry_run:
            print(f"{raw}: dry run, {r.seconds:.0f}s{checked} ({how}){reverted}, nothing written",
                  file=out)
        else:
            print(f"{raw}: nothing to change{reverted}", file=out)
        for line in r.notes:
            print(f"  {line}", file=out)
        for change, verdict in r.reverted:
            sentence = " ".join(change.new.split())
            sentence = sentence if len(sentence) <= 100 else sentence[:97] + "..."
            print(f"  {'would put back' if r.refused else 'put back'} ({verdict.effective}) "
                  f"\"{sentence}\": {verdict.cited or verdict.reason}", file=out)
        if args.dry_run:
            # A dry run pays for the call, so it shows what the call produced.
            out.writelines(difflib.unified_diff(
                old.splitlines(keepends=True), r.text.splitlines(keepends=True),
                fromfile=raw, tofile=f"{raw} (rewrite)"))
    return worst


def _humanize_stdin(args, out, spawn, voice) -> int:
    """Text mode: rewrite stdin and print the result. On a refusal the original
    is printed unchanged, the reason goes to stderr, and the exit code is 1."""
    original = sys.stdin.read()
    try:
        r = humanize_text(original, kind=args.kind, spawn=spawn, voice=voice,
                          model=args.model, timeout=args.timeout)
    except SetupError as e:
        print(f"wr is not set up: {e}", file=sys.stderr)
        out.write(original)
        return 2
    except SpawnFailed as e:
        print(f"the model call failed ({e}); the text is unchanged", file=sys.stderr)
        out.write(original)
        return 1
    out.write(r.text)
    if r.refused:
        print(f"refused, text unchanged: {r.refused}", file=sys.stderr)
        return 1
    return 0


def _voice_target(target: str | None) -> Path | None:
    """The voice a `wr voice` subcommand works on: the one named, or the active one."""
    if target is None:
        return active_voice(None)[0]
    return resolve_voice(target, Path.cwd())


def cmd_voice_check(args, out) -> int:
    """Check a voice directory; exit 1 when there are errors, 2 when it cannot be read."""
    from .voice_tools import check
    try:
        path = _voice_target(args.target)
    except ConfigError as e:
        print(f"wr cannot find the voice: {e}", file=out)
        return 2
    if path is None:
        print("no voice is set, so there is nothing to check", file=out)
        return 0
    if not path.is_dir():
        print(f"{path} is a single-file voice; a directory voice is the one with a structure "
              f"to check (`wr voice split` makes one)", file=out)
        return 0
    findings = check(path, overlaps=args.overlaps)
    for f in findings:
        print(str(f), file=out)
    errors = sum(1 for f in findings if f.level == "error")
    warnings = sum(1 for f in findings if f.level == "warning")
    if not findings:
        print(f"{path}: no problems", file=out)
    else:
        print(f"{path}: {errors} errors, {warnings} warnings", file=out)
    return 1 if errors else 0


def cmd_voice_build(args, out) -> int:
    """Write the view of a voice directory, or with --check say whether it is current."""
    from .voice import VoiceError
    from .voice_tools import build, is_fresh, view_path
    try:
        path = _voice_target(args.target)
    except ConfigError as e:
        print(f"wr cannot find the voice: {e}", file=out)
        return 2
    if path is None or not path.is_dir():
        print("only a voice directory has a view to build", file=out)
        return 0 if path is None else 2
    try:
        if args.check:
            if is_fresh(path):
                print(f"{view_path(path)} is up to date", file=out)
                return 0
            print(f"{view_path(path)} is missing or out of date; run `wr voice build`", file=out)
            return 1
        print(f"wrote {build(path)}", file=out)
    except (VoiceError, OSError) as e:
        print(f"wr cannot build the voice: {e}", file=out)
        return 2
    return 0


def cmd_voice_show(args, out) -> int:
    """Print a rule with what refines it, its evidence and its decisions."""
    from .voice import VoiceError
    from .voice_tools import show
    try:
        path = _voice_target(args.voice_dir)
    except ConfigError as e:
        print(f"wr cannot find the voice: {e}", file=out)
        return 2
    if path is None or not path.is_dir():
        print("only a voice directory has rules to show", file=out)
        return 2
    try:
        out.write(show(path, args.name))
    except KeyError:
        print(f"{args.name} is neither a rule nor a rule file of {path}", file=out)
        return 2
    except (VoiceError, OSError) as e:
        print(f"wr cannot read the voice: {e}", file=out)
        return 2
    return 0


def cmd_voice_split(args, out) -> int:
    """Split a single-file voice into a directory whose core is the same text."""
    from .voice_tools import split
    try:
        root = split(Path(args.file), Path(args.into))
    except FileExistsError:
        print(f"{args.into} already exists; split writes a new directory", file=out)
        return 2
    except (ConfigError, OSError) as e:
        print(f"wr cannot split the voice: {e}", file=out)
        return 2
    print(f"wrote {root}; the model receives the same core. Rename the rule files as you like, "
          f"then run `wr voice build {root}` and `wr voice check {root}`", file=out)
    return 0


def cmd_voice(args, out) -> int:
    """Say which voice is active. With --core, print the part an agent follows,
    or nothing when no voice is set, so a skill can run it unconditionally."""
    try:
        voice_path, source = active_voice(None)
        core = read_core(voice_path) if voice_path else ""
    except (ConfigError, OSError) as e:
        print(f"wr cannot pick a voice: {e}", file=out)
        return 2
    if args.core:
        out.write(core)
        return 0
    if voice_path is None:
        print(f"no voice: the rewrite follows the humanizer skill alone. To turn "
              f"one on, add voice = \"<name>\" to {config_path()}", file=out)
    else:
        print(f"voice {voice_path.stem}, from {source} {config_path()}: "
              f"{voice_path}", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wr", description="Rewrite prose so it reads as a person wrote it.")
    sub = ap.add_subparsers(dest="command", required=True)
    hz = sub.add_parser("humanize",
                        help="rewrite files in one model call each, following the "
                             "humanizer skill and, if one is set, a voice")
    hz.add_argument("paths", nargs="*")
    hz.add_argument("--text", action="store_true",
                    help="read the text from stdin and print the rewrite")
    hz.add_argument("--kind", choices=("prose", "commit", "pr"), default="prose",
                    help="with --text: a commit message keeps its subject line and "
                         "trailers, a PR body keeps its generated footer")
    hz.add_argument("--voice", default=None,
                    help="voice name or file for this run, or 'none'; overrides "
                         "the configuration")
    hz.add_argument("--full-voice", action="store_true",
                    help="send the whole voice file, not only its core")
    hz.add_argument("--root", default=".",
                    help="repository root that sources named by path resolve "
                         "against (default: the current directory)")
    hz.add_argument("--no-sources", action="store_true",
                    help="do not give the call the files the document links to "
                         "or names; it may then add no background")
    hz.add_argument("--dry-run", action="store_true",
                    help="run the rewrite and its checks, print the diff, "
                         "write nothing")
    hz.add_argument("--no-check", action="store_true",
                    help="write the rewrite without verifying its sentences against the code")
    hz.add_argument("--check-timeout", type=int, default=None,
                    help="seconds the checker call may take (default 600)")
    hz.add_argument("--check-effort", default="high",
                    help="effort level of the checker call (default high)")
    hz.add_argument("--model", default=None)
    hz.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    hk = sub.add_parser("hook", help="answer a Claude Code hook event (used by the "
                                     "plugin; reads the hook JSON on stdin)")
    hk.add_argument("event")
    hk.add_argument("--part", type=_positive, default=None,
                    help="send only this part of a long context (session-start, subagent-start)")
    sub.add_parser("report", help="what the automatic rewrites have had to change")
    st = sub.add_parser("style", help="write the voice as a Claude Code output style")
    st.add_argument("--enable", action="store_true",
                    help="also select it in ~/.claude/settings.json")
    st.add_argument("--print", dest="show", action="store_true",
                    help="print the style instead of writing it")
    vo = sub.add_parser("voice", help="show which voice is active")
    vo.add_argument("--core", action="store_true",
                    help="print the voice's core, or nothing when no voice is set")
    vsub = vo.add_subparsers(dest="voice_command")
    vc = vsub.add_parser("check", help="check a voice directory's structure")
    vc.add_argument("target", nargs="?", default=None,
                    help="a voice directory, a voice name, or nothing for the active voice")
    vc.add_argument("--overlaps", action="store_true",
                    help="also list rules that share phrases, which may restate each other")
    vb = vsub.add_parser("build", help="write the whole voice as one readable file beside it")
    vb.add_argument("target", nargs="?", default=None,
                    help="a voice directory, a voice name, or nothing for the active voice")
    vb.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 when the readable file is missing or out of date")
    vp = vsub.add_parser("split", help="turn a single-file voice into a voice directory")
    vp.add_argument("file", help="the voice file to split")
    vp.add_argument("--into", required=True, help="the directory to create")
    vs = vsub.add_parser("show", help="a rule, or a rule file, with everything that points at it")
    vs.add_argument("name", help="a rule identifier such as register.bold, or a file name such as register")
    vs.add_argument("--voice-dir", default=None,
                    help="a voice directory or name; the active voice when not given")
    return ap


def cmd_style(args, out) -> int:
    """Write the voice as an output style, which Claude Code sends with every request."""
    from .style import NAME, StyleError, build, install
    try:
        if args.show:
            out.write(build())
            return 0
        path = install(enable=args.enable)
    except StyleError as e:
        print(f"wr: {e}", file=sys.stderr)
        return 2
    print(f"style written to {path}", file=out)
    if args.enable:
        print(f"selected as the output style. Restart Claude Code, or run /config, "
              f"to use it; `wr style --enable` again after editing the voice.", file=out)
    else:
        print(f'select it with "outputStyle": "{NAME}" in ~/.claude/settings.json, '
              f"or with /config in Claude Code", file=out)
    return 0


def cmd_report(args, out) -> int:
    """Print what the automatic rewrites have had to change (see metrics.py)."""
    from . import metrics
    print(metrics.report(), file=out)
    return 0


def main(argv=None, out=None, spawn=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    if args.command == "style":
        return cmd_style(args, out)
    if args.command == "report":
        return cmd_report(args, out)
    if args.command == "voice":
        if getattr(args, "voice_command", None) == "check":
            return cmd_voice_check(args, out)
        if getattr(args, "voice_command", None) == "build":
            return cmd_voice_build(args, out)
        if getattr(args, "voice_command", None) == "show":
            return cmd_voice_show(args, out)
        if getattr(args, "voice_command", None) == "split":
            return cmd_voice_split(args, out)
        return cmd_voice(args, out)
    if args.command == "hook":
        from .hooks import run
        return run(args.event, sys.stdin, out, spawn=spawn, part=args.part)
    return cmd_humanize(args, out, spawn=spawn)


if __name__ == "__main__":
    raise SystemExit(main())
