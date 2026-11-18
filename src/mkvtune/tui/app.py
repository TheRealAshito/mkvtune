"""The mkvtune Textual TUI — v0.2 interface.

Layout follows Midnight Commander's bones (top pull-down menus, function-key
bar, in-app file browser, modal progress dialogs) with duf-style visual
density: colored flag dots, per-episode audio/sub chips, an info bar of
counts.

Interaction model (unchanged semantics): *you* are the one picking. Auto-pick
(g/G) only fills a pending plan you can edit; nothing touches a file until
preview (F4) and explicit apply (F5). Picking a track offers season-wide
scope: apply to every episode with a matching track, since picking subtitles
across 60+ episodes one file at a time is the exact chore this tool exists to
kill.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape as rich_escape
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button, Checkbox, DataTable, DirectoryTree, Input, Label, ListItem, ListView,
    OptionList, ProgressBar, Select, Static,
)
from textual.widgets._option_list import Option

from .. import config
from .. import journal as journal_mod
from .. import policy as policy_mod
from ..edit import apply_changes, build_command, plan, target_flags, verify_flags
from ..model import (
    FileTracks, FlagChange, Overrides, PartialFlags, Track, TrackKind,
    effective_flags,
)
from ..scan import ToolNotFound, clear_cache, find_tools, identify, list_mkv_files

log = logging.getLogger("mkvtune.tui")

FLAG_INDEX = {"e": 0, "d": 1, "f": 2}

MENUS: dict[str, list[tuple[str, str, str]]] = {
    "file": [
        ("Browse files…", "F3", "file.browse"),
        ("Go to folder…", "ctrl+g", "file.goto"),
        ("Rescan", "r", "file.rescan"),
        ("Quit", "F10", "file.quit"),
    ],
    "pick": [
        ("Pick highlighted track as default", "F2", "pick.pick"),
        ("Pick + disable others", "X", "pick.pick_disable"),
        ("Toggle enabled", "e", "pick.enabled"),
        ("Toggle default", "d", "pick.default"),
        ("Toggle forced", "f", "pick.forced"),
        ("Auto-pick this episode", "g", "pick.smart"),
        ("Clear pending (this episode)", "c", "pick.clear"),
    ],
    "batch": [
        ("Auto-pick all episodes", "G", "batch.smart_all"),
        ("Clear all pending", "C", "batch.clear_all"),
        ("Preview pending changes", "F4", "batch.preview"),
        ("Apply pending changes", "F5", "batch.apply"),
        ("Undo last apply", "F8", "batch.undo"),
        ("Policy & settings…", "s", "batch.settings"),
    ],
    "view": [
        ("Toggle file browser", "F3", "view.browser"),
        ("Browser: mc panel / tree", "", "view.browser_mode"),
        ("Next theme", "t", "view.theme"),
        ("Select theme…", "T", "view.theme_pick"),
    ],
    "help": [
        ("Keys", "F1", "help.keys"),
        ("Diagnostics", "D", "help.diag"),
    ],
}

MENU_TITLES = {"file": "File", "pick": "Pick", "batch": "Batch",
               "view": "View", "help": "Help"}

THEMES: dict[str, Theme] = {
    "dark": Theme(
        name="dark", primary="#6cb6ff", secondary="#4ec9b0", warning="#e5c07b",
        error="#f26d78", success="#4ec9b0", accent="#bb9af7",
        foreground="#d7dee7", background="#101418", surface="#161c24",
        panel="#1b222c", dark=True),
    "amoled": Theme(
        name="amoled", primary="#00e5ff", secondary="#7dffb2", warning="#ffd166",
        error="#ff5470", success="#00e59a", accent="#ff4fd8",
        foreground="#e8fcff", background="#000000", surface="#000000",
        panel="#0a0a0c", dark=True),
    "bw": Theme(
        name="bw", primary="#ffffff", secondary="#b0b0b0", warning="#ffffff",
        error="#ffffff", success="#ffffff", accent="#ffffff",
        foreground="#ffffff", background="#000000", surface="#000000",
        panel="#121212", boost="#1c1c1c", dark=True, luminosity_spread=0.08,
        variables={
            "block-cursor-background": "#ffffff",
            "block-cursor-foreground": "#000000",
            "block-cursor-text-style": "bold",
            "scrollbar": "#555555",
            "scrollbar-hover": "#b0b0b0",
            "scrollbar-active": "#ffffff",
            "border": "#777777",
            "footer-key-foreground": "#ffffff",
        }),
    "bw-light": Theme(
        name="bw-light", primary="#000000", secondary="#4a4a4a", warning="#000000",
        error="#000000", success="#000000", accent="#000000",
        foreground="#000000", background="#ffffff", surface="#ffffff",
        panel="#eaeaea", boost="#dedede", dark=False, luminosity_spread=0.08,
        variables={
            "block-cursor-background": "#000000",
            "block-cursor-foreground": "#ffffff",
            "block-cursor-text-style": "bold",
            "scrollbar": "#999999",
            "scrollbar-hover": "#444444",
            "scrollbar-active": "#000000",
            "border": "#666666",
            "footer-key-foreground": "#000000",
        }),
    "htop": Theme(
        name="htop", primary="#00ffff", secondary="#00ff00", warning="#ffff00",
        error="#ff5555", success="#00ff00", accent="#00ffff",
        foreground="#ffffff", background="#000000", surface="#000000",
        panel="#121212", boost="#1a1a1a", dark=True,
        variables={
            "block-cursor-background": "#008787",
            "block-cursor-foreground": "#ffffff",
            "block-cursor-text-style": "bold",
            "scrollbar": "#008787",
            "scrollbar-hover": "#00ffff",
            "scrollbar-active": "#00ff00",
        }),
    "mc": Theme(
        name="mc", primary="#00aaaa", secondary="#00aaaa", warning="#ffff55",
        error="#ff5555", success="#55ff55", accent="#55ffff",
        foreground="#ffffff", background="#0000aa", surface="#0000aa",
        panel="#000000", dark=True,
        variables={
            "block-cursor-background": "#00aaaa",
            "block-cursor-foreground": "#000000",
            "block-cursor-text-style": "bold",
        }),
    "paper": Theme(
        name="paper", primary="#2456a6", secondary="#2c7a5b", warning="#b0790a",
        error="#b2333c", success="#2c7a5b", accent="#7a3fa8",
        foreground="#26221b", background="#f4f1ea", surface="#ffffff",
        panel="#e7e1d5", dark=False),
}

THEME_ORDER = ["dark", "amoled", "bw", "bw-light", "htop", "mc", "paper"]

THEME_LABELS = {
    "dark": "dark      — Slate dark with blue/teal accents",
    "amoled": "amoled    — Pure black with neon accents",
    "bw": "bw        — Strict black & white (zero color)",
    "bw-light": "bw-light  — Strict white & black (zero color)",
    "htop": "htop      — Black with cyan/green/yellow htop palette",
    "mc": "mc        — Classic Midnight Commander blue",
    "paper": "paper     — Warm light paper",
}


@dataclass(frozen=True)
class ThemePalette:
    key: str
    dir: str
    ok: str
    pending: str
    dim: str
    audio: str
    sub: str
    flag_on: str
    flag_changed: str
    flag_off: str


THEME_PALETTES: dict[str, ThemePalette] = {
    "dark": ThemePalette(
        key="bold #6cb6ff", dir="bold #6cb6ff", ok="green",
        pending="bold yellow", dim="dim", audio="bold cyan", sub="bold green",
        flag_on="bold green", flag_changed="bold yellow", flag_off="dim",
    ),
    "amoled": ThemePalette(
        key="bold #00e5ff", dir="bold #00e5ff", ok="#00e59a",
        pending="bold #ffd166", dim="dim", audio="bold #00e5ff", sub="bold #00e59a",
        flag_on="bold #00e59a", flag_changed="bold #ffd166", flag_off="dim",
    ),
    "bw": ThemePalette(
        key="bold reverse", dir="bold #ffffff", ok="bold #ffffff",
        pending="bold reverse", dim="dim", audio="bold #ffffff", sub="bold #ffffff",
        flag_on="bold #ffffff", flag_changed="bold reverse", flag_off="dim",
    ),
    "bw-light": ThemePalette(
        key="bold reverse", dir="bold #000000", ok="bold #000000",
        pending="bold reverse", dim="dim", audio="bold #000000", sub="bold #000000",
        flag_on="bold #000000", flag_changed="bold reverse", flag_off="dim",
    ),
    "htop": ThemePalette(
        key="bold #00ffff", dir="bold #00ffff", ok="bold #00ff00",
        pending="bold #ffff00", dim="dim", audio="bold #00ffff", sub="bold #00ff00",
        flag_on="bold #00ff00", flag_changed="bold #ffff00", flag_off="dim",
    ),
    "mc": ThemePalette(
        key="bold #55ffff", dir="bold #55ffff", ok="bold #55ff55",
        pending="bold #ffff55", dim="dim", audio="bold #55ffff", sub="bold #55ff55",
        flag_on="bold #55ff55", flag_changed="bold #ffff55", flag_off="dim",
    ),
    "paper": ThemePalette(
        key="bold #2456a6", dir="bold #2456a6", ok="bold #2c7a5b",
        pending="bold #b0790a", dim="dim", audio="bold #2456a6", sub="bold #2c7a5b",
        flag_on="bold #2c7a5b", flag_changed="bold #b0790a", flag_off="dim",
    ),
}


def theme_palette(theme_name: str) -> ThemePalette:
    return THEME_PALETTES.get(theme_name, THEME_PALETTES["dark"])


def middle_truncate(name: str, width: int) -> str:
    """Keep the head and the TAIL of a filename — fansub names carry the
    episode number at the end, which is what the eye hunts for."""
    if len(name) <= width:
        return name
    if width < 8:
        return name[:width]
    head = max(4, int(width * 0.4))
    tail = width - head - 1
    return name[:head] + "…" + name[-tail:]


def flag_text(value: bool, changed: bool = False,
              palette: ThemePalette | None = None) -> Text:
    pal = palette or THEME_PALETTES["dark"]
    if changed:
        return Text("●", style=pal.flag_changed)
    return Text("●", style=pal.flag_on) if value else Text("○", style=pal.flag_off)


class _MenuItem(Static):
    """A menu-bar title that opens its dropdown on click."""

    def on_click(self) -> None:
        key = self.id.split("-", 1)[1]
        self.app._open_menu(key, x=self.region.x)  # type: ignore[attr-defined]


class _Entry(ListItem):
    """One line of the mc-style browser panel."""

    def __init__(self, label: str, entry: Path, kind: str) -> None:
        super().__init__(Label(label), classes=f"panel-{kind}")
        self.entry = entry
        self.kind = kind  # "up" | "dir" | "file"


class _BrowserPanel(ListView):
    """mc-style browser: the contents of ONE folder plus a ".." entry to go
    up — never the whole tree. Talks to the app directly on selection."""

    BINDINGS = [Binding("backspace", "go_up", "Up")]

    def __init__(self, path: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cwd = path

    def on_mount(self) -> None:
        self.show_dir(self.cwd)

    def show_dir(self, path: Path) -> None:
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            return
        self.cwd = path
        self.clear()
        if path.parent != path:
            self.append(_Entry("../", path.parent, "up"))
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                self.append(_Entry(f"{entry.name}/", entry, "dir"))
            elif entry.is_file():
                self.append(_Entry(entry.name, entry, "file"))
        if self.index is not None and len(self.children) > 0:  # keep cursor sane
            self.index = 0

    def action_go_up(self) -> None:
        if self.cwd.parent != self.cwd:
            self.show_dir(self.cwd.parent)

    @on(ListView.Selected)
    def _entry_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, _Entry):
            return
        if item.kind == "up":
            self.show_dir(item.entry)
        elif item.kind == "dir":
            self.show_dir(item.entry)
            self.app._panel_folder_entered(item.entry)  # type: ignore[attr-defined]
        else:
            self.app._panel_file_picked(item.entry)  # type: ignore[attr-defined]


class MkvtuneApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "mkvtune"

    BINDINGS = [
        Binding("f1", "help", "Help"),
        Binding("f2", "pick", "Pick"),
        Binding("f3", "browse", "Browse"),
        Binding("f4", "preview", "Preview"),
        Binding("f5", "apply", "Apply"),
        Binding("f8", "undo", "Undo"),
        Binding("f9", "menu", "Menu"),
        Binding("f10", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
        Binding("p", "pick", "Pick default"),
        Binding("X", "pick_disable_others", "Pick + disable others"),
        Binding("e", "toggle_enabled", "Enable"),
        Binding("d", "toggle_default", "Default"),
        Binding("f", "toggle_forced", "Forced"),
        Binding("g", "smart_pick", "Auto-pick file"),
        Binding("G", "smart_pick_all", "Auto-pick all"),
        Binding("c", "clear_current", "Clear pending"),
        Binding("C", "clear_all", "Clear all pending"),
        Binding("m", "mark", "Mark file"),
        Binding("P", "preview", "Preview"),
        Binding("exclamation_mark", "apply", "Apply"),
        Binding("u", "undo", "Undo"),
        Binding("r", "rescan", "Rescan"),
        Binding("s", "settings", "Settings"),
        Binding("t", "theme", "Theme"),
        Binding("T", "select_theme", "Select theme"),
        Binding("D", "diagnostics", "Diag"),
        Binding("ctrl+g", "goto", "Go to folder"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, directory: str | Path, settings: config.Settings | None = None,
                 tools: dict[str, str] | None = None) -> None:
        super().__init__()
        self.directory = Path(directory).expanduser().resolve()
        self.settings = settings or config.load_settings()
        self.tools = tools or find_tools()
        self.states: dict[str, FileTracks] = {}
        self.order: list[str] = []
        self.pending: dict[str, Overrides] = {}
        self.marked: set[str] = set()
        self.current_file: str | None = None
        self.active_policy: str | None = None
        self._track_rows: list[Track] = []
        self._after_load_status: tuple[str, str] | None = None
        self._scan_screen: "_ProgressScreen | None" = None
        self._op_screen: "_ProgressScreen | None" = None
        self._loaded_width: int = 0
        self.scan_done = threading.Event()
        self.apply_done = threading.Event()
        self.undo_done = threading.Event()

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            for key, title in MENU_TITLES.items():
                yield _MenuItem(f" {title} ", id=f"menu-{key}", classes="menu-item")
            yield Static("mkvtune", id="brand")
        yield Static("", id="infobar")
        with Horizontal(id="body"):
            with Vertical(id="browser"):
                yield Label("BROWSER  (F3 closes)", id="browser-title")
                yield _BrowserPanel(self.directory, id="panel")
                yield DirectoryTree("/", id="tree")
            with Vertical(id="main"):
                yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                with Vertical(id="tracks-wrap"):
                    yield Label("TRACKS", id="tracks-title")
                    yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield Static("", id="keybar", markup=True)
        yield Static("Loading…", id="status")

    def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self.theme = self.settings.theme if self.settings.theme in THEMES else "dark"
        self._render_keybar()
        files = self.query_one("#files", DataTable)
        files.add_columns("✓", "Episode", "Audio", "Sub", "Δ")
        tracks = self.query_one("#tracks", DataTable)
        tracks.add_columns("#", "Type", "Codec", "Lang", "Name", "E", "D", "F")
        self._apply_browser_mode()
        files.focus()  # never let the hidden browser widgets hold initial focus
        self.load_files(show_scan=True)

    def _palette(self) -> ThemePalette:
        name = self.theme if self.theme in THEME_PALETTES else self.settings.theme
        return theme_palette(name)

    def _render_keybar(self) -> None:
        k = self._palette().key
        keys = [
            ("F1", "Help"), ("F2", "Pick"), ("X", "Pick+Dis"),
            ("F3", "Browse"), ("F4", "Preview"), ("F5", "Apply"),
            ("F8", "Undo"), ("F10", "Quit"),
        ]
        bar = "  ".join(f"[{k}]{label}[/] {desc}" for label, desc in keys)
        self.query_one("#keybar", Static).update(bar)

    def _apply_theme_refresh(self) -> None:
        self._render_keybar()
        self._refresh_files()
        self._refresh_tracks()

    def _apply_browser_mode(self) -> None:
        mode = self.settings.browser_mode if self.settings.browser_mode in ("panel", "tree") else "panel"
        self.query_one("#panel", _BrowserPanel).display = mode == "panel"
        self.query_one("#tree", DirectoryTree).display = mode == "tree"

    # ----------------------------------------------------------- scanning

    @work(thread=True, exclusive=True, group="scan")
    def load_files(self, show_scan: bool = True) -> None:
        self.scan_done.clear()
        self._scan_screen = None
        files = list_mkv_files(self.directory)

        # The scan dialog appears only if the scan is actually SLOW (first
        # visit); cached re-entries stay flash-free but long scans can never
        # look like a hang.
        def maybe_show() -> None:
            if show_scan and not self.scan_done.is_set() and self._scan_screen is None:
                self._scan_screen = _ScanScreen()
                self.push_screen(self._scan_screen)

        timer = threading.Timer(0.35, lambda: self.call_from_thread(maybe_show))
        timer.daemon = True
        if show_scan:
            timer.start()

        states_by_path: dict[str, FileTracks] = {}
        total = len(files)
        if files:
            workers = min(4, total)
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(identify, path, self.tools, use_cache=True): path
                    for path in files
                }
                for future in as_completed(future_map):
                    path = future_map[future]
                    done += 1
                    try:
                        states_by_path[str(path)] = future.result()
                    except Exception as exc:  # defensive fallback
                        states_by_path[str(path)] = FileTracks(
                            path=str(path), size=0, mtime=0.0, tracks=(), error=str(exc))
                    if self._scan_screen is not None:
                        self.call_from_thread(
                            self._scan_screen.update_progress, done, total, path.name)

        states = {str(p): states_by_path[str(p)] for p in files}

        def finish() -> None:
            timer.cancel()
            self.states = states
            self.order = list(states)
            if self.current_file not in self.states:
                self.current_file = self.order[0] if self.order else None
            if self._scan_screen is not None:
                self._scan_screen.dismiss()
                self._scan_screen = None
            self._refresh_files()
            self._refresh_tracks()
            if self._after_load_status is not None:
                text, cls = self._after_load_status
                self._set_status(text, cls)
                self._after_load_status = None
            elif not self.order:
                self._set_status(f"No .mkv files in {self.directory} — "
                                 f"F3 opens the file browser, ctrl+g jumps to a folder")
            else:
                self._set_status(f"{len(self.order)} episode(s) loaded from {self.directory}")
            self.scan_done.set()

        self.call_from_thread(finish)

    # ---------------------------------------------------------- rendering

    def _name_width(self) -> int:
        return max(24, (self.size.width if self.size.width else 80) - 46)

    def _refresh_files(self) -> None:
        table = self.query_one("#files", DataTable)
        keep = max(0, table.cursor_row or 0)
        table.clear()
        self._loaded_width = self.size.width
        name_width = self._name_width()
        pal = self._palette()
        for path in self.order:
            state = self.states[path]
            mark = Text("●", style=pal.pending) if path in self.marked else Text(" ")
            audio, sub = self._pick_chips(state)
            changes = self._change_count(state)
            table.add_row(
                mark,
                middle_truncate(Path(path).name, name_width),
                audio,
                sub,
                Text(str(changes), style=pal.pending) if changes else Text("—", style=pal.dim),
                key=path,
            )
        if self.order:
            table.move_cursor(row=min(keep, len(self.order) - 1))
        self._refresh_info()

    def _refresh_tracks(self) -> None:
        table = self.query_one("#tracks", DataTable)
        keep = max(0, table.cursor_row or 0)
        table.clear()
        self._track_rows = []
        state = self.states.get(self.current_file or "")
        title = self.query_one("#tracks-title", Label)
        if state is None:
            title.update("TRACKS")
            return
        pal = self._palette()
        overrides = self.pending.get(self.current_file or "", {})
        title.update(f"TRACKS — {middle_truncate(Path(state.path).name, 60)}")
        for track in state.tracks:
            eff = effective_flags(track, overrides.get(track.number))
            changed = eff != track.flags
            self._track_rows.append(track)
            table.add_row(
                f"{'•' if changed else ' '}{track.number}",
                track.kind.value,
                track.codec,
                track.language,
                middle_truncate(track.name or "", 28),
                *(flag_text(v, changed, palette=pal) for v in eff),
                key=str(track.number),
            )
        if self._track_rows:
            table.move_cursor(row=min(keep, len(self._track_rows) - 1))

    def _pick_chips(self, state: FileTracks) -> tuple[Text, Text]:
        overrides = self.pending.get(state.path, {})
        pal = self._palette()

        def winner(kind: TrackKind, style: str) -> Text:
            for track in state.tracks_of(kind):
                eff = effective_flags(track, overrides.get(track.number))
                if eff[1]:
                    label = f"#{track.number} {track.language}"
                    if track.name:
                        label += f" {middle_truncate(track.name, 14)}"
                    return Text(label, style=style)
            return Text("—", style=pal.dim)

        return winner(TrackKind.AUDIO, pal.audio), winner(TrackKind.SUBTITLES, pal.sub)

    def _refresh_info(self) -> None:
        pending_files = sum(1 for p in self.order if self._change_count(self.states[p]))
        pending_changes = sum(self._change_count(self.states[p]) for p in self.order)
        pal = self._palette()
        info = self.query_one("#infobar", Static)
        parts = [
            f"[{pal.dir}]{rich_escape(str(self.directory))}[/]",
            f"[{pal.ok}]✓ scanned[/] {len(self.order)} episodes",
        ]
        if self.active_policy:
            parts.append(f"[{pal.dim}]policy: {rich_escape(self.active_policy)}[/]")
        if pending_changes:
            parts.append(f"[{pal.pending}]● {pending_changes} pending change(s) "
                         f"in {pending_files} file(s)[/]")
        else:
            parts.append(f"[{pal.dim}]no pending changes[/]")
        if self.marked:
            parts.append(f"[{pal.pending}]marked: {len(self.marked)}[/]")
        info.update("   ".join(parts))

    def _change_count(self, state: FileTracks) -> int:
        return len(plan(state, self.pending.get(state.path, {})))

    # ------------------------------------------------------------ helpers

    def _set_status(self, text: str, cls: str = "") -> None:
        status = self.query_one("#status", Static)
        status.update(text)
        status.set_class(cls == "-ok", "-ok")
        status.set_class(cls == "-error", "-error")
        status.set_class(cls == "-busy", "-busy")

    def _current_state(self) -> FileTracks | None:
        return self.states.get(self.current_file or "")

    def _current_track(self) -> Track | None:
        if not self._track_rows:
            return None
        table = self.query_one_optional("#tracks", DataTable)
        if table is None:
            return None
        index = table.cursor_row or 0
        index = max(0, min(index, len(self._track_rows) - 1))
        return self._track_rows[index]

    def _collect_diffs(self) -> list[tuple[str, list[FlagChange]]]:
        diffs = []
        for path in self.order:
            overrides = self.pending.get(path)
            if not overrides:
                continue
            changes = plan(self.states[path], overrides)
            if changes:
                diffs.append((path, changes))
        return diffs

    def _track_signature(self, track: Track, state: FileTracks) -> tuple:
        """Identity of a track ACROSS episodes: type, language, name, codec
        and its position among same-type tracks (the old s1/s2 intuition)."""
        ordinal = sum(1 for t in state.tracks_of(track.kind) if t.number < track.number)
        return (track.kind, track.language, track.name, track.codec, ordinal)

    def _matching_tracks(self, track: Track, state: FileTracks) -> dict[str, Track]:
        sig = self._track_signature(track, state)
        matches: dict[str, Track] = {}
        for path, other in self.states.items():
            candidates = [t for t in other.tracks if self._track_signature(t, other) == sig]
            if len(candidates) == 1:
                matches[path] = candidates[0]
        return matches

    def _relaxed_matching_tracks(self, track: Track, state: FileTracks) -> dict[str, Track]:
        """Fallback match across episodes by (kind, language, name), ignoring codec/ordinal."""
        key = (track.kind, track.language, track.name)
        matches: dict[str, Track] = {}
        for path, other in self.states.items():
            candidates = [t for t in other.tracks if (t.kind, t.language, t.name) == key]
            if len(candidates) == 1:
                matches[path] = candidates[0]
        return matches

    def _confirm_discard(self, verb: str, proceed) -> None:
        diffs = self._collect_diffs()
        if not diffs:
            proceed()
            return
        n_changes = sum(len(c) for _, c in diffs)

        def after(go: bool | None) -> None:
            if go:
                proceed()
            else:
                self._set_status(f"Cancelled — {n_changes} pending change(s) kept", "-busy")

        self.push_screen(
            ConfirmScreen(
                f"Discard {n_changes} pending change(s) in {len(diffs)} episode(s) and {verb}?",
                title="UNSAVED PENDING CHANGES",
                confirm_label="Discard",
            ),
            after,
        )

    # ------------------------------------------------------------- events

    @on(DataTable.RowHighlighted, "#files")
    def _file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # DataTable emits row_key=None on empty tables / mid-clear (cursor -1)
        key = event.row_key.value if event.row_key is not None else None
        if not isinstance(key, str):
            return
        try:
            if key != self.current_file and key in self.states:
                self.current_file = key
                self._refresh_tracks()
        except NoMatches:
            return  # widgets torn down during app shutdown

    @on(DataTable.RowHighlighted, "#tracks")
    def _track_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            track = self._current_track()
            if track is not None:
                override = self.pending.get(self.current_file or "", {}).get(track.number)
                eff = effective_flags(track, override)
                # hover detail lives on the tracks title — #status is reserved for
                # action feedback (apply summaries etc.) and must not be clobbered
                state = self.states.get(self.current_file or "")
                file_part = middle_truncate(Path(state.path).name, 40) if state else ""
                self.query_one("#tracks-title", Label).update(
                    f"TRACKS — {file_part}  ·  {track.describe()}  "
                    f"E/D/F = {'/'.join('on' if v else 'off' for v in eff)}")
        except NoMatches:
            return  # widgets torn down during app shutdown

    def on_resize(self, event: events.Resize) -> None:
        if self.states and self.size.width != self._loaded_width:
            self._refresh_files()

    @on(DirectoryTree.DirectorySelected, "#tree")
    def _browser_dir(self, event: DirectoryTree.DirectorySelected) -> None:
        if not self.query_one("#browser").display:
            return
        self._load_directory(event.path)

    @on(DirectoryTree.FileSelected, "#tree")
    def _browser_file(self, event: DirectoryTree.FileSelected) -> None:
        if not self.query_one("#browser").display:
            return
        self._load_directory(event.path.parent, focus=Path(event.path))

    def _load_directory(self, directory: Path, focus: Path | None = None) -> None:
        directory = Path(directory).expanduser().resolve()
        if not directory.is_dir():
            return

        def do_load() -> None:
            self.directory = directory
            self.pending.clear()
            self.marked.clear()
            self.current_file = str(focus) if focus else None
            self.query_one("#panel", _BrowserPanel).show_dir(directory)
            self._set_status(f"Loading {directory}…", "-busy")
            self.load_files(show_scan=True)

        self._confirm_discard(f"open {directory.name}", do_load)

    def _panel_folder_entered(self, path: Path) -> None:
        self._load_directory(path)

    def _panel_file_picked(self, path: Path) -> None:
        self._load_directory(path.parent, focus=path)

    # ------------------------------------------------------------ actions

    def action_menu(self) -> None:
        self._open_menu("file", x=0)

    def _open_menu(self, key: str, x: int = 0) -> None:
        items = [(f"{label}    {shortcut}", action) for label, shortcut, action in MENUS[key]]

        def done(action: str | None) -> None:
            if action:
                self._dispatch(action)

        self.push_screen(_MenuScreen(MENU_TITLES[key], items, x), done)

    def _dispatch(self, action: str) -> None:
        table = {
            "file.browse": self.action_browse,
            "file.goto": self.action_goto,
            "file.rescan": self.action_rescan,
            "file.quit": self.action_quit,
            "pick.pick": self.action_pick,
            "pick.pick_disable": self.action_pick_disable_others,
            "pick.enabled": self.action_toggle_enabled,
            "pick.default": self.action_toggle_default,
            "pick.forced": self.action_toggle_forced,
            "pick.smart": self.action_smart_pick,
            "pick.clear": self.action_clear_current,
            "batch.smart_all": self.action_smart_pick_all,
            "batch.clear_all": self.action_clear_all,
            "batch.preview": self.action_preview,
            "batch.apply": self.action_apply,
            "batch.undo": self.action_undo,
            "batch.settings": self.action_settings,
            "view.browser": self.action_browse,
            "view.browser_mode": self.action_browser_mode,
            "view.theme": self.action_theme,
            "view.theme_pick": self.action_select_theme,
            "help.keys": self.action_help,
            "help.diag": self.action_diagnostics,
        }
        handler = table.get(action)
        if handler is not None:
            handler()

    def action_browse(self) -> None:
        browser = self.query_one("#browser")
        browser.display = not browser.display
        if browser.display:
            if self.settings.browser_mode == "tree":
                self.query_one("#tree", DirectoryTree).focus()
            else:
                self.query_one("#panel", _BrowserPanel).focus()
            self._set_status("Browser open — Enter enters folders / picks files, "
                             "backspace goes up, F3 closes", "-busy")

    def action_browser_mode(self) -> None:
        self.settings.browser_mode = "tree" if self.settings.browser_mode != "tree" else "panel"
        config.save_settings(self.settings)
        self._apply_browser_mode()
        self._set_status(f"Browser mode: "
                         f"{'mc panel (one folder + ..)' if self.settings.browser_mode == 'panel' else 'tree'}",
                         "-ok")

    def action_goto(self) -> None:
        self.push_screen(_GotoScreen(self.directory), self._goto_confirmed)

    def _goto_confirmed(self, target: str | None) -> None:
        if not target:
            return
        path = Path(target).expanduser()
        if not path.is_dir():
            self._set_status(f"Not a folder: {target}", "-error")
            return
        self._load_directory(path)

    def action_pick(self) -> None:
        self._pick_current(disable_others=None)

    def action_pick_disable_others(self) -> None:
        self._pick_current(disable_others=True)

    def _pick_current(self, *, disable_others: bool | None) -> None:
        state, track = self._current_state(), self._current_track()
        if state is None or track is None:
            self._set_status("Nothing to pick — no track selected", "-busy")
            return
        if track.kind is TrackKind.VIDEO:
            self._set_status("Video tracks don't take part in default/forced picking", "-busy")
            return
        strict = self._matching_tracks(track, state)
        relaxed = self._relaxed_matching_tracks(track, state)
        if len(strict) <= 1 and len(relaxed) <= 1:
            self._apply_pick([(state.path, track)], disable_others=disable_others)
            return
        desc = (f'{track.kind.value} #{track.number} {track.language}'
                f'{f" “{track.name}”" if track.name else ""}')
        skipped = [Path(p).name for p in self.order if p not in strict]
        self.push_screen(
            _ScopeScreen(
                desc,
                strict_count=len(strict),
                relaxed_count=len(relaxed),
                total=len(self.states),
                skipped_names=skipped,
                disable_others=bool(disable_others),
            ),
            lambda scope: self._pick_scope_confirmed(
                scope, strict, relaxed, track, state, disable_others=disable_others))

    def _pick_scope_confirmed(self, scope: str | None, strict: dict[str, Track],
                              relaxed: dict[str, Track], track: Track,
                              state: FileTracks, *,
                              disable_others: bool | None) -> None:
        if scope == "all":
            targets = sorted(strict.items()) if len(strict) > 1 else sorted(relaxed.items())
            self._apply_pick(targets, disable_others=disable_others)
        elif scope == "relaxed":
            self._apply_pick(sorted(relaxed.items()), disable_others=disable_others)
        elif scope == "one":
            self._apply_pick([(state.path, track)], disable_others=disable_others)

    def _apply_pick(self, targets: list[tuple[str, Track]], *,
                    disable_others: bool | None = None) -> None:
        for path, track in targets:
            kind_disable = (
                disable_others
                if disable_others is not None
                else (self.settings.disable_other_subs
                      if track.kind is TrackKind.SUBTITLES
                      else self.settings.disable_other_audio
                      if track.kind is TrackKind.AUDIO else False)
            )
            overrides = policy_mod.pick_overrides(
                self.states[path], track.kind, track.number,
                also_force=self.settings.force_picked_subs,
                disable_others=kind_disable)
            self.pending.setdefault(path, {}).update(overrides)
        self._refresh_files()
        self._refresh_tracks()
        first = targets[0][1]
        force_note = " (+forced)" if (first.kind is TrackKind.SUBTITLES
                                      and self.settings.force_picked_subs) else ""
        dis_note = " (+others disabled)" if disable_others else ""
        where = (f"{len(targets)} episode(s)" if len(targets) > 1
                 else Path(targets[0][0]).name)
        self._set_status(
            f"Picked {first.kind.value} #{first.number}{force_note}{dis_note} as exclusive default "
            f"in {where} — rival defaults cleared. Review with F4, apply with F5.", "-ok")

    def _toggle_flag(self, which: str) -> None:
        state, track = self._current_state(), self._current_track()
        if state is None or track is None:
            self._set_status("No track selected", "-busy")
            return
        overrides = self.pending.setdefault(state.path, {})
        current = effective_flags(track, overrides.get(track.number))
        partial: list[bool | None] = list(overrides.get(track.number, (None, None, None)))
        index = FLAG_INDEX[which]
        partial[index] = not current[index]
        overrides[track.number] = tuple(partial)  # type: ignore[assignment]
        self._refresh_files()
        self._refresh_tracks()
        self._set_status(
            f"{Path(state.path).name}: track #{track.number} "
            f"{'EDF'[index]} -> {int(bool(partial[index]))} (pending)", "-busy")

    def action_toggle_enabled(self) -> None:
        self._toggle_flag("e")

    def action_toggle_default(self) -> None:
        self._toggle_flag("d")

    def action_toggle_forced(self) -> None:
        self._toggle_flag("f")

    def _active_pick_policy(self) -> policy_mod.PickPolicy:
        if self.active_policy:
            loaded = policy_mod.load_policy(self.active_policy)
            if loaded is not None:
                return loaded
        return policy_mod.PickPolicy.from_settings(self.settings)

    def action_smart_pick(self) -> None:
        state = self._current_state()
        if state is None:
            self._set_status("No episode selected", "-busy")
            return
        policy = self._active_pick_policy()
        result = policy_mod.smart_pick(state, policy)
        self.pending[state.path] = result.overrides
        self._refresh_files()
        self._refresh_tracks()
        self._set_status(f"{Path(state.path).name}: {'; '.join(result.notes)} — "
                         f"review with F4, apply with F5", "-ok")

    def action_smart_pick_all(self) -> None:
        targets = [p for p in self.order if p in self.marked] or list(self.order)
        policy = self._active_pick_policy()
        picked = 0
        for path in targets:
            result = policy_mod.smart_pick(self.states[path], policy)
            if result.overrides:
                self.pending[path] = result.overrides
                picked += 1
        self._refresh_files()
        self._refresh_tracks()
        scope = "marked" if self.marked else "all"
        self._set_status(f"Auto-picked {picked} {scope} episode(s) — "
                         f"review with F4, apply with F5", "-ok")

    def action_clear_current(self) -> None:
        if not self.current_file or self.current_file not in self.pending:
            self._set_status("No pending changes on this episode", "-busy")
            return
        self.pending.pop(self.current_file, None)
        self._refresh_files()
        self._refresh_tracks()
        self._set_status(f"Cleared pending changes for {Path(self.current_file).name}", "-ok")

    def action_clear_all(self) -> None:
        diffs = self._collect_diffs()
        if not diffs and not self.pending:
            self._set_status("No pending changes to clear", "-busy")
            return
        self.pending.clear()
        self._refresh_files()
        self._refresh_tracks()
        self._set_status("Cleared all pending changes (scan cache kept)", "-ok")

    def action_mark(self) -> None:
        if self.current_file is None:
            return
        if self.current_file in self.marked:
            self.marked.discard(self.current_file)
        else:
            self.marked.add(self.current_file)
        self._refresh_files()

    def action_preview(self) -> None:
        diffs = self._collect_diffs()
        if not diffs:
            self._set_status("No pending changes to preview", "-busy")
            return
        self.push_screen(PreviewScreen(diffs, self.tools.get("mkvpropedit", "mkvpropedit")))

    def action_apply(self) -> None:
        diffs = self._collect_diffs()
        if not diffs:
            self._set_status("Nothing to apply — F2 picks a track, g auto-picks", "-busy")
            return
        n_changes = sum(len(c) for _, c in diffs)
        self.push_screen(
            ConfirmScreen(f"Apply {n_changes} flag change(s) across {len(diffs)} episode(s)?"),
            self._apply_confirmed)

    def _apply_confirmed(self, go: bool | None) -> None:
        if not go:
            self._set_status("Apply cancelled — pending changes kept", "-busy")
            return
        diffs = self._collect_diffs()
        if not diffs:
            self._set_status("Nothing to apply", "-busy")
            return
        expected_by_path = {
            path: target_flags(self.states[path], self.pending.get(path, {}))
            for path, _ in diffs
        }
        self._set_status(f"Applying changes to {len(diffs)} episode(s)…", "-busy")
        self._do_apply(diffs, expected_by_path)

    @work(thread=True, exclusive=True, group="apply")
    def _do_apply(self, diffs: list[tuple[str, list[FlagChange]]],
                  expected_by_path: dict[str, dict]) -> None:
        self.apply_done.clear()
        self._op_screen = None
        total = len(diffs)

        def maybe_show() -> None:
            if not self.apply_done.is_set() and self._op_screen is None:
                self._op_screen = _ProgressScreen("APPLYING CHANGES", "Starting…")
                self.push_screen(self._op_screen)

        timer = threading.Timer(0.15, lambda: self.call_from_thread(maybe_show))
        timer.daemon = True
        timer.start()

        results = []
        problems: list[str] = []
        for idx, (path, changes) in enumerate(diffs):
            if self._op_screen is not None:
                self.call_from_thread(
                    self._op_screen.update_progress, idx + 1, total, Path(path).name)
            result = apply_changes(path, changes, mkvpropedit=self.tools["mkvpropedit"])
            results.append(result)
            if result.ok:
                fresh = identify(result.path, self.tools, use_cache=False)
                for problem in verify_flags(fresh, expected_by_path[result.path]):
                    problems.append(f"{Path(result.path).name}: {problem}")

        journal_mod.write_journal(results, note="tui apply")
        ok_results = [r for r in results if r.ok]
        bad_results = [r for r in results if not r.ok]
        total_changes = sum(len(r.changes) for r in ok_results)

        if bad_results:
            detail = "; ".join(f"{Path(r.path).name}: {r.error}" for r in bad_results)
            summary, cls = f"FAILED on {len(bad_results)} file(s): {detail}", "-error"
        elif problems:
            summary, cls = f"Applied but VERIFY found issues: {'; '.join(problems)}", "-error"
        else:
            summary, cls = (f"Applied {total_changes} change(s) on {len(ok_results)} "
                            f"episode(s) — all verified", "-ok")

        def finish() -> None:
            timer.cancel()
            if self._op_screen is not None:
                self._op_screen.dismiss()
                self._op_screen = None
            for result in ok_results:
                self.pending.pop(result.path, None)
            self._after_load_status = (summary, cls)
            self._set_status(summary, cls)
            self.apply_done.set()
            self.load_files(show_scan=False)

        self.call_from_thread(finish)

    def action_undo(self) -> None:
        journals = journal_mod.list_journals()
        if not journals:
            self._set_status("No journals yet — nothing to undo", "-busy")
            return
        self.push_screen(UndoScreen(journals[:10]), self._undo_confirmed)

    def _undo_confirmed(self, journal_id: str | None) -> None:
        if not journal_id:
            return
        self._set_status(f"Restoring from journal {journal_id}…", "-busy")
        self._do_undo(journal_id)

    @work(thread=True, exclusive=True, group="undo")
    def _do_undo(self, journal_id: str) -> None:
        self.undo_done.clear()
        self._op_screen = None
        data = journal_mod.load_journal(journal_id)
        entries = journal_mod.undo_plan(data)
        total = len(entries)

        def maybe_show() -> None:
            if not self.undo_done.is_set() and self._op_screen is None:
                self._op_screen = _ProgressScreen("RESTORING FROM JOURNAL", "Starting…")
                self.push_screen(self._op_screen)

        timer = threading.Timer(0.15, lambda: self.call_from_thread(maybe_show))
        timer.daemon = True
        timer.start()

        restored = 0
        problems: list[str] = []
        for idx, (path, changes) in enumerate(entries):
            if self._op_screen is not None:
                self.call_from_thread(
                    self._op_screen.update_progress, idx + 1, max(total, 1), Path(path).name)
            result = apply_changes(path, changes, mkvpropedit=self.tools["mkvpropedit"])
            if result.ok:
                fresh = identify(path, self.tools, use_cache=False)
                want = {c.track.number: c.after for c in changes}
                file_problems = verify_flags(fresh, want)
                if file_problems:
                    for problem in file_problems:
                        problems.append(f"{Path(path).name}: {problem}")
                else:
                    restored += 1
            else:
                problems.append(f"{Path(path).name}: {result.error}")

        if problems:
            cls = "-error"
            summary = (f"Undo {journal_id}: restored {restored}/{total} episode(s) — "
                       f"{'; '.join(problems)}")
        else:
            cls = "-ok"
            summary = f"Undo {journal_id}: restored {restored}/{total} episode(s) — all verified"

        def finish() -> None:
            timer.cancel()
            if self._op_screen is not None:
                self._op_screen.dismiss()
                self._op_screen = None
            self.pending.clear()
            self._after_load_status = (summary, cls)
            self._set_status(summary, cls)
            self.undo_done.set()
            self.load_files(show_scan=False)

        self.call_from_thread(finish)

    def action_rescan(self) -> None:
        def do_rescan() -> None:
            self.pending.clear()
            self._set_status("Rescanning…", "-busy")
            clear_cache()
            self.load_files(show_scan=True)

        self._confirm_discard("rescan (clears pending edits)", do_rescan)

    def action_quit(self) -> None:
        self._confirm_discard("quit", self.exit)

    def _set_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        self.settings.theme = name
        config.save_settings(self.settings)
        self.theme = name
        self._apply_theme_refresh()
        self._set_status(f"Theme: {name}", "-ok")

    def action_theme(self) -> None:
        current = self.settings.theme if self.settings.theme in THEME_ORDER else "dark"
        nxt = THEME_ORDER[(THEME_ORDER.index(current) + 1) % len(THEME_ORDER)]
        self._set_theme(nxt)

    def action_select_theme(self) -> None:
        self.push_screen(
            _ThemeScreen(self.theme if self.theme in THEMES else self.settings.theme),
            lambda chosen: self._set_theme(chosen) if chosen else None,
        )

    def action_settings(self) -> None:
        self.push_screen(
            _SettingsScreen(self.settings, self.active_policy),
            self._settings_saved,
        )

    def _settings_saved(self, result: tuple[config.Settings, str | None] | None) -> None:
        if result is None:
            return
        new_settings, policy_name = result
        self.settings = new_settings
        self.active_policy = policy_name
        config.save_settings(self.settings)
        self._refresh_info()
        label = f"policy={policy_name}" if policy_name else "custom settings"
        self._set_status(f"Saved settings ({label})", "-ok")

    def action_diagnostics(self) -> None:
        self.push_screen(DiagScreen(self.tools, self.settings, self.active_policy))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


# --------------------------------------------------------------- screens

class _MenuScreen(ModalScreen[str]):
    """Dropdown under a menu-bar item."""

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, title: str, items: list[tuple[str, str]], x: int) -> None:
        super().__init__()
        self.menu_title = title
        self.items = items
        self.x = x

    def compose(self) -> ComposeResult:
        options = [Option(label, id=action) for label, action in self.items]
        yield OptionList(*options, id="menu-options", classes="menu-dropdown")

    def on_mount(self) -> None:
        self.styles.align = ("left", "top")
        self.query_one("#menu-options", OptionList).styles.offset = (self.x, 1)

    @on(OptionList.OptionSelected, "#menu-options")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ProgressScreen(ModalScreen[None]):
    """MC-style progress dialog so long scans, applies, and undos never look frozen."""

    def __init__(self, title: str = "SCANNING EPISODES", initial: str = "Starting…") -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box scan-box"):
            yield Static(self._title, classes="modal-title")
            yield Static(self._initial, id="scan-file", classes="modal-text")
            yield ProgressBar(total=None, show_eta=False, id="scan-bar")
            yield Static("0 / 0", id="scan-count", classes="modal-text")

    def update_progress(self, done: int, total: int, name: str) -> None:
        if not self.is_mounted:
            return
        self.query_one("#scan-file", Static).update(middle_truncate(name, 56))
        self.query_one("#scan-count", Static).update(f"{done} / {total} episodes")
        self.query_one("#scan-bar", ProgressBar).update(progress=done, total=max(total, 1))


class _ScanScreen(_ProgressScreen):
    """Progress dialog specifically for folder scanning."""

    def __init__(self) -> None:
        super().__init__("SCANNING EPISODES", "Starting…")


class _ScopeScreen(ModalScreen[str]):
    """Ask once: this episode, or every episode with the same track?"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("a", "all", "All"),
        Binding("l", "relaxed", "Lang+name"),
        Binding("o", "one", "This file"),
    ]

    def __init__(self, track_desc: str, strict_count: int, total: int, *,
                 relaxed_count: int = 0, skipped_names: list[str] | None = None,
                 disable_others: bool = False) -> None:
        super().__init__()
        self.track_desc = track_desc
        self.strict_count = strict_count
        self.relaxed_count = relaxed_count
        self.total = total
        self.skipped_names = skipped_names or []
        self.disable_others = disable_others

    def compose(self) -> ComposeResult:
        title = ("SET DEFAULT & DISABLE OTHERS — SCOPE"
                 if self.disable_others else "SET DEFAULT — SCOPE")
        primary_count = self.strict_count if self.strict_count > 1 else self.relaxed_count
        lines = [
            f"{self.strict_count} of {self.total} episode(s) carry an exact matching track "
            f"(same type, language, name, codec and position)."
        ]
        if self.relaxed_count > self.strict_count:
            lines.append(
                f"{self.relaxed_count} of {self.total} match by type + language + name alone."
            )
        if self.skipped_names:
            sample = ", ".join(middle_truncate(n, 28) for n in self.skipped_names[:4])
            extra = (f" (+{len(self.skipped_names) - 4} more)"
                     if len(self.skipped_names) > 4 else "")
            lines.append(f"Differing layout: {sample}{extra}")
        lines.append("Apply the pick across matched episodes?")
        with Vertical(classes="modal-box"):
            yield Static(title, classes="modal-title")
            yield Static(f"Track: {self.track_desc}", classes="modal-text")
            yield Static("\n".join(lines), classes="modal-text", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button(f"All {primary_count} episodes", id="all", classes="-primary")
                if self.relaxed_count > self.strict_count and self.strict_count > 1:
                    yield Button(f"Lang+name ({self.relaxed_count})", id="relaxed")
                yield Button("This episode only", id="one")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#all", Button).focus()

    @on(Button.Pressed, "#all")
    def _all(self) -> None:
        self.dismiss("all")

    @on(Button.Pressed, "#relaxed")
    def _relaxed(self) -> None:
        self.dismiss("relaxed")

    @on(Button.Pressed, "#one")
    def _one(self) -> None:
        self.dismiss("one")

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_all(self) -> None:
        self.dismiss("all")

    def action_relaxed(self) -> None:
        if self.relaxed_count > self.strict_count and self.strict_count > 1:
            self.dismiss("relaxed")

    def action_one(self) -> None:
        self.dismiss("one")


class _GotoScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: Path) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static("GO TO FOLDER", classes="modal-title")
            yield Input(value=str(self.current), id="goto-input")
            with Horizontal(classes="modal-actions"):
                yield Button("Open", id="open", classes="-primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#goto-input", Input).focus()

    @on(Input.Submitted, "#goto-input")
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    @on(Button.Pressed, "#open")
    def _open(self) -> None:
        self.dismiss(self.query_one("#goto-input", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _Modal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Close")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class PreviewScreen(_Modal):
    def __init__(self, diffs: list[tuple[str, list[FlagChange]]], mkvpropedit: str) -> None:
        super().__init__()
        self.diffs = diffs
        self.mkvpropedit = mkvpropedit

    def compose(self) -> ComposeResult:
        lines = []
        total = 0
        for path, changes in self.diffs:
            lines.append(f"{Path(path).name}  ({len(changes)} change(s))")
            for change in changes:
                name = f' "{change.track.name}"' if change.track.name else ""
                lines.append(f"    #{change.track.number} {change.track.kind.value:<9} "
                             f"{change.track.language}{name}  {change.summary()}")
            total += len(changes)
            cmd = build_command(path, changes, self.mkvpropedit)
            lines.append("    $ " + shlex.join(cmd))
            lines.append("")
        lines.append(f"TOTAL: {total} change(s) on {len(self.diffs)} episode(s) — "
                     f"nothing written yet")
        with Vertical(classes="modal-box", id="preview-box"):
            yield Static("PREVIEW (DRY RUN)", classes="modal-title")
            with VerticalScroll():
                yield Static("\n".join(lines), classes="modal-text", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button("Close", id="close", classes="-primary")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Confirm"),
        Binding("n", "cancel", "Cancel"),
    ]

    def __init__(self, message: str, *, title: str = "CONFIRM",
                 confirm_label: str = "Apply") -> None:
        super().__init__()
        self.message = message
        self.modal_title = title
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box", id="confirm-box"):
            yield Static(self.modal_title, classes="modal-title")
            yield Static(self.message, classes="modal-text", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button(self.confirm_label, id="apply", classes="-primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#apply", Button).focus()

    @on(Button.Pressed, "#apply")
    def _apply(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)


class UndoScreen(_Modal):
    def __init__(self, journals: list[Path]) -> None:
        super().__init__()
        self.journals = journals

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box", id="undo-box"):
            yield Static("UNDO — pick a journal to restore", classes="modal-title")
            table = DataTable(id="journals", cursor_type="row")
            table.add_columns("Journal", "Created", "Files", "Note")
            yield table
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#journals", DataTable)
        for path in self.journals:
            data = journal_mod.load_journal(path)
            table.add_row(path.stem, data.get("created", "?"),
                          str(len(data.get("entries", []))), data.get("note", ""),
                          key=path.stem)
        table.focus()

    @on(DataTable.RowSelected, "#journals")
    def _pick(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        self.dismiss(str(event.row_key.value))

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)


class _ThemeScreen(_Modal):
    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        options = [
            Option(THEME_LABELS.get(name, name), id=name)
            for name in THEME_ORDER
        ]
        with Vertical(classes="modal-box", id="theme-box"):
            yield Static("SELECT THEME", classes="modal-title")
            yield OptionList(*options, id="theme-options")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        opt_list = self.query_one("#theme-options", OptionList)
        if self.current in THEME_ORDER:
            opt_list.highlighted = THEME_ORDER.index(self.current)
        opt_list.focus()

    @on(OptionList.OptionSelected, "#theme-options")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)


class _SettingsScreen(_Modal):
    def __init__(self, settings: config.Settings, active_policy: str | None) -> None:
        super().__init__()
        self.settings = settings
        self.active_policy = active_policy

    def compose(self) -> ComposeResult:
        policies = [("Custom (use settings below)", "__custom__")]
        for name in policy_mod.list_policies():
            policies.append((name, name))
        initial_policy = self.active_policy if self.active_policy in dict((v, k) for k, v in policies) else "__custom__"
        with Vertical(classes="modal-box", id="settings-box"):
            yield Static("POLICY & SETTINGS", classes="modal-title")
            with VerticalScroll():
                yield Label("Auto-pick policy preset (for g / G):", classes="field-label")
                yield Select(policies, allow_blank=False, value=initial_policy, id="policy-select")
                yield Label("Preferred audio languages (comma-separated):", classes="field-label")
                yield Input(value=", ".join(self.settings.audio_langs), id="audio-langs")
                yield Label("Preferred subtitle languages (comma-separated):", classes="field-label")
                yield Input(value=", ".join(self.settings.sub_langs), id="sub-langs")
                yield Checkbox("Force picked subtitles (flag-forced=1)",
                               value=self.settings.force_picked_subs, id="force-subs")
                yield Checkbox("Disable unpicked subtitle tracks (flag-enabled=0)",
                               value=self.settings.disable_other_subs, id="disable-subs")
                yield Checkbox("Disable unpicked audio tracks (flag-enabled=0)",
                               value=self.settings.disable_other_audio, id="disable-audio")
            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="save", classes="-primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#policy-select", Select).focus()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        audio_raw = self.query_one("#audio-langs", Input).value
        sub_raw = self.query_one("#sub-langs", Input).value
        audio_langs = tuple(x.strip() for x in audio_raw.split(",") if x.strip()) or ("jpn",)
        sub_langs = tuple(x.strip() for x in sub_raw.split(",") if x.strip()) or ("eng",)
        policy_val = self.query_one("#policy-select", Select).value
        chosen_policy = None if policy_val in (Select.BLANK, "__custom__") else str(policy_val)
        updated = config.Settings(
            theme=self.settings.theme,
            debug=self.settings.debug,
            browser_mode=self.settings.browser_mode,
            force_picked_subs=self.query_one("#force-subs", Checkbox).value,
            disable_other_subs=self.query_one("#disable-subs", Checkbox).value,
            disable_other_audio=self.query_one("#disable-audio", Checkbox).value,
            audio_langs=audio_langs,
            sub_langs=sub_langs,
        )
        self.dismiss((updated, chosen_policy))

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)


