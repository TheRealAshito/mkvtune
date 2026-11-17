from mkvtune.edit import apply_changes, build_command, plan, verify_flags
from mkvtune.model import FileTracks, FlagChange, Track, TrackKind


def make_track(number=2, **kw) -> Track:
    defaults = dict(uid=1, kind=TrackKind.SUBTITLES, codec="SubRip/SRT",
                    language="eng", name="Full", enabled=True, default=False, forced=False)
    defaults.update(kw)
    return Track(number=number, **defaults)


def test_build_command_sets_only_changed_flags():
    change = FlagChange(track=make_track(), after=(True, True, True))
    cmd = build_command("f.mkv", [change], "mkvpropedit")
    assert cmd == ["mkvpropedit", "f.mkv", "--edit", "track:@2",
                   "--set", "flag-default=1", "--set", "flag-forced=1"]


def test_build_command_multiple_tracks():
    changes = [
        FlagChange(track=make_track(number=3, kind=TrackKind.AUDIO, codec="AAC"),
                   after=(True, True, False)),
        FlagChange(track=make_track(number=4), after=(False, False, False)),
    ]
    cmd = build_command("f.mkv", changes, "/usr/bin/mkvpropedit")
    assert cmd == ["/usr/bin/mkvpropedit", "f.mkv",
                   "--edit", "track:@3", "--set", "flag-default=1",
                   "--edit", "track:@4", "--set", "flag-enabled=0"]


def test_apply_changes_success_and_failure():
    class Ok:
        returncode, stdout, stderr = 0, "done", ""

    class Fail:
        returncode, stdout, stderr = 2, "", "boom"

    calls = []

    def ok_runner(cmd, **kw):
        calls.append(cmd)
        return Ok()

    def fail_runner(cmd, **kw):
        return Fail()

    change = FlagChange(track=make_track(), after=(True, True, True))
    result = apply_changes("f.mkv", [change], runner=ok_runner)
    assert result.ok and result.error is None and calls[0][0] == "mkvpropedit"

    result = apply_changes("f.mkv", [change], runner=fail_runner)
    assert not result.ok and "exited 2" in (result.error or "")

    # no changes -> no command run at all
    assert apply_changes("f.mkv", [], runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError())).ok


def test_verify_flags_reports_mismatches():
    state = FileTracks(path="f.mkv", size=0, mtime=0, tracks=(
        make_track(number=2, default=False, forced=False),
        make_track(number=3, default=False, forced=False),
    ))
    problems = verify_flags(state, {2: (True, True, True), 3: (True, False, False), 9: (True, True, True)})
    assert len(problems) == 2
    assert any("track 2" in p for p in problems)
    assert any("track 9" in p for p in problems)

    assert verify_flags(state, {2: (True, False, False)}) == []


def test_plan_counts_changes():
    state = FileTracks(path="f.mkv", size=0, mtime=0, tracks=(make_track(number=2),))
    assert len(plan(state, {2: (None, True, True)})) == 1
    assert plan(state, {2: (True, False, False)}) == []
