"""mkvtune — a TUI for MKV audio/subtitle track flag management.

Wraps mkvtoolnix (mkvmerge -J for scanning, mkvpropedit for in-place header
edits). No remuxing, no re-encoding, no network access, no telemetry.
"""
__version__ = "0.2.1"

from .model import (  # noqa: F401
    FileTracks, FlagChange, Flags, Overrides, PartialFlags, Track, TrackKind,
    diff_flags, effective_flags, merge_flags, target_flags,
)
