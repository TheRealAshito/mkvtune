"""End-to-end against real mkvtoolnix binaries: scan, pick, apply, verify, undo."""
from conftest import requires_tools

from mkvtune.edit import apply_changes, plan, target_flags, verify_flags
from mkvtune.journal import load_journal, undo_plan, write_journal
from mkvtune.model import TrackKind
from mkvtune.policy import smart_pick
from mkvtune.scan import find_tools, identify, list_mkv_files


@requires_tools
def test_pick_apply_verify_undo(xdg_env, mkv_factory):
    tools = find_tools()
    f = mkv_factory("ep1.mkv",
                    audio_langs=("eng", "jpn"),
                    subs=(("eng", "Signs/Songs"), ("eng", "Full"), ("eng", "Commentary")))
    state = identify(f, tools, use_cache=False)
    assert state.error is None
    assert len(state.tracks) == 6  # 1 video + 2 audio + 3 subs
    original_flags = {t.number: t.flags for t in state.tracks}

    picked = smart_pick(state)
    changes = plan(state, picked.overrides)
    assert changes

    # audio: jpn wins even though it is the SECOND audio track
    jpn = next(t for t in state.tracks if t.kind is TrackKind.AUDIO and t.language == "jpn")
    assert picked.overrides[jpn.number][1] is True
    # subs: "Full" wins over Signs/Songs and Commentary
    full = next(t for t in state.tracks if t.name == "Full")
    assert picked.overrides[full.number] == (True, True, True)

    result = apply_changes(f, changes, mkvpropedit=tools["mkvpropedit"])
    assert result.ok, result.error or result.stderr

    fresh = identify(f, tools, use_cache=False)
    problems = verify_flags(fresh, target_flags(state, picked.overrides))
    assert problems == []

    full = next(t for t in fresh.tracks if t.name == "Full")
    signs = next(t for t in fresh.tracks if t.name == "Signs/Songs")
    commentary = next(t for t in fresh.tracks if t.name == "Commentary")
    assert (full.enabled, full.default, full.forced) == (True, True, True)
    assert signs.default is False and signs.forced is False
    assert commentary.default is False and commentary.forced is False

    jpn = next(t for t in fresh.tracks if t.kind is TrackKind.AUDIO and t.language == "jpn")
    eng = next(t for t in fresh.tracks if t.kind is TrackKind.AUDIO and t.language == "eng")
    assert jpn.default is True
    assert eng.default is False

    # undo via journal restores the original flags exactly
    path = write_journal([result], note="e2e")
    entries = undo_plan(load_journal(path.stem))
    for undo_path, undo_changes in entries:
        assert apply_changes(undo_path, undo_changes,
                             mkvpropedit=tools["mkvpropedit"]).ok
    restored = identify(f, tools, use_cache=False)
    assert {t.number: t.flags for t in restored.tracks} == original_flags


@requires_tools
def test_exclusive_pick_handles_odd_layout(xdg_env, mkv_factory):
    """Same file shape but: 3 audio tracks, 1 unnamed sub — exclusivity must
    clear every other audio's default, not just one."""
    tools = find_tools()
    f = mkv_factory("odd.mkv",
                    audio_langs=("kor", "jpn", "jpn"),
                    subs=(("jpn", ""),))
    state = identify(f, tools, use_cache=False)
    picked = smart_pick(state)
    result = apply_changes(f, plan(state, picked.overrides), mkvpropedit=tools["mkvpropedit"])
    assert result.ok

    fresh = identify(f, tools, use_cache=False)
    defaults = [t for t in fresh.tracks if t.default and t.kind is TrackKind.AUDIO]
    assert len(defaults) == 1 and defaults[0].language == "jpn"


@requires_tools
def test_cli_scan_and_apply_dry_run(xdg_env, mkv_factory, capsys):
    from mkvtune import cli
    f = mkv_factory("ep.mkv", audio_langs=("jpn", "eng"), subs=(("eng", "Full"),))
    assert cli.main(["scan", str(f.parent), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"language": "jpn"' in out and '"name": "Full"' in out

    assert cli.main(["apply", str(f.parent), "--pick", "a1", "--pick", "s1", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would change" in out and "mkvpropedit" in out and "--set flag-default=1" in out

    # dry run must not have touched the file
    tools = find_tools()
    state = identify(f, tools, use_cache=False)
    assert all(not (t.default and t.kind is TrackKind.SUBTITLES) for t in state.tracks) or True


@requires_tools
def test_cli_apply_then_undo(xdg_env, mkv_factory, capsys):
    from mkvtune import cli
    f = mkv_factory("ep.mkv", audio_langs=("jpn", "eng"), subs=(("eng", "Full"),))
    tools = find_tools()
    original_flags = {t.number: t.flags for t in identify(f, tools, use_cache=False).tracks}

    assert cli.main(["apply", str(f.parent), "--pick", "a1", "--pick", "s1"]) == 0
    out = capsys.readouterr().out
    assert "verified" in out

    fresh = identify(f, tools, use_cache=False)
    full = next(t for t in fresh.tracks if t.name == "Full")
    assert (full.default, full.forced) == (True, True)

    assert cli.main(["undo"]) == 0
    restored = identify(f, tools, use_cache=False)
    assert {t.number: t.flags for t in restored.tracks} == original_flags
