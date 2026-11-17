"""Core data model for MKV track state: tracks, flags, and flag diffs."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

# (enabled, default, forced)
Flags = tuple[bool, bool, bool]
# Same triple but None means "leave this flag untouched"
PartialFlags = tuple[bool | None, bool | None, bool | None]
# track number -> partial flags to apply
Overrides = dict[int, PartialFlags]

FLAG_NAMES = ("flag-enabled", "flag-default", "flag-forced")


class TrackKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLES = "subtitles"

    @classmethod
    def from_mkvmerge(cls, raw: str) -> "TrackKind":
        mapping = {"video": cls.VIDEO, "audio": cls.AUDIO, "subtitles": cls.SUBTITLES}
        try:
            return mapping[raw]
        except KeyError:
            raise ValueError(f"unknown track type: {raw!r}")


@dataclass(frozen=True)
class Track:
    number: int          # Matroska track number (mkvpropedit selector track:@N)
    uid: int | None
    kind: TrackKind
    codec: str
    language: str
    name: str
    enabled: bool
    default: bool
    forced: bool

    @property
    def flags(self) -> Flags:
        return (self.enabled, self.default, self.forced)

    @property
    def selector(self) -> str:
        return f"track:@{self.number}"

    def describe(self) -> str:
        name = f' "{self.name}"' if self.name else ""
        return f"{self.kind.value} #{self.number} {self.codec} {self.language}{name}"


@dataclass(frozen=True)
class FlagChange:
    """A pending or applied change of one track's flags."""
    track: Track
    after: Flags

    @property
    def before(self) -> Flags:
        return self.track.flags

    @property
    def is_noop(self) -> bool:
        return self.before == self.after

    def summary(self) -> str:
        parts = []
        for label, b, a in zip("EDF", self.before, self.after):
            if b != a:
                parts.append(f"{label}:{int(b)}->{int(a)}")
        return " ".join(parts) if parts else "no change"


@dataclass(frozen=True)
class FileTracks:
    """The scan result for one file."""
    path: str
    size: int
    mtime: float
    tracks: tuple[Track, ...]
    error: str | None = None

    def tracks_of(self, kind: TrackKind) -> tuple[Track, ...]:
        return tuple(t for t in self.tracks if t.kind is kind)

    def find(self, number: int) -> Track | None:
        for t in self.tracks:
            if t.number == number:
                return t
        return None

    @property
    def ok(self) -> bool:
        return self.error is None


def merge_flags(before: Flags, after: PartialFlags) -> Flags:
    """Fill None entries in ``after`` with the values from ``before``."""
    return tuple(b if a is None else a for b, a in zip(before, after))  # type: ignore[return-value]


def effective_flags(track: Track, override: PartialFlags | None) -> Flags:
    return merge_flags(track.flags, override or (None, None, None))


def diff_flags(state: FileTracks, overrides: Overrides) -> list[FlagChange]:
    """Changes needed to move ``state`` to the overridden flags (skipping noops)."""
    changes = []
    for track in state.tracks:
        override = overrides.get(track.number)
        if override is None:
            continue
        merged = merge_flags(track.flags, override)
        if merged != track.flags:
            changes.append(FlagChange(track=track, after=merged))
    return changes


def target_flags(state: FileTracks, overrides: Overrides) -> dict[int, Flags]:
    """Full flag triples every overridden track should end up with."""
    out: dict[int, Flags] = {}
    for track in state.tracks:
        override = overrides.get(track.number)
        if override is not None:
            out[track.number] = merge_flags(track.flags, override)
    return out
