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
    from mkvtune.tui.app import THEME_ORDER, MkvtuneApp

    f = mkv_factory("ep.mkv")

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            assert app.theme == THEME_ORDER[0]
            for expected in THEME_ORDER[1:]:
                await pilot.press("t")
                assert app.theme == expected
                assert config.load_settings().theme == expected
            await pilot.press("t")
            assert app.theme == THEME_ORDER[0]  # cycles around

    asyncio.run(scenario())


def test_theme_palettes_are_visually_distinct():
    from mkvtune.tui.app import THEME_ORDER, THEME_PALETTES, THEMES

    assert set(THEMES) == {"dark", "amoled", "bw", "bw-light", "htop", "mc", "paper"}
    assert set(THEME_ORDER) == set(THEMES)
    assert set(THEME_PALETTES) == set(THEMES)
    assert THEMES["amoled"].background == "#000000"
    assert THEMES["bw"].background == "#000000"
    assert THEMES["bw"].foreground == "#ffffff"
    assert THEMES["bw"].primary == "#ffffff"
    assert THEMES["bw-light"].background == "#ffffff"
    assert THEMES["bw-light"].foreground == "#000000"
    assert THEMES["bw-light"].dark is False
    assert THEMES["htop"].background == "#000000"
    assert THEMES["htop"].primary == "#00ffff"
    assert THEMES["mc"].background == "#0000aa"
    assert THEMES["paper"].dark is False
    assert all(t.dark for name, t in THEMES.items() if name not in ("paper", "bw-light"))

    # Strict monochrome palettes must contain no chromatic color names or hexes
    for mono in ("bw", "bw-light"):
        pal = THEME_PALETTES[mono]
        for field_val in vars(pal).values():
            for bad in ("cyan", "green", "yellow", "red", "blue", "#6cb6ff", "#00e5ff"):
                assert bad not in field_val, f"{mono} palette leaked color {bad!r}: {field_val!r}"


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


@requires_tools
def test_tui_survives_keys_on_empty_tables(xdg_env, mkv_factory):
    """Regression (v0.1.1): DataTable emits RowHighlighted(row_key=None) with
    cursor_row=-1 when a table is empty or mid-clear. On slow machines keys
    arrive during the scan window and crashed the handler. run_test re-raises
    any app exception on exit, so 'survives' is asserted by finishing."""
    import tempfile

    from textual.widgets import DataTable

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp

    f = mkv_factory("ep.mkv")

    async def hammer(app, expect_rows):
        async with app.run_test(size=(120, 30)) as pilot:
            # BEFORE the scan finishes: tables may still be empty
            for key in ("down", "up", "enter", "down", "p", "e", "d", "f", "m", "g"):
                await pilot.press(key)
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            # AFTER load: refresh cycle (clear + re-add) + more cursor keys
            await pilot.press("m")
            await pilot.press("g")
            for key in ("down", "up", "enter", "p"):
                await pilot.press(key)
            assert app.query_one("#files", DataTable).row_count == expect_rows

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        await hammer(app, expect_rows=1)

        with tempfile.TemporaryDirectory() as tmp:
            empty = MkvtuneApp(tmp, settings=config.load_settings(), tools=find_tools())
            await hammer(empty, expect_rows=0)

    asyncio.run(scenario())


def test_row_highlighted_none_key_guard():
    """Deterministic version of the v0.1.1 regression: the handlers must
    tolerate DataTable events carrying row_key=None (empty/mid-clear tables)."""
    from types import SimpleNamespace

    from mkvtune.tui.app import MkvtuneApp, UndoScreen

    app = MkvtuneApp(".")
    app._file_highlighted(SimpleNamespace(row_key=None))        # must not raise
    app._file_highlighted(SimpleNamespace(row_key=SimpleNamespace(value=None)))

    undo = UndoScreen([])
    undo._pick(SimpleNamespace(row_key=None))                   # must not raise


