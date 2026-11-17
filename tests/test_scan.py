from mkvtune.model import TrackKind
from mkvtune.scan import parse_identify

IDENTIFY = {
    "tracks": [
        {"type": "video", "codec": "AVC/H.264/MPEG-4p10", "id": 0, "properties": {
            "number": 1, "track_uid": 111, "default_track": True,
            "enabled_track": True, "forced_track": False}},
        {"type": "audio", "codec": "AAC", "id": 1, "properties": {
            "number": 2, "track_uid": 222, "language": "jpn", "track_name": "Japanese",
            "default_track": True, "enabled_track": True, "forced_track": False}},
        {"type": "subtitles", "codec": "SubRip/SRT", "id": 2, "properties": {
            "number": 3, "track_uid": 333, "language": "eng", "track_name": "Full",
            "default_track": False, "enabled_track": False, "forced_track": True}},
        {"type": "buttons", "codec": "B_VOBBTN", "id": 3, "properties": {"number": 4}},
        {"type": "audio", "codec": "AC-3", "id": 4, "properties": {}},
    ]
}


def test_parse_identify_tracks():
    state = parse_identify("/x/f.mkv", IDENTIFY, size=5, mtime=7.0)
    assert [t.kind for t in state.tracks] == [TrackKind.VIDEO, TrackKind.AUDIO,
                                             TrackKind.SUBTITLES, TrackKind.AUDIO]
    video, audio, subs, audio2 = state.tracks

    assert video.language == "und"          # no language property -> und
    assert video.name == ""
    assert (video.enabled, video.default, video.forced) == (True, True, False)

    assert audio.number == 2 and audio.uid == 222
    assert audio.language == "jpn" and audio.name == "Japanese"
    assert audio.codec == "AAC"

    assert (subs.enabled, subs.default, subs.forced) == (False, False, True)

    # number falls back to track id + 1 when properties.number is missing
    assert audio2.number == 5
    assert audio2.language == "und"


def test_parse_identify_empty():
    state = parse_identify("/x/f.mkv", {"tracks": []})
    assert state.tracks == () and state.ok


def test_flags_tristate_defaults():
    state = parse_identify("/x/f.mkv", {"tracks": [
        {"type": "audio", "codec": "AAC", "id": 0, "properties": {"number": 1}},
    ]})
    assert state.tracks[0].flags == (True, False, False)
