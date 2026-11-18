"""Command line interface: TUI by default, plus scriptable subcommands."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

from . import __version__, config
from . import journal as journal_mod
from . import policy as policy_mod
from .edit import apply_changes, build_command, plan, target_flags, verify_flags
from .model import Overrides, TrackKind
from .scan import ToolNotFound, clear_cache, find_tools, identify, list_mkv_files

PICK_RE = re.compile(r"^([avs])(\d+)$")


def _flags_str(flags: tuple[bool, bool, bool]) -> str:
    return "".join("●" if v else "○" for v in flags)


def _load_states(directory: str, tools: dict[str, str]) -> list:
    files = list_mkv_files(directory)
    if not files:
        print(f"no .mkv files in {Path(directory).resolve()}", file=sys.stderr)
    return [identify(f, tools) for f in files]


def cmd_scan(args: argparse.Namespace) -> int:
    tools = find_tools()
    states = _load_states(args.directory, tools)
    if args.json:
        payload = [{
            "path": s.path, "error": s.error,
            "tracks": [{"number": t.number, "kind": t.kind.value, "codec": t.codec,
                        "language": t.language, "name": t.name,
                        "enabled": t.enabled, "default": t.default, "forced": t.forced}
                       for t in s.tracks],
        } for s in states]
        print(json.dumps(payload, indent=2))
        return 0 if all(s.ok for s in states) else 1
    for state in states:
        print(f"\n{Path(state.path).name}")
        if state.error:
            print(f"  ERROR: {state.error}")
            continue
        for t in state.tracks:
            name = f' "{t.name}"' if t.name else ""
            print(f"  #{t.number} {t.kind.value:<10} {t.codec:<24} {t.language:<5} "
                  f"E/D/F={_flags_str(t.flags)}{name}")
    return 0


def _parse_picks(tokens: list[str], states: list, *,
                 disable_others: bool | None = None) -> dict[str, Overrides]:
    """--pick a1 / --pick s2 style: the Nth track of that type (mkvpropedit's
    own selector language, matching the user's muscle memory)."""
    parsed = []
    for token in tokens:
        m = PICK_RE.match(token)
        if not m:
            raise SystemExit(f"invalid --pick {token!r}: use aN/sN/vN (e.g. s2 = second subtitle track)")
        parsed.append(({"a": TrackKind.AUDIO, "s": TrackKind.SUBTITLES, "v": TrackKind.VIDEO}[m.group(1)],
                       int(m.group(2))))
    per_file: dict[str, Overrides] = {}
    settings = config.load_settings()
    for state in states:
        overrides: Overrides = {}
        for kind, nth in parsed:
            of_kind = state.tracks_of(kind)
            if nth > len(of_kind):
                print(f"{Path(state.path).name}: no {kind.value} track #{nth}, skipping this pick",
                      file=sys.stderr)
                continue
            chosen = of_kind[nth - 1]
            kind_disable = (
                disable_others
                if disable_others is not None
                else (settings.disable_other_subs if kind is TrackKind.SUBTITLES
                      else settings.disable_other_audio if kind is TrackKind.AUDIO
                      else False)
            )
            overrides.update(policy_mod.pick_overrides(
                state, kind, chosen.number,
                also_force=settings.force_picked_subs,
                disable_others=kind_disable))
        per_file[state.path] = overrides
    return per_file


def cmd_apply(args: argparse.Namespace) -> int:
    tools = find_tools()
    settings = config.load_settings()
    policy = policy_mod.load_policy(args.policy) if args.policy else policy_mod.PickPolicy.from_settings(settings)
    if args.policy and policy is None:
        raise SystemExit(f"unknown policy {args.policy!r}; see: mkvtune policies")

    states = _load_states(args.directory, tools)
    disable_others = True if getattr(args, "disable_others", False) else None
    if disable_others and policy is not None:
        from dataclasses import replace
        policy = replace(policy, disable_other_subs=True, disable_other_audio=True)
    manual = _parse_picks(args.pick, states, disable_others=disable_others) if args.pick else None

    results = []
    for state in states:
        if state.error:
            print(f"{Path(state.path).name}: scan failed: {state.error}", file=sys.stderr)
            continue
        if manual is not None:
            overrides = manual[state.path]
            notes = ["manual picks: " + " ".join(args.pick)]
        else:
            picked = policy_mod.smart_pick(state, policy)
            overrides, notes = picked.overrides, picked.notes
        changes = plan(state, overrides)
        if not changes:
            print(f"{Path(state.path).name}: already correct ({'; '.join(notes)})")
            continue
        if args.dry_run:
            cmd = build_command(state.path, changes, tools["mkvpropedit"])
            print(f"{Path(state.path).name}: would change {len(changes)} track(s) "
                  f"({'; '.join(notes)})")
            print("  " + shlex.join(cmd))
            continue
        result = apply_changes(state.path, changes, mkvpropedit=tools["mkvpropedit"])
        results.append(result)
        status = "ok" if result.ok else f"FAILED ({result.error})"
        print(f"{Path(state.path).name}: {status} — {len(changes)} track(s); {'; '.join(notes)}")

    if args.dry_run or not results:
        return 0

    path = journal_mod.write_journal(results, note=f"cli apply ({args.policy or 'smart'})")
    if path:
        print(f"journal: {path.stem} (undo with: mkvtune undo {path.stem})")

    failed = [r for r in results if not r.ok]
    for result in results:
        if not result.ok:
            continue
        fresh = identify(result.path, tools, use_cache=False)
        problems = verify_flags(fresh, target_flags_by_result(result))
        result.verified = not problems
        result.verify_problems = problems
        mark = "verified" if not problems else f"VERIFY FAILED: {'; '.join(problems)}"
        print(f"  {Path(result.path).name}: {mark}")
    return 1 if failed else 0


def target_flags_by_result(result) -> dict:
    from .model import merge_flags
    return {c.track.number: merge_flags(c.track.flags, c.after) for c in result.changes}


def cmd_undo(args: argparse.Namespace) -> int:
    tools = find_tools()
    if args.list:
        for p in journal_mod.list_journals():
            data = journal_mod.load_journal(p)
            print(f"{p.stem}  {data.get('created', '?')}  {len(data.get('entries', []))} file(s)  {data.get('note', '')}")
        return 0
    journals = journal_mod.list_journals()
    if args.journal:
        target = config.journal_dir() / f"{args.journal}.json"
        if not target.exists():
            raise SystemExit(f"no journal named {args.journal!r}")
    elif journals:
        target = journals[0]
    else:
        raise SystemExit("no journals to undo")

    data = journal_mod.load_journal(target)
    plan_entries = journal_mod.undo_plan(data)
    failed = 0
    for path, changes in plan_entries:
        result = apply_changes(path, changes, mkvpropedit=tools["mkvpropedit"])
        status = "restored" if result.ok else f"FAILED ({result.error})"
        print(f"{Path(path).name}: {status}")
        if result.ok:
            fresh = identify(path, tools, use_cache=False)
            want = {c.track.number: c.after for c in changes}
            problems = verify_flags(fresh, want)
            if problems:
                print(f"  VERIFY FAILED: {'; '.join(problems)}")
                failed += 1
        else:
            failed += 1
    print(f"undo of {target.stem}: {len(plan_entries) - failed}/{len(plan_entries)} file(s) restored")
    return 1 if failed else 0


def cmd_policies(args: argparse.Namespace) -> int:
    for name, p in policy_mod.list_policies().items():
        force = "default+forced" if p.force_picked_subs else "default only"
        dis = []
        if p.disable_other_subs:
            dis.append("subs")
        if p.disable_other_audio:
            dis.append("audio")
        extra = f", disables other {'+'.join(dis)}" if dis else ""
        print(f"{name:<24} audio={','.join(p.audio_langs)}  subs={','.join(p.sub_langs)}  [{force}{extra}]")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import subprocess
    settings = config.load_settings()
    print(f"mkvtune {__version__}")
    try:
        tools = find_tools()
        for name, path in tools.items():
            out = subprocess.run([path, "--version"], capture_output=True, text=True)
            print(f"{name}: {path} — {out.stdout.splitlines()[0] if out.stdout else '?'}")
    except ToolNotFound as exc:
        print(f"TOOL PROBLEM: {exc}")
    print(f"config:  {config.config_dir()}")
    print(f"data:    {config.data_dir()}")
    print(f"cache:   {config.cache_dir()}")
    print(f"logs:    {config.log_dir() / 'mkvtune.log'}")
    print(f"theme:   {settings.theme}   debug: {settings.debug}")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    from .tui.app import run_tui
    return run_tui(args.directory, settings=config.load_settings())


SUBCOMMANDS = {"tui", "scan", "apply", "undo", "policies", "doctor"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mkvtune",
        description="Pick MKV audio/subtitle defaults and batch-apply flags "
                    "(enabled/default/forced) with mkvpropedit — no remux.")
    parser.add_argument("--version", action="version", version=f"mkvtune {__version__}")
    sub = parser.add_subparsers(dest="subcommand")

    p_tui = sub.add_parser("tui", help="interactive TUI (default)")
    p_tui.add_argument("directory", nargs="?", default=".")
    p_tui.set_defaults(func=cmd_tui)

    p_scan = sub.add_parser("scan", help="print the track layout of every .mkv in the folder")
    p_scan.add_argument("directory", nargs="?", default=".")
    p_scan.add_argument("--json", action="store_true", help="machine-readable output")
    p_scan.set_defaults(func=cmd_scan)

    p_apply = sub.add_parser("apply", help="batch-apply picks (smart-pick or --pick) to the folder")
    p_apply.add_argument("directory", nargs="?", default=".")
    p_apply.add_argument("--pick", action="append", metavar="aN|sN",
                         help="manual pick: Nth audio/subtitle track, e.g. --pick a1 --pick s2")
    p_apply.add_argument("--policy", help="named policy preset (see: mkvtune policies)")
    p_apply.add_argument("--disable-others", action="store_true",
                         help="also set flag-enabled=0 on unpicked tracks of the picked type(s)")
    p_apply.add_argument("--dry-run", action="store_true", help="print the mkvpropedit commands only")
    p_apply.set_defaults(func=cmd_apply)

    p_undo = sub.add_parser("undo", help="restore the flags from a journal (default: latest)")
    p_undo.add_argument("journal", nargs="?", help="journal id (see: mkvtune undo --list)")
    p_undo.add_argument("--list", action="store_true", dest="list", help="list journals")
    p_undo.set_defaults(func=cmd_undo)

    p_pol = sub.add_parser("policies", help="list smart-pick policy presets")
    p_pol.set_defaults(func=cmd_policies)

    p_doc = sub.add_parser("doctor", help="diagnostics: tools, paths, settings")
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    config.setup_logging()
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] not in SUBCOMMANDS and not args_list[0].startswith("-"):
        args_list.insert(0, "tui")  # `mkvtune ~/anime` opens the TUI there
    parser = build_parser()
    args = parser.parse_args(args_list)
    func = getattr(args, "func", None)
    if func is None:
        args.directory = "."
        args.func = cmd_tui
        func = cmd_tui
    try:
        return func(args)
    except ToolNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