@requires_tools
def test_tui_scope_modal_applies_to_matching_episodes(xdg_env, mkv_factory):
    """v0.2 season flow: picking a track asks about scope and can fill every
    episode that carries the same track (type/language/name/position)."""
    from textual.widgets import DataTable

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp, _ScopeScreen

    layout = dict(audio_langs=("jpn", "eng"),
                  subs=(("eng", "Signs/Songs"), ("eng", "Full")))
    f1 = mkv_factory("ep1.mkv", **layout)
    f2 = mkv_factory("ep2.mkv", **layout)

    async def scenario():
        app = MkvtuneApp(f1.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(140, 40)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            assert app.query_one("#files", DataTable).row_count == 2

            tracks = app.query_one("#tracks", DataTable)
            tracks.move_cursor(row=4)          # the "Full" sub (5th track)
            await pilot.pause(0.05)
            await pilot.press("p")
            assert isinstance(app.screen, _ScopeScreen), "scope question missing"
            await pilot.press("a")             # all matching episodes
            await pilot.pause(0.1)

            assert len(app.pending) == 2, "pick did not propagate to both episodes"
            for path in app.pending:
                state = app.states[path]
                full = next(t for t in state.tracks if t.name == "Full")
                signs = next(t for t in state.tracks if t.name == "Signs/Songs")
                assert app.pending[path][full.number] == (True, True, True)
                assert app.pending[path][signs.number] == (None, False, False)

    asyncio.run(scenario())


@requires_tools
def test_tui_menu_dropdown_and_browser(xdg_env, mkv_factory):
    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp, _MenuScreen

    f = mkv_factory("ep.mkv")

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)

            # browser is open by default in v0.3
            assert app.query_one("#browser").display is True

            # F9 opens the File dropdown; escape closes it
            await pilot.press("f9")
            await pilot.pause(0.1)
            assert isinstance(app.screen, _MenuScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, _MenuScreen)

            # Enter on the first menu item (Browse…) dispatches its action
            await pilot.press("f9")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.query_one("#browser").display is False  # toggled off

            # F3 toggles the browser pane
            await pilot.press("f3")
            assert app.query_one("#browser").display is True
            await pilot.press("f3")
            assert app.query_one("#browser").display is False

    asyncio.run(scenario())


@requires_tools
def test_tui_goto_folder(xdg_env, mkv_factory, tmp_path):
    from textual.widgets import DataTable, Input

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp, _GotoScreen

    sub = tmp_path / "season2"
    sub.mkdir()
    f = mkv_factory("ep.mkv")
    f.rename(sub / "ep.mkv")

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            assert app.query_one("#files", DataTable).row_count == 0

            await pilot.press("ctrl+g")
            await pilot.pause(0.2)
            assert isinstance(app.screen, _GotoScreen)
            app.screen.query_one("#goto-input", Input).value = str(sub)
            await pilot.press("enter")

            for _ in range(100):
                await pilot.pause(0.05)
                if app.query_one("#files", DataTable).row_count == 1:
                    break
            assert app.query_one("#files", DataTable).row_count == 1
            assert app.directory == sub.resolve()

    asyncio.run(scenario())


@requires_tools
def test_tui_browser_defaults_and_modes(xdg_env, mkv_factory):
    """v0.3: browser open by default in mc panel mode; tree mode (rooted at
    /) stays available via the View menu."""
    from pathlib import Path

    from textual.widgets import DirectoryTree

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp, _BrowserPanel

    f = mkv_factory("ep.mkv")

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            assert app.query_one("#browser").display is True
            assert app.query_one("#panel", _BrowserPanel).display is True
            assert app.query_one("#tree", DirectoryTree).display is False

            app.action_browser_mode()
            assert app.query_one("#panel", _BrowserPanel).display is False
            tree = app.query_one("#tree", DirectoryTree)
            assert tree.display is True
            assert Path(tree.path) == Path("/")

    asyncio.run(scenario())


