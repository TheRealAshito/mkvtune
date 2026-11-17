"""TUI smoke test through Textual's headless pilot: real files, real apply."""
import asyncio
import time

import pytest

from conftest import requires_tools


@requires_tools
def test_tui_scan_pick_preview_apply(xdg_env, mkv_factory, monkeypatch):
    from textual.widgets import DataTable, Static

    from mkvtune import config
    from mkvtune.policy import pick_overrides  # noqa: F401  (documented semantics)
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import ConfirmScreen, MkvtuneApp, PreviewScreen

    f = mkv_factory("ep.mkv", audio_langs=("jpn", "eng"),
                    subs=(("eng", "Signs/Songs"), ("eng", "Full")))

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(140, 40)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)

            files = app.query_one("#files", DataTable)
            tracks = app.query_one("#tracks", DataTable)
            assert files.row_count == 1
            assert tracks.row_count == 5

            # auto-pick fills a proposal (pending), nothing applied yet
            await pilot.press("g")
            assert app.pending, "smart pick produced no pending changes"

            # preview shows the dry-run diff
            await pilot.press("P")
            assert isinstance(app.screen, PreviewScreen)
            await pilot.press("escape")

            # explicit apply via confirm modal
            await pilot.press("exclamation_mark")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")

            text = ""
            for _ in range(100):
                await pilot.pause(0.05)
                text = str(app.query_one("#status", Static).content)
                if "verified" in text or "FAILED" in text or "VERIFY" in text:
                    break
            assert "verified" in text, f"status never confirmed: {text!r}"

            # apply cleared the pending plan
            assert not app.pending

        return text

    text = asyncio.run(scenario())
    assert "1 file(s)" in text or "verified" in text


@requires_tools
def test_tui_manual_pick_semantics(xdg_env, mkv_factory):
    from textual.widgets import DataTable

    from mkvtune import config
    from mkvtune.scan import find_tools, identify
    from mkvtune.tui.app import MkvtuneApp

    f = mkv_factory("ep.mkv", audio_langs=("jpn", "eng"),
                    subs=(("eng", "Signs/Songs"), ("eng", "Full")))

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(140, 40)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)

            tracks = app.query_one("#tracks", DataTable)
            # move to the "Signs/Songs" row (4th row) and pick it manually
            tracks.move_cursor(row=3)
            await pilot.pause(0.05)
            await pilot.press("p")

            state = app.states[str(f)]
            overrides = app.pending[str(f)]
            signs = next(t for t in state.tracks if t.name == "Signs/Songs")
            full = next(t for t in state.tracks if t.name == "Full")
            assert overrides[signs.number] == (True, True, True)   # chosen wins
            assert overrides[full.number] == (None, False, False)  # rival cleared

    asyncio.run(scenario())


@requires_tools
def test_tui_theme_toggle_and_quit(xdg_env, mkv_factory):
    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp

    f = mkv_factory("ep.mkv")

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            await pilot.press("t")
            assert app.screen.has_class("amoled")
            assert config.load_settings().theme == "amoled"
            await pilot.press("t")
            assert not app.screen.has_class("amoled")

    asyncio.run(scenario())


def test_tui_empty_folder(xdg_env):
    import tempfile
    from pathlib import Path

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            app = MkvtuneApp(tmp, settings=config.load_settings(), tools=find_tools())
            async with app.run_test(size=(120, 30)) as pilot:
                deadline = time.monotonic() + 30
                while not app.scan_done.is_set() and time.monotonic() < deadline:
                    await pilot.pause(0.05)
                await pilot.press("g")
                await pilot.press("P")
                assert not app.pending

    asyncio.run(scenario())
