"""File discovery and track scanning via ``mkvmerge -J``."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import config
from .model import FileTracks, Track, TrackKind

log = logging.getLogger("mkvtune.scan")

TOOL_ENV = "MKVTUNE_MKVTOOLNIX"
CACHE_FORMAT = 1


class ToolNotFound(RuntimeError):
    pass


def find_tool(name: str) -> str:
    """Locate an mkvtoolnix binary: env override first, then PATH."""
    override = os.environ.get(TOOL_ENV)
    if override:
        base = Path(override).expanduser()
        candidate = base / name if base.is_dir() else base
        if candidate.exists():
            return str(candidate)
        raise ToolNotFound(f"{TOOL_ENV} points at {base} but {name} is not there")
    found = shutil.which(name)
    if found:
        return found
    raise ToolNotFound(f"{name} not found in PATH — install mkvtoolnix")


def find_tools() -> dict[str, str]:
    return {name: find_tool(name) for name in ("mkvmerge", "mkvpropedit")}


def list_mkv_files(directory: str | Path) -> list[Path]:
    """All .mkv files directly inside ``directory`` (non-recursive, like the
    original ``for arquivo in *.mkv`` loop)."""
    d = Path(directory).expanduser().resolve()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".mkv")


def parse_identify(path: str | Path, data: dict, *, size: int = 0, mtime: float = 0.0) -> FileTracks:
    """Turn ``mkvmerge -J`` JSON into a :class:`FileTracks`."""
    tracks: list[Track] = []
    for raw in data.get("tracks", []):
        props = raw.get("properties") or {}
        try:
            kind = TrackKind.from_mkvmerge(raw.get("type", ""))
        except ValueError:
            log.debug("skipping unknown track type in %s: %r", path, raw.get("type"))
            continue
        number = props.get("number")
        if number is None:
            number = int(raw.get("id", 0)) + 1  # track id order is 0-based
        tracks.append(Track(
            number=int(number),
            uid=props.get("track_uid"),
            kind=kind,
            codec=raw.get("codec") or props.get("codec_id") or "?",
            language=(props.get("language") or props.get("language_ietf") or "und"),
            name=props.get("track_name") or "",
            enabled=bool(props.get("enabled_track", True)),
            default=bool(props.get("default_track", False)),
            forced=bool(props.get("forced_track", False)),
        ))
    return FileTracks(path=str(path), size=size, mtime=mtime, tracks=tuple(tracks))


def identify(path: str | Path, tools: dict[str, str] | None = None, *, use_cache: bool = True) -> FileTracks:
    """Scan one file's tracks. Never raises on per-file errors: a failed scan
    comes back as ``FileTracks(error=...)`` so batch flows can keep going."""
    tools = tools or find_tools()
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        return FileTracks(path=str(p), size=0, mtime=0.0, tracks=(), error=str(exc))

    if use_cache:
        cached = _load_cache(p, st)
        if cached is not None:
            log.debug("cache hit: %s", p.name)
            return cached

    cmd = [tools["mkvmerge"], "-J", str(p)]
    log.debug("run: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        return FileTracks(path=str(p), size=st.st_size, mtime=st.st_mtime, tracks=(), error=str(exc))

    # mkvmerge exit codes: 0 = fine, 1 = warnings, 2 = errors
    if proc.returncode >= 2:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return FileTracks(path=str(p), size=st.st_size, mtime=st.st_mtime, tracks=(),
                          error=detail[-1] if detail else f"mkvmerge exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return FileTracks(path=str(p), size=st.st_size, mtime=st.st_mtime, tracks=(),
                          error=f"unparseable mkvmerge -J output: {exc}")

    state = parse_identify(p, data, size=st.st_size, mtime=st.st_mtime)
    if use_cache:
        _save_cache(p, st, state)
    return state


# --- cache ---------------------------------------------------------------

def _cache_key(p: Path, st: os.stat_result) -> str:
    raw = f"{p.resolve()}:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(p: Path, st: os.stat_result) -> Path:
    return config.cache_dir() / f"{_cache_key(p, st)}.json"


def _load_cache(p: Path, st: os.stat_result) -> FileTracks | None:
    path = _cache_path(p, st)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("format") != CACHE_FORMAT:
        return None
    try:
        tracks = tuple(
            Track(
                number=t["number"], uid=t.get("uid"), kind=TrackKind(t["kind"]),
                codec=t.get("codec", "?"), language=t.get("language", "und"), name=t.get("name", ""),
                enabled=t["enabled"], default=t["default"], forced=t["forced"],
            )
            for t in data["tracks"]
        )
        return FileTracks(path=data["path"], size=data["size"], mtime=data["mtime"],
                          tracks=tracks, error=data.get("error"))
    except (KeyError, TypeError, ValueError):
        return None


def _save_cache(p: Path, st: os.stat_result, state: FileTracks) -> None:
    payload = {
        "format": CACHE_FORMAT,
        "path": state.path, "size": state.size, "mtime": state.mtime,
        "error": state.error,
        "tracks": [
            {"number": t.number, "uid": t.uid, "kind": t.kind.value, "codec": t.codec,
             "language": t.language, "name": t.name,
             "enabled": t.enabled, "default": t.default, "forced": t.forced}
            for t in state.tracks
        ],
    }
    try:
        _cache_path(p, st).write_text(json.dumps(payload))
    except OSError as exc:
        log.debug("cache write failed: %s", exc)


def clear_cache() -> int:
    """Drop all cached scans; returns how many were removed."""
    count = 0
    for f in config.cache_dir().glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