@requires_tools
def test_tui_panel_navigates_like_mc(xdg_env, mkv_factory, tmp_path):
    """mc panel behavior: one folder's contents + ".."; Enter enters a
    folder (and loads its episodes), backspace goes up."""
    from textual.widgets import DataTable

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp, _BrowserPanel

    sub = tmp_path / "season2"
    sub.mkdir()
    f = mkv_factory("ep.mkv")
    f.rename(sub / "ep.mkv")

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(120, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            panel = app.query_one("#panel", _BrowserPanel)
            assert panel.cwd == tmp_path.resolve()
            assert panel.children[0].kind == "up"          # ".." on top
            assert any(c.kind == "dir" and c.entry.name == "season2" for c in panel.children)

            panel.focus()
            await pilot.press("down")                      # highlight season2/
            await pilot.press("enter")                     # enter + load episodes
            for _ in range(100):
                await pilot.pause(0.05)
                if app.query_one("#files", DataTable).row_count == 1:
                    break
            assert app.query_one("#files", DataTable).row_count == 1
            assert app.directory == sub.resolve()
            assert panel.cwd == sub.resolve()
            assert panel.children[0].kind == "up"

            await pilot.press("backspace")                 # go up, no reload
            assert panel.cwd == tmp_path.resolve()

    asyncio.run(scenario())


@requires_tools
def test_tui_long_names_truncate_keep_tail(xdg_env, mkv_factory):
    """Fansub names carry the episode number at the end — truncation must
    keep the tail visible (v0.2 layout complaint)."""
    from textual.widgets import DataTable

    from mkvtune import config
    from mkvtune.scan import find_tools
    from mkvtune.tui.app import MkvtuneApp

    long_name = ("[SubsPlease] Some Extremely Long Anime Title That Keeps Going On "
                 "And On Forever - S01E01 (1080p) [ABCD1234].mkv")
    f = mkv_factory(long_name)

    async def scenario():
        app = MkvtuneApp(f.parent, settings=config.load_settings(), tools=find_tools())
        async with app.run_test(size=(80, 30)) as pilot:
            deadline = time.monotonic() + 30
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.05)
            row = app.query_one("#files", DataTable).get_row_at(0)
            label = getattr(row[1], "plain", str(row[1]))
            assert label != long_name, "name was not truncated"
            assert "…" in label
            assert label.endswith(".mkv"), f"tail lost: {label!r}"
            assert len(label) <= 36

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Deterministic headless TUI tests (mocked mkvmerge/mkvpropedit, no binaries needed)
# ---------------------------------------------------------------------------

def _fake_mkv_dir(tmp_path, count=2):
    from mkvtune.model import FileTracks, Track, TrackKind

    store = {}
    for i in range(1, count + 1):
        p = tmp_path / f"Show - 0{i} [1080p].mkv"
        p.write_bytes(b"fake")
        store[str(p)] = FileTracks(
            path=str(p), size=4, mtime=float(i),
            tracks=(
                Track(1, 10, TrackKind.VIDEO, "AVC", "und", "", True, True, False),
                Track(2, 20, TrackKind.AUDIO, "AAC", "eng", "English", True, True, False),
                Track(3, 30, TrackKind.AUDIO, "AAC", "jpn", "Japanese", True, False, False),
                Track(4, 40, TrackKind.SUBTITLES, "ASS", "eng", "Signs", True, False, False),
                Track(5, 50, TrackKind.SUBTITLES, "ASS", "eng", "Full", True, False, False),
            ),
        )
    return store


def _patch_fake_tools(monkeypatch, store):
    from mkvtune.edit import ApplyResult
    from mkvtune.model import FileTracks, merge_flags
    import mkvtune.tui.app as tui_app

    def fake_identify(path, tools=None, *, use_cache=True):
        return store[str(path)]

    def fake_apply(path, changes, *, mkvpropedit="mkvpropedit"):
        state = store[str(path)]
        by_num = {c.track.number: c.after for c in changes}
        updated = []
        for t in state.tracks:
            if t.number in by_num:
                e, d, f = by_num[t.number]
                updated.append(type(t)(
                    t.number, t.uid, t.kind, t.codec, t.language, t.name, e, d, f))
            else:
                updated.append(t)
        store[str(path)] = FileTracks(state.path, state.size, state.mtime, tuple(updated))
        return ApplyResult(path=str(path), ok=True, cmd=[mkvpropedit, str(path)], changes=list(changes))

    monkeypatch.setattr(tui_app, "identify", fake_identify)
    monkeypatch.setattr(tui_app, "apply_changes", fake_apply)
    return {"mkvmerge": "fake-mkvmerge", "mkvpropedit": "fake-mkvpropedit"}


