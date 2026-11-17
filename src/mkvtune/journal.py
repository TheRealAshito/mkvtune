"""Undo journals: every apply writes the before-state so it can be reverted."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from . import config
from .edit import ApplyResult
from .model import FlagChange, Track, TrackKind

log = logging.getLogger("mkvtune.journal")

JOURNAL_FORMAT = 1


def write_journal(results: list[ApplyResult], note: str = "") -> Path | None:
    """Persist what changed and what it was before. One journal per apply run."""
    entries = []
    for result in results:
        if not result.ok or not result.changes:
            continue
        entries.append({
            "path": result.path,
            "cmd": result.cmd,
            "before": [_track_dict(c.track, c.before) for c in result.changes],
            "after": [_track_dict(c.track, c.after) for c in result.changes],
        })
    if not entries:
        return None

    stamp = time.strftime("%Y%m%dT%H%M%S")
    directory = config.journal_dir()
    path = directory / f"{stamp}.json"
    counter = 2
    while path.exists():
        path = directory / f"{stamp}-{counter}.json"
        counter += 1

    payload = {
        "format": JOURNAL_FORMAT,
        "id": path.stem,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    log.info("journal written: %s (%d files)", path.stem, len(entries))
    return path


def _track_dict(track: Track, flags: tuple[bool, bool, bool]) -> dict:
    return {
        "number": track.number, "kind": track.kind.value,
        "codec": track.codec, "language": track.language, "name": track.name,
        "enabled": flags[0], "default": flags[1], "forced": flags[2],
    }


def list_journals() -> list[Path]:
    return sorted(config.journal_dir().glob("*.json"), reverse=True)


def load_journal(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        # allow bare ids
        p = config.journal_dir() / f"{p}.json"
    data = json.loads(p.read_text())
    if data.get("format") != JOURNAL_FORMAT:
        raise ValueError(f"{p} is not a mkvtune journal")
    return data


def undo_plan(data: dict) -> list[tuple[str, list[FlagChange]]]:
    """Changes that restore every entry's before-state.

    Assumes the journal's after-state is what the file currently holds (true
    right after an apply); the undo apply is verified like any other apply.
    """
    plan: list[tuple[str, list[FlagChange]]] = []
    for entry in data.get("entries", []):
        changes = []
        for before, after in zip(entry["before"], entry["after"]):
            track = Track(
                number=before["number"], uid=None, kind=TrackKind(before["kind"]),
                codec=before.get("codec", "?"), language=before.get("language", "und"),
                name=before.get("name", ""),
                enabled=after["enabled"], default=after["default"], forced=after["forced"],
            )
            changes.append(FlagChange(track=track,
                                      after=(before["enabled"], before["default"], before["forced"])))
        plan.append((entry["path"], changes))
    return plan
