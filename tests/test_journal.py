from mkvtune.edit import ApplyResult
from mkvtune.journal import list_journals, load_journal, undo_plan, write_journal
from mkvtune.model import FlagChange, Track, TrackKind


def make_change() -> FlagChange:
    before = Track(number=3, uid=1, kind=TrackKind.SUBTITLES, codec="SubRip/SRT",
                   language="eng", name="Full", enabled=True, default=False, forced=False)
    return FlagChange(track=before, after=(True, True, True))


def test_journal_roundtrip(xdg_env):
    change = make_change()
    result = ApplyResult(path="/x/f.mkv", ok=True, cmd=["mkvpropedit", "/x/f.mkv"],
                         changes=[change])
    path = write_journal([result], note="unit test")
    assert path is not None and path.exists()

    assert len(list_journals()) == 1
    data = load_journal(path.stem)
    assert data["note"] == "unit test"
    assert data["entries"][0]["path"] == "/x/f.mkv"

    entries = undo_plan(data)
    assert len(entries) == 1
    undo_path, changes = entries[0]
    assert undo_path == "/x/f.mkv"
    # undo targets the ORIGINAL flags; current (post-apply) state is the "before"
    assert changes[0].after == (True, False, False)
    assert changes[0].before == (True, True, True)
    assert changes[0].track.selector == "track:@3"


def test_journal_skips_failed_results(xdg_env):
    failed = ApplyResult(path="/x/f.mkv", ok=False, cmd=[], changes=[make_change()],
                         error="exit 2")
    assert write_journal([failed]) is None
    assert write_journal([]) is None
    assert list_journals() == []


def test_load_journal_by_full_path(xdg_env):
    change = make_change()
    path = write_journal([ApplyResult(path="/x/f.mkv", ok=True, cmd=[], changes=[change])])
    data = load_journal(path)
    assert data["id"] == path.stem