def test_tui_bw_theme_and_picker_no_color_leak(xdg_env, tmp_path, monkeypatch):
    from textual.widgets import DataTable, Static
    from mkvtune import config
    from mkvtune.tui.app import MkvtuneApp, _ThemeScreen, flag_text, theme_palette

    store = _fake_mkv_dir(tmp_path, count=1)
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 30)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            # Open theme picker via T and select "bw"
            await pilot.press("T")
            assert isinstance(app.screen, _ThemeScreen)
            opt = app.screen.query_one("#theme-options")
            opt.highlighted = 2  # bw
            await pilot.press("enter")
            await pilot.pause(0.05)
            assert app.theme == "bw"
            assert config.load_settings().theme == "bw"

            # Trigger pending change so keybar/infobar/chips/flags render in bw
            await pilot.press("g")
            await pilot.pause(0.05)

            keybar_markup = str(app.query_one("#keybar", Static).content)
            infobar_markup = str(app.query_one("#infobar", Static).content)
            for chromatic in ("#6cb6ff", "#00e5ff", "cyan", "green", "yellow"):
                assert chromatic not in keybar_markup
                assert chromatic not in infobar_markup

            files = app.query_one("#files", DataTable)
            row = files.get_row_at(0)
            assert "cyan" not in str(row[2].style)
            assert "green" not in str(row[3].style)
            assert flag_text(True, changed=True, palette=theme_palette("bw")).style == "bold reverse"

    asyncio.run(scenario())


def test_tui_pick_disable_others_and_clear_pending(xdg_env, tmp_path, monkeypatch):
    from textual.widgets import DataTable
    from mkvtune import config
    from mkvtune.tui.app import MkvtuneApp, _ScopeScreen

    store = _fake_mkv_dir(tmp_path, count=2)
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 30)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            tracks = app.query_one("#tracks", DataTable)
            # Highlight audio #3 (jpn) and press X -> pick + disable others across all episodes
            tracks.move_cursor(row=2)
            await pilot.pause(0.02)
            await pilot.press("X")
            assert isinstance(app.screen, _ScopeScreen)
            await pilot.press("a")
            await pilot.pause(0.05)

            assert len(app.pending) == 2
            for ov in app.pending.values():
                assert ov[3] == (True, True, None)   # jpn chosen
                assert ov[2] == (False, False, False)  # eng audio disabled!

            # c clears only current episode; C clears all
            await pilot.press("c")
            await pilot.pause(0.02)
            assert len(app.pending) == 1
            await pilot.press("C")
            await pilot.pause(0.02)
            assert len(app.pending) == 0

    asyncio.run(scenario())


def test_tui_async_apply_undo_verify_and_escape(xdg_env, tmp_path, monkeypatch):
    from textual.widgets import Static
    from mkvtune import config
    from mkvtune.tui.app import ConfirmScreen, MkvtuneApp, UndoScreen

    store = _fake_mkv_dir(tmp_path, count=2)
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 30)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            await pilot.press("G")
            assert len(app.pending) == 2

            # Unsaved guard on quit/rescan: cancelling keeps pending edits
            await pilot.press("q")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("n")
            assert len(app.pending) == 2

            # Apply in background worker
            await pilot.press("exclamation_mark")
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")

            deadline = time.monotonic() + 10
            while not app.apply_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.02)
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.02)
            await pilot.pause(0.05)

            assert not app.pending
            status_text = str(app.query_one("#status", Static).content)
            assert "all verified" in status_text

            # Verify flags actually changed in fake store
            for st in store.values():
                assert st.find(3).default is True
                assert st.find(5).default is True and st.find(5).forced is True

            # UndoScreen closes with Escape
            await pilot.press("u")
            assert isinstance(app.screen, UndoScreen)
            await pilot.press("escape")
            assert not isinstance(app.screen, UndoScreen)

            # Reopen UndoScreen and restore first journal
            await pilot.press("u")
            assert isinstance(app.screen, UndoScreen)
            await pilot.press("enter")

            deadline = time.monotonic() + 10
            while not app.undo_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.02)
            while not app.scan_done.is_set() and time.monotonic() < deadline:
                await pilot.pause(0.02)
            await pilot.pause(0.05)

            undo_status = str(app.query_one("#status", Static).content)
            assert "all verified" in undo_status
            for st in store.values():
                assert st.find(2).default is True
                assert st.find(3).default is False

    asyncio.run(scenario())


