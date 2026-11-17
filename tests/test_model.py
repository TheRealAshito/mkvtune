from mkvtune.model import (
    FileTracks, FlagChange, Track, TrackKind, diff_flags, effective_flags,
    merge_flags, target_flags,
)


def make_track(number: int = 2, kind: TrackKind = TrackKind.SUBTITLES, **kw) -> Track:
    defaults = dict(uid=1, codec="SubRip/SRT", language="eng", name="Full",
                    enabled=True, default=False, forced=False)
    defaults.update(kw)
    return Track(number=number, kind=kind, **defaults)


def test_selector_is_track_number():
    assert make_track(number=7).selector == "track:@7"


def test_merge_flags_none_keeps_before():
    assert merge_flags((True, False, True), (None, True, None)) == (True, True, True)


def test_effective_flags_without_override():
    track = make_track()
    assert effective_flags(track, None) == track.flags
    assert effective_flags(track, (False, None, None)) == (False, False, False)


def test_diff_flags_skips_noops_and_missing():
    state = FileTracks(path="f.mkv", size=0, mtime=0, tracks=(
        make_track(number=1, default=False),
        make_track(number=2, default=False),
    ))
    overrides = {
        1: (None, None, None),          # noop
        2: (None, True, None),          # real change
        9: (True, True, True),          # track doesn't exist — ignored
    }
    changes = diff_flags(state, overrides)
    assert len(changes) == 1
    assert changes[0].track.number == 2
    assert changes[0].before == (True, False, False)
    assert changes[0].after == (True, True, False)
    assert changes[0].summary() == "D:0->1"


def test_target_flags_covers_noops_too():
    state = FileTracks(path="f.mkv", size=0, mtime=0, tracks=(make_track(number=1),))
    assert target_flags(state, {1: (None, True, None)}) == {1: (True, True, False)}


def test_change_summary():
    change = FlagChange(track=make_track(), after=(False, True, True))
    assert change.summary() == "E:1->0 D:0->1 F:0->1"
