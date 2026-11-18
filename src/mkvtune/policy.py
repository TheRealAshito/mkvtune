"""Pick semantics: exclusive default picking and smart preselection.

The core rule (user requirement): picking a track makes it THE one — it gets
default (and forced, for subtitles), and every other track of the same type
gets its default and forced flags explicitly cleared, so nothing else can
hijack the player's track selection. mkvpropedit does NOT do this clearing
automatically (unlike mkvmerge), so we always spell it out.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config
from .model import FileTracks, Overrides, Track, TrackKind


@dataclass(frozen=True)
class PickPolicy:
    """Parameters for the automatic preselection ("smart pick")."""
    name: str = "anime-standard"
    audio_langs: tuple[str, ...] = ("jpn", "ja", "jap")
    sub_langs: tuple[str, ...] = ("eng",)
    sub_name_exclude: str = r"(?i)(sign|song|lyric|credit|opening|ending|karaoke|comment|forced|\bop\b|\bed\b)"
    sub_name_include: str = r"(?i)(full|dialog)"
    force_picked_subs: bool = True
    disable_other_subs: bool = False
    disable_other_audio: bool = False

    @classmethod
    def from_settings(cls, settings: config.Settings) -> "PickPolicy":
        return cls(
            audio_langs=tuple(settings.audio_langs),
            sub_langs=tuple(settings.sub_langs),
            force_picked_subs=settings.force_picked_subs,
            disable_other_subs=settings.disable_other_subs,
            disable_other_audio=settings.disable_other_audio,
        )


BUILTIN_POLICIES: dict[str, PickPolicy] = {
    "anime-standard": PickPolicy(),
    "anime-no-force": PickPolicy(name="anime-no-force", force_picked_subs=False),
    "anime-disable-others": PickPolicy(name="anime-disable-others", disable_other_subs=True),
}


def pick_overrides(state: FileTracks, kind: TrackKind, chosen_number: int, *,
                   also_force: bool = True, exclusive: bool = True,
                   disable_others: bool = False) -> Overrides:
    """Exclusive pick: chosen track wins, every other track of that kind has
    its default (and forced) flags cleared. ``also_force`` only affects
    subtitles — forced audio is meaningless for playback selection."""
    overrides: Overrides = {}
    force_on_pick = also_force and kind is TrackKind.SUBTITLES
    for track in state.tracks_of(kind):
        if track.number == chosen_number:
            overrides[track.number] = (True, True, True if force_on_pick else None)
        elif exclusive:
            overrides[track.number] = (False if disable_others else None, False, False)
    return overrides


@dataclass
class SmartPickResult:
    overrides: Overrides
    notes: list[str]


def smart_pick(state: FileTracks, policy: PickPolicy | None = None) -> SmartPickResult:
    """Preselect Japanese audio + full-dialogue subtitles. This only *proposes*
    (fills the pending plan) — the user reviews and can override before apply."""
    policy = policy or PickPolicy()
    notes: list[str] = []
    overrides: Overrides = {}

    audio = state.tracks_of(TrackKind.AUDIO)
    if audio:
        wanted = {lang.lower() for lang in policy.audio_langs}
        chosen = next((t for t in audio if t.language.lower() in wanted), None)
        if chosen is None:
            chosen = audio[0]
            notes.append(f"audio -> #{chosen.number} ({chosen.language}) — no preferred-language audio, fell back")
        else:
            notes.append(f"audio -> #{chosen.number} ({chosen.language}{_name(chosen)})")
        overrides.update(pick_overrides(state, TrackKind.AUDIO, chosen.number,
                                        also_force=False,
                                        disable_others=policy.disable_other_audio))
    else:
        notes.append("audio -> none found")

    subs = state.tracks_of(TrackKind.SUBTITLES)
    if subs:
        include = re.compile(policy.sub_name_include) if policy.sub_name_include else None
        exclude = re.compile(policy.sub_name_exclude) if policy.sub_name_exclude else None
        wanted = {lang.lower() for lang in policy.sub_langs}

        def score(track: Track) -> float:
            value = 0.0
            if track.language.lower() in wanted:
                value += 2
            if include and include.search(track.name):
                value += 2
            if exclude and exclude.search(track.name):
                value -= 3
            if not track.name:
                value += 1  # unnamed subtitle tracks are usually full dialogue
            return value

        ranked = sorted(subs, key=lambda t: (-score(t), t.number))
        chosen = ranked[0]
        notes.append(f"subs -> #{chosen.number} ({chosen.language}{_name(chosen)})")
        overrides.update(pick_overrides(state, TrackKind.SUBTITLES, chosen.number,
                                        also_force=policy.force_picked_subs,
                                        disable_others=policy.disable_other_subs))
    else:
        notes.append("subs -> none found")

    return SmartPickResult(overrides=overrides, notes=notes)


def _name(track: Track) -> str:
    return f' "{track.name}"' if track.name else ""


# --- policy presets on disk ----------------------------------------------

def list_policies() -> dict[str, PickPolicy]:
    found = dict(BUILTIN_POLICIES)
    for path in sorted(config.policy_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        try:
            policy = PickPolicy(
                name=data.get("name", path.stem),
                audio_langs=tuple(data.get("audio_langs", PickPolicy.audio_langs)),
                sub_langs=tuple(data.get("sub_langs", PickPolicy.sub_langs)),
                sub_name_exclude=data.get("sub_name_exclude", PickPolicy.sub_name_exclude),
                sub_name_include=data.get("sub_name_include", PickPolicy.sub_name_include),
                force_picked_subs=bool(data.get("force_picked_subs", True)),
                disable_other_subs=bool(data.get("disable_other_subs", False)),
                disable_other_audio=bool(data.get("disable_other_audio", False)),
            )
        except (TypeError, ValueError):
            continue
        found[policy.name] = policy
    return found


def load_policy(name: str) -> PickPolicy | None:
    return list_policies().get(name)