def test_tui_settings_modal_saves_and_sets_policy(xdg_env, tmp_path, monkeypatch):
    from textual.widgets import Checkbox, Input, Select
    from mkvtune import config
    from mkvtune.tui.app import MkvtuneApp, _SettingsScreen

    store = _fake_mkv_dir(tmp_path, count=1)
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 35)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            await pilot.press("s")
            assert isinstance(app.screen, _SettingsScreen)
            app.screen.query_one("#policy-select", Select).value = "anime-disable-others"
            app.screen.query_one("#audio-langs", Input).value = "jpn, kor"
            app.screen.query_one("#disable-audio", Checkbox).value = True
            await pilot.click("#save")
            await pilot.pause(0.05)

            assert app.active_policy == "anime-disable-others"
            saved = config.load_settings()
            assert saved.audio_langs == ("jpn", "kor")
            assert saved.disable_other_audio is True

    asyncio.run(scenario())


def test_tui_theme_cycle_all_and_preview_quoting(xdg_env, tmp_path, monkeypatch):
    from textual.widgets import Static
    from mkvtune import config
    from mkvtune.tui.app import THEME_ORDER, MkvtuneApp, PreviewScreen

    store = _fake_mkv_dir(tmp_path, count=1)
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 30)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            assert app.theme == THEME_ORDER[0]
            for expected in THEME_ORDER[1:]:
                await pilot.press("t")
                assert app.theme == expected
            await pilot.press("t")
            assert app.theme == THEME_ORDER[0]

            await pilot.press("g")
            await pilot.press("P")
            assert isinstance(app.screen, PreviewScreen)
            preview_text = str(app.screen.query_one(".modal-text", Static).content)
            # Filename contains spaces and brackets so shlex.join must shell-quote it
            assert "'" in preview_text and "Show - 01 [1080p].mkv" in preview_text

    asyncio.run(scenario())


def test_tui_scope_relaxed_matching(xdg_env, tmp_path, monkeypatch):
    from mkvtune.model import FileTracks, Track, TrackKind
    from mkvtune import config
    from mkvtune.tui.app import MkvtuneApp, _ScopeScreen

    p1 = tmp_path / "ep1.mkv"
    p2 = tmp_path / "ep2.mkv"
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    store = {
        str(p1): FileTracks(str(p1), 1, 1.0, (
            Track(1, 10, TrackKind.VIDEO, "AVC", "und", "", True, True, False),
            Track(2, 20, TrackKind.SUBTITLES, "ASS", "eng", "Full", True, False, False),
        )),
        # ep2 has same kind+lang+name ("Full") but different codec ("SRT") and ordinal
        str(p2): FileTracks(str(p2), 1, 2.0, (
            Track(1, 10, TrackKind.VIDEO, "AVC", "und", "", True, True, False),
            Track(2, 20, TrackKind.SUBTITLES, "ASS", "eng", "Signs", True, False, False),
            Track(3, 30, TrackKind.SUBTITLES, "SRT", "eng", "Full", True, False, False),
        )),
    }
    tools = _patch_fake_tools(monkeypatch, store)

    async def scenario():
        app = MkvtuneApp(tmp_path, settings=config.load_settings(), tools=tools)
        async with app.run_test(size=(120, 30)) as pilot:
            while not app.scan_done.is_set():
                await pilot.pause(0.02)

            tracks = app.query_one("#tracks")
            tracks.move_cursor(row=1)  # Full subtitle on ep1
            await pilot.pause(0.02)
            await pilot.press("p")
            assert isinstance(app.screen, _ScopeScreen)
            await pilot.press("a")
            await pilot.pause(0.05)
            assert len(app.pending) == 2

    asyncio.run(scenario())