class HelpScreen(_Modal):
    def compose(self) -> ComposeResult:
        themes_str = " / ".join(THEME_ORDER)
        text = "\n".join([
            "MENUS: click File / Pick / Batch / View / Help  (F9 opens File)",
            "",
            "NAVIGATION",
            "  ↑/↓        move between episodes (Tab switches to the tracks table)",
            "  F3         open/close the file browser",
            "             (mc panel by default: one folder + .. to go up;",
            "              View menu switches to the full tree)",
            "  Enter      enter folder / open episode's folder / pick journal",
            "  backspace  go up a folder (browser)",
            "  ctrl+g     jump to any folder by path",
            "",
            "PICKING (you are always the one deciding)",
            "  F2 / p     make the highlighted track THE default for its type",
            "             — subs also get forced; rival defaults+forced cleared",
            "             If other episodes carry a matching track you choose:",
            "             all of them at once, or this episode only.",
            "  X          pick highlighted track AND disable all other tracks of",
            "             that type (works for both audio & subtitles)",
            "  g          auto-pick this episode",
            "  G          auto-pick all episodes (or only the marked ones)",
            "  e / d / f  toggle enabled / default / forced on the highlighted track",
            "  c / C      clear pending changes (current episode / all episodes)",
            "  m          mark/unmark an episode (limits G to marked)",
            "",
            "BATCH",
            "  F4 / P     preview pending changes + exact commands (dry run)",
            "  F5 / !     apply pending changes (asks first, journals for undo)",
            "  F8 / u     undo a previous apply from its journal (verified)",
            "  s          policy presets & language/flag settings",
            "  r          rescan folder (drop cache)",
            "",
            "OTHER",
            f"  t / T      next theme / theme picker ({themes_str})",
            "  D          diagnostics (tools, paths, settings)",
            "  F10 / q    quit",
        ])
        with Vertical(classes="modal-box", id="help-box"):
            yield Static("HELP", classes="modal-title")
            with VerticalScroll():
                yield Static(text, classes="modal-text", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button("Close", id="close", classes="-primary")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class DiagScreen(_Modal):
    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("x", "toggle_debug", "Toggle debug"),
    ]

    def __init__(self, tools: dict[str, str], settings: config.Settings,
                 active_policy: str | None = None) -> None:
        super().__init__()
        self.tools = tools
        self.settings = settings
        self.active_policy = active_policy

    def compose(self) -> ComposeResult:
        lines = []
        for name, path in self.tools.items():
            try:
                out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                version = out.stdout.splitlines()[0] if out.stdout else "?"
            except (OSError, subprocess.SubprocessError) as exc:
                version = f"error: {exc}"
            lines.append(f"{name:<10} {path}")
            lines.append(f"{'':<10} {version}")
        lines.append("")
        lines.append(f"config     {config.config_dir()}")
        lines.append(f"data       {config.data_dir()}")
        lines.append(f"cache      {config.cache_dir()}")
        lines.append(f"log        {config.log_dir() / 'mkvtune.log'}")
        lines.append(f"journals   {len(journal_mod.list_journals())} journal(s)")
        lines.append(f"theme      {self.settings.theme}")
        lines.append(f"policy     {self.active_policy or 'custom (settings)'}")
        lines.append(f"debug      {'ON' if self.settings.debug else 'OFF'}  (press x to toggle)")
        with Vertical(classes="modal-box", id="diag-box"):
            yield Static("DIAGNOSTICS", classes="modal-title")
            with VerticalScroll():
                yield Static("\n".join(lines), classes="modal-text", markup=False, id="diag-text")
            with Horizontal(classes="modal-actions"):
                yield Button("Close", id="close", classes="-primary")

    def action_toggle_debug(self) -> None:
        self.settings.debug = not self.settings.debug
        config.save_settings(self.settings)
        config.setup_logging(self.settings.debug)
        self.dismiss(None)

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------- entry

def run_tui(directory: str | Path, settings: config.Settings | None = None) -> int:
    settings = settings or config.load_settings()
    config.setup_logging(settings.debug)
    try:
        tools = find_tools()
    except ToolNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    log.info("starting TUI in %s", Path(directory).resolve())
    app = MkvtuneApp(directory, settings=settings, tools=tools)
    app.run()
    return 0
