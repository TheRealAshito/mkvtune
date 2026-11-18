import pytest

from mkvtune import cli

parser = cli.build_parser


def test_scan_routing():
    args = parser().parse_args(["scan", "/x", "--json"])
    assert args.func is cli.cmd_scan and args.directory == "/x" and args.json


def test_apply_routing_with_picks():
    args = parser().parse_args(["apply", "/x", "--pick", "a1", "--pick", "s2", "--dry-run"])
    assert args.func is cli.cmd_apply
    assert args.pick == ["a1", "s2"] and args.dry_run


def test_tui_routing():
    args = parser().parse_args(["tui", "/anime"])
    assert args.func is cli.cmd_tui and args.directory == "/anime"


def test_bare_directory_opens_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_tui", lambda args: seen.update(dir=args.directory) or 0)
    assert cli.main(["/some/dir"]) == 0
    assert seen["dir"] == "/some/dir"


def test_no_args_defaults_to_cwd_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_tui", lambda args: seen.update(dir=args.directory) or 0)
    assert cli.main([]) == 0
    assert seen["dir"] == "."


def test_invalid_pick_rejected():
    with pytest.raises(SystemExit, match="invalid --pick"):
        cli._parse_picks(["bogus"], [])


def test_parse_picks_respects_per_kind_disable_settings(xdg_env):
    from mkvtune import config
    from mkvtune.model import FileTracks, Track, TrackKind

    s = config.Settings(disable_other_subs=True, disable_other_audio=False)
    config.save_settings(s)

    state = FileTracks(
        path="/x/ep.mkv", size=0, mtime=0.0,
        tracks=(
            Track(1, 1, TrackKind.AUDIO, "AAC", "jpn", "", True, False, False),
            Track(2, 2, TrackKind.AUDIO, "AAC", "eng", "", True, False, False),
            Track(3, 3, TrackKind.SUBTITLES, "SRT", "eng", "Full", True, False, False),
            Track(4, 4, TrackKind.SUBTITLES, "SRT", "eng", "Signs", True, False, False),
        ),
    )

    picks = cli._parse_picks(["a1", "s1"], [state])
    ov = picks["/x/ep.mkv"]
    assert ov[2] == (None, False, False)   # other audio NOT disabled
    assert ov[4] == (False, False, False)  # other sub disabled

    forced_disable = cli._parse_picks(["a1", "s1"], [state], disable_others=True)
    ov2 = forced_disable["/x/ep.mkv"]
    assert ov2[2] == (False, False, False)  # other audio disabled when explicitly requested


def test_apply_parser_accepts_disable_others():
    args = parser().parse_args(["apply", "/x", "--pick", "a1", "--disable-others"])
    assert args.disable_others is True
