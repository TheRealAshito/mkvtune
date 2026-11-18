from mkvtune.model import FileTracks, Track, TrackKind
from mkvtune.policy import PickPolicy, pick_overrides, smart_pick


def make_track(number, kind, lang="und", name="", **kw) -> Track:
    defaults = dict(uid=number, codec="X", language=lang, name=name,
                    enabled=True, default=False, forced=False)
    defaults.update(kw)
    return Track(number=number, kind=kind, **defaults)


def state_with(*tracks) -> FileTracks:
    return FileTracks(path="f.mkv", size=0, mtime=0, tracks=tuple(tracks))


ANIME = state_with(
    make_track(1, TrackKind.VIDEO),
    make_track(2, TrackKind.AUDIO, "eng", "English Dub"),
    make_track(3, TrackKind.AUDIO, "jpn", "Japanese"),
    make_track(4, TrackKind.SUBTITLES, "eng", "Signs/Songs"),
    make_track(5, TrackKind.SUBTITLES, "eng", "Full"),
    make_track(6, TrackKind.SUBTITLES, "eng", "Commentary"),
)


class TestPickOverrides:
    def test_sub_pick_is_exclusive_with_forced(self):
        overrides = pick_overrides(ANIME, TrackKind.SUBTITLES, 5)
        assert overrides[5] == (True, True, True)      # chosen: enabled, default, forced
        assert overrides[4] == (None, False, False)    # others: default+forced cleared
        assert overrides[6] == (None, False, False)

    def test_sub_pick_without_force(self):
        overrides = pick_overrides(ANIME, TrackKind.SUBTITLES, 5, also_force=False)
        assert overrides[5] == (True, True, None)

    def test_audio_pick_leaves_forced_alone(self):
        overrides = pick_overrides(ANIME, TrackKind.AUDIO, 3)
        assert overrides[3] == (True, True, None)
        assert overrides[2] == (None, False, False)

    def test_disable_others(self):
        overrides = pick_overrides(ANIME, TrackKind.SUBTITLES, 5, disable_others=True)
        assert overrides[4] == (False, False, False)

    def test_video_pick_does_nothing(self):
        assert pick_overrides(ANIME, TrackKind.VIDEO, 1) == {1: (True, True, None)}


class TestSmartPick:
    def test_picks_jpn_audio_even_when_second(self):
        result = smart_pick(ANIME)
        audio_wins = [n for n, f in result.overrides.items()
                      if f[1] and ANIME.find(n).kind is TrackKind.AUDIO]
        assert audio_wins == [3]
        assert any("audio -> #3" in note for note in result.notes)

    def test_picks_full_subs_over_signs_and_commentary(self):
        result = smart_pick(ANIME)
        sub_wins = [n for n, f in result.overrides.items()
                    if f[1] and ANIME.find(n).kind is TrackKind.SUBTITLES]
        assert sub_wins == [5]
        assert result.overrides[5] == (True, True, True)

    def test_unnamed_sub_beats_signs(self):
        state = state_with(
            make_track(1, TrackKind.SUBTITLES, "eng", "Signs"),
            make_track(2, TrackKind.SUBTITLES, "eng", ""),
        )
        result = smart_pick(state)
        assert [n for n, f in result.overrides.items() if f[1]] == [2]

    def test_language_bonus(self):
        state = state_with(
            make_track(1, TrackKind.SUBTITLES, "por", "Full"),
            make_track(2, TrackKind.SUBTITLES, "eng", "Full"),
        )
        result = smart_pick(state)
        assert [n for n, f in result.overrides.items() if f[1]] == [2]

    def test_falls_back_when_no_preferred_audio(self):
        state = state_with(make_track(1, TrackKind.AUDIO, "kor"))
        result = smart_pick(state)
        assert result.overrides[1][1] is True
        assert any("fell back" in note for note in result.notes)

    def test_no_audio_no_sub_notes(self):
        result = smart_pick(state_with(make_track(1, TrackKind.VIDEO)))
        assert result.overrides == {}
        assert "none found" in " ".join(result.notes)

    def test_only_signs_still_picks_one(self):
        state = state_with(make_track(1, TrackKind.SUBTITLES, "eng", "Signs/Songs"))
        result = smart_pick(state)
        assert 1 in result.overrides

    def test_policy_disable_other_subs(self):
        policy = PickPolicy(disable_other_subs=True)
        result = smart_pick(ANIME, policy)
        assert result.overrides[4] == (False, False, False)
        assert result.overrides[2] == (None, False, False)  # audio unaffected

    def test_policy_disable_other_audio(self):
        policy = PickPolicy(disable_other_audio=True)
        result = smart_pick(ANIME, policy)
        assert result.overrides[2] == (False, False, False)
        assert result.overrides[3] == (True, True, None)
