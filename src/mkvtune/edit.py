"""Building, applying and verifying mkvpropedit flag changes."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .model import FLAG_NAMES, FileTracks, FlagChange, Flags, Overrides, diff_flags, target_flags

log = logging.getLogger("mkvtune.edit")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass
class ApplyResult:
    path: str
    ok: bool
    cmd: list[str]
    changes: list[FlagChange] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    verified: bool | None = None       # None = not verified
    verify_problems: list[str] = field(default_factory=list)


def plan(state: FileTracks, overrides: Overrides) -> list[FlagChange]:
    return diff_flags(state, overrides)


def build_command(path: str | Path, changes: Sequence[FlagChange],
                  mkvpropedit: str = "mkvpropedit") -> list[str]:
    """One mkvpropedit invocation touching only the flags that actually change."""
    cmd = [mkvpropedit, str(path)]
    for change in changes:
        cmd += ["--edit", change.track.selector]
        for index, flag_name in enumerate(FLAG_NAMES):
            if change.before[index] != change.after[index]:
                cmd += ["--set", f"{flag_name}={int(change.after[index])}"]
    return cmd


def apply_changes(path: str | Path, changes: Sequence[FlagChange], *,
                  mkvpropedit: str = "mkvpropedit", runner: Runner = subprocess.run) -> ApplyResult:
    """Apply flag changes in place. mkvpropedit only rewrites track headers —
    no remux, no re-encode, milliseconds regardless of file size."""
    if not changes:
        return ApplyResult(path=str(path), ok=True, cmd=[], changes=[])
    cmd = build_command(path, changes, mkvpropedit)
    log.info("apply: %s", " ".join(cmd))
    try:
        proc = runner(cmd, capture_output=True, text=True)
    except OSError as exc:
        log.error("failed to run mkvpropedit: %s", exc)
        return ApplyResult(path=str(path), ok=False, cmd=cmd, changes=list(changes), error=str(exc))
    ok = proc.returncode == 0
    if not ok:
        log.warning("mkvpropedit failed for %s (exit %s): %s", path, proc.returncode, proc.stderr)
    return ApplyResult(path=str(path), ok=ok, cmd=cmd, changes=list(changes),
                       stdout=proc.stdout or "", stderr=proc.stderr or "",
                       error=None if ok else f"mkvpropedit exited {proc.returncode}")


def verify_flags(state: FileTracks, expected: Mapping[int, Flags]) -> list[str]:
    """Compare a fresh scan against the flags we meant to set."""
    problems: list[str] = []
    for number, flags in sorted(expected.items()):
        track = state.find(number)
        if track is None:
            problems.append(f"track {number} disappeared from the file")
        elif track.flags != flags:
            want = " ".join(f"{n}={int(v)}" for n, v in zip(("E", "D", "F"), flags))
            got = " ".join(f"{n}={int(v)}" for n, v in zip(("E", "D", "F"), track.flags))
            problems.append(f"track {number}: wanted [{want}] but file has [{got}]")
    return problems
