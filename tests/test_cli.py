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
