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
import subprocess
import sys
import threading
from pathlib import Path

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, DirectoryTree, Input, Label, OptionList, ProgressBar,
    Static,
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
        ("Toggle enabled", "e", "pick.enabled"),
        ("Toggle default", "d", "pick.default"),
        ("Toggle forced", "f", "pick.forced"),
        ("Auto-pick this episode", "g", "pick.smart"),
    ],
    "batch": [
        ("Auto-pick all episodes", "G", "batch.smart_all"),
        ("Preview pending changes", "F4", "batch.preview"),
        ("Apply pending changes", "F5", "batch.apply"),
        ("Undo last apply", "F8", "batch.undo"),
    ],
    "view": [
        ("Toggle file browser", "F3", "view.browser"),
        ("Toggle dark / AMOLED theme", "t", "view.theme"),
    ],
    "help": [
        ("Keys", "F1", "help.keys"),
        ("Diagnostics", "D", "help.diag"),
    ],
}

MENU_TITLES = {"file": "File", "pick": "Pick", "batch": "Batch",
               "view": "View", "help": "Help"}


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


def flag_text(value: bool, changed: bool = False) -> Text:
    if changed:
        return Text("●", style="bold yellow")
    return Text("●", style="bold green") if value else Text("○", style="dim")


class _MenuItem(Static):
    """A menu-bar title that opens its dropdown on click."""

    def on_click(self) -> None:
        key = self.id.split("-", 1)[1]
        self.app._open_menu(key, x=self.region.x)  # type: ignore[attr-defined]


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
        Binding("e", "toggle_enabled", "Enable"),
        Binding("d", "toggle_default", "Default"),
        Binding("f", "toggle_forced", "Forced"),
        Binding("g", "smart_pick", "Auto-pick file"),
        Binding("G", "smart_pick_all", "Auto-pick all"),
        Binding("m", "mark", "Mark file"),
        Binding("P", "preview", "Preview"),
        Binding("exclamation_mark", "apply", "Apply"),
        Binding("u", "undo", "Undo"),
        Binding("r", "rescan", "Rescan"),
        Binding("t", "theme", "Theme"),
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
        self._track_rows: list[Track] = []
        self._after_load_status: str | None = None
        self._scan_screen: "_ScanScreen | None" = None
        self._loaded_width: int = 0
        self.scan_done = threading.Event()

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
                yield DirectoryTree(str(self.directory.parent or self.directory), id="tree")
            with Vertical(id="main"):
                yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                with Vertical(id="tracks-wrap"):
                    yield Label("TRACKS", id="tracks-title")
                    yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield Static(
            "[bold #6cb6ff]F1[/] Help  [bold #6cb6ff]F2[/] Pick  "
            "[bold #6cb6ff]F3[/] Browse  [bold #6cb6ff]F4[/] Preview  "
            "[bold #6cb6ff]F5[/] Apply  [bold #6cb6ff]F8[/] Undo  "
            "[bold #6cb6ff]F10[/] Quit",
            id="keybar", markup=True)
        yield Static("Loading…", id="status")

    def on_mount(self) -> None:
        if self.settings.amoled:
            self.add_class("amoled")
            self.screen.add_class("amoled")
        self.query_one("#browser").display = False
        files = self.query_one("#files", DataTable)
        files.add_columns("✓", "Episode", "Audio", "Sub", "Δ")
        tracks = self.query_one("#tracks", DataTable)
        tracks.add_columns("#", "Type", "Codec", "Lang", "Name", "E", "D", "F")
        files.focus()  # never let the hidden browser tree hold initial focus
        self.load_files(show_scan=True)

    # ----------------------------------------------------------- scanning

    @work(thread=True, exclusive=True, group="scan")
    def load_files(self, show_scan: bool = True) -> None:
        self.scan_done.clear()
        files = list_mkv_files(self.directory)

        def start() -> None:
            if show_scan:
                self._scan_screen = _ScanScreen()
                self.push_screen(self._scan_screen)

        self.call_from_thread(start)

        states: dict[str, FileTracks] = {}
        total = len(files)
        for index, path in enumerate(files):
            if self._scan_screen is not None:
                self.call_from_thread(self._scan_screen.update_progress,
                                      index + 1, total, path.name)
            states[str(path)] = identify(path, self.tools, use_cache=True)

        def finish() -> None:
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
                self._set_status(self._after_load_status)
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
        for path in self.order:
            state = self.states[path]
            mark = Text("●", style="bold yellow") if path in self.marked else Text(" ")
            audio, sub = self._pick_chips(state)
            changes = self._change_count(state)
            table.add_row(
                mark,
                middle_truncate(Path(path).name, name_width),
                audio,
                sub,
                Text(str(changes), style="bold yellow") if changes else Text("—", style="dim"),
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
                *(flag_text(v, changed) for v in eff),
                key=str(track.number),
            )
        if self._track_rows:
            table.move_cursor(row=min(keep, len(self._track_rows) - 1))

    def _pick_chips(self, state: FileTracks) -> tuple[Text, Text]:
        overrides = self.pending.get(state.path, {})

        def winner(kind: TrackKind, color: str) -> Text:
            for track in state.tracks_of(kind):
                eff = effective_flags(track, overrides.get(track.number))
                if eff[1]:
                    label = f"#{track.number} {track.language}"
                    if track.name:
                        label += f" {middle_truncate(track.name, 14)}"
                    return Text(label, style=f"bold {color}")
            return Text("—", style="dim")

        return winner(TrackKind.AUDIO, "cyan"), winner(TrackKind.SUBTITLES, "green")

    def _refresh_info(self) -> None:
        pending_files = sum(1 for p in self.order if self._change_count(self.states[p]))
        pending_changes = sum(self._change_count(self.states[p]) for p in self.order)
        info = self.query_one("#infobar", Static)
        parts = [
            f"[bold #6cb6ff]{self.directory}[/]",
            f"[green]✓ scanned[/] {len(self.order)} episodes",
        ]
        if pending_changes:
            parts.append(f"[bold yellow]● {pending_changes} pending change(s) "
                         f"in {pending_files} file(s)[/]")
        else:
            parts.append("[dim]no pending changes[/]")
        if self.marked:
            parts.append(f"[yellow]marked: {len(self.marked)}[/]")
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
        self.directory = directory
        self.pending.clear()
        self.marked.clear()
        self.current_file = str(focus) if focus else None
        self._set_status(f"Loading {directory}…", "-busy")
        self.load_files(show_scan=True)

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
            "pick.enabled": self.action_toggle_enabled,
            "pick.default": self.action_toggle_default,
            "pick.forced": self.action_toggle_forced,
            "pick.smart": self.action_smart_pick,
            "batch.smart_all": self.action_smart_pick_all,
            "batch.preview": self.action_preview,
            "batch.apply": self.action_apply,
            "batch.undo": self.action_undo,
            "view.browser": self.action_browse,
            "view.theme": self.action_theme,
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
            self.query_one("#tree", DirectoryTree).focus()
            self._set_status("Browser open — Enter picks a folder, F3 closes", "-busy")

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
        state, track = self._current_state(), self._current_track()
        if state is None or track is None:
            self._set_status("Nothing to pick — no track selected", "-busy")
            return
        if track.kind is TrackKind.VIDEO:
            self._set_status("Video tracks don't take part in default/forced picking", "-busy")
            return
        matches = self._matching_tracks(track, state)
        others = len(matches) - 1
        if others <= 0:
            self._apply_pick([(state.path, track)])
            return
        desc = (f'{track.kind.value} #{track.number} {track.language}'
                f'{f" “{track.name}”" if track.name else ""}')
        self.push_screen(
            _ScopeScreen(desc, others, len(self.states)),
            lambda scope: self._pick_scope_confirmed(scope, matches, track, state))

    def _pick_scope_confirmed(self, scope: str | None, matches: dict[str, Track],
                              track: Track, state: FileTracks) -> None:
        if scope == "all":
            self._apply_pick(sorted(matches.items()))
        elif scope == "one":
            self._apply_pick([(state.path, track)])

    def _apply_pick(self, targets: list[tuple[str, Track]]) -> None:
        for path, track in targets:
            overrides = policy_mod.pick_overrides(
                self.states[path], track.kind, track.number,
                also_force=self.settings.force_picked_subs,
                disable_others=(self.settings.disable_other_subs
                                if track.kind is TrackKind.SUBTITLES else False))
            self.pending.setdefault(path, {}).update(overrides)
        self._refresh_files()
        self._refresh_tracks()
        first = targets[0][1]
        force_note = " (+forced)" if (first.kind is TrackKind.SUBTITLES
                                      and self.settings.force_picked_subs) else ""
        where = (f"{len(targets)} episode(s)" if len(targets) > 1
                 else Path(targets[0][0]).name)
        self._set_status(
            f"Picked {first.kind.value} #{first.number}{force_note} as exclusive default "
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

    def action_smart_pick(self) -> None:
        state = self._current_state()
        if state is None:
            self._set_status("No episode selected", "-busy")
            return
        policy = policy_mod.PickPolicy.from_settings(self.settings)
        result = policy_mod.smart_pick(state, policy)
        self.pending[state.path] = result.overrides
        self._refresh_files()
        self._refresh_tracks()
        self._set_status(f"{Path(state.path).name}: {'; '.join(result.notes)} — "
                         f"review with F4, apply with F5", "-ok")

    def action_smart_pick_all(self) -> None:
        targets = [p for p in self.order if p in self.marked] or list(self.order)
        policy = policy_mod.PickPolicy.from_settings(self.settings)
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
        self._do_apply()

    def _do_apply(self) -> None:
        results = []
        for path, changes in self._collect_diffs():
            results.append(apply_changes(path, changes, mkvpropedit=self.tools["mkvpropedit"]))
        if not results:
            self._set_status("Nothing to apply", "-busy")
            return

        ok_results = [r for r in results if r.ok]
        bad_results = [r for r in results if not r.ok]
        total_changes = sum(len(r.changes) for r in ok_results)

        problems: list[str] = []
        for result in ok_results:
            want = target_flags(self.states[result.path], self.pending.get(result.path, {}))
            fresh = identify(result.path, self.tools, use_cache=False)
            for problem in verify_flags(fresh, want):
                problems.append(f"{Path(result.path).name}: {problem}")

        journal_mod.write_journal(results, note="tui apply")

        for result in ok_results:
            self.pending.pop(result.path, None)

        if bad_results:
            detail = "; ".join(f"{Path(r.path).name}: {r.error}" for r in bad_results)
            summary, cls = f"FAILED on {len(bad_results)} file(s): {detail}", "-error"
        elif problems:
            summary, cls = f"Applied but VERIFY found issues: {'; '.join(problems)}", "-error"
        else:
            summary, cls = (f"Applied {total_changes} change(s) on {len(ok_results)} "
                            f"episode(s) — all verified", "-ok")
        self._after_load_status = summary
        self._set_status(summary, cls)
        self.load_files(show_scan=False)

    def action_undo(self) -> None:
        journals = journal_mod.list_journals()
        if not journals:
            self._set_status("No journals yet — nothing to undo", "-busy")
            return
        self.push_screen(UndoScreen(journals[:10]), self._undo_confirmed)

    def _undo_confirmed(self, journal_id: str | None) -> None:
        if not journal_id:
            return
        data = journal_mod.load_journal(journal_id)
        entries = journal_mod.undo_plan(data)
        restored = 0
        for path, changes in entries:
            result = apply_changes(path, changes, mkvpropedit=self.tools["mkvpropedit"])
            if result.ok:
                restored += 1
        self.pending.clear()
        cls = "-ok" if restored == len(entries) else "-error"
        summary = f"Undo {journal_id}: restored {restored}/{len(entries)} episode(s)"
        self._after_load_status = summary
        self._set_status(summary, cls)
        self.load_files(show_scan=False)

    def action_rescan(self) -> None:
        self.pending.clear()
        self._set_status("Rescanning…", "-busy")
        clear_cache()
        self.load_files(show_scan=True)

    def action_theme(self) -> None:
        self.settings.theme = "dark" if self.settings.amoled else "amoled"
        config.save_settings(self.settings)
        self.screen.toggle_class("amoled")
        self.toggle_class("amoled")
        self._set_status(f"Theme: {self.settings.theme}", "-ok")

    def action_diagnostics(self) -> None:
        self.push_screen(DiagScreen(self.tools, self.settings))

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


class _ScanScreen(ModalScreen[None]):
    """MC-style progress dialog so a scan can never look like a hang."""

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box scan-box"):
            yield Static("SCANNING EPISODES", classes="modal-title")
            yield Static("Starting…", id="scan-file", classes="modal-text")
            yield ProgressBar(total=None, show_eta=False, id="scan-bar")
            yield Static("0 / 0", id="scan-count", classes="modal-text")

    def update_progress(self, done: int, total: int, name: str) -> None:
        if not self.is_mounted:
            return
        self.query_one("#scan-file", Static).update(middle_truncate(name, 56))
        self.query_one("#scan-count", Static).update(f"{done} / {total} episodes")
        self.query_one("#scan-bar", ProgressBar).update(progress=done, total=max(total, 1))


class _ScopeScreen(ModalScreen[str]):
    """Ask once: this episode, or every episode with the same track?"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("a", "all", "All"),
        Binding("o", "one", "This file"),
    ]

    def __init__(self, track_desc: str, others: int, total: int) -> None:
        super().__init__()
        self.track_desc = track_desc
        self.others = others
        self.total = total

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static("SET DEFAULT — SCOPE", classes="modal-title")
            yield Static(f"Track: {self.track_desc}", classes="modal-text")
            yield Static(
                f"{self.others} of the other {self.total - 1} episode(s) carry a matching "
                f"track (same type, language, name and position).\n"
                f"Apply the pick there too?",
                classes="modal-text")
            with Horizontal(classes="modal-actions"):
                yield Button(f"All {self.others + 1} episodes", id="all", classes="-primary")
                yield Button("This episode only", id="one")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#all", Button).focus()

    @on(Button.Pressed, "#all")
    def _all(self) -> None:
        self.dismiss("all")

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
            lines.append("    $ " + " ".join(cmd))
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

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box", id="confirm-box"):
            yield Static("CONFIRM", classes="modal-title")
            yield Static(self.message, classes="modal-text", markup=False)
            with Horizontal(classes="modal-actions"):
                yield Button("Apply", id="apply", classes="-primary")
                yield Button("Cancel", id="cancel")

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


class UndoScreen(ModalScreen[str]):
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

    @on(DataTable.RowSelected, "#journals")
    def _pick(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        self.dismiss(str(event.row_key.value))

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)


class HelpScreen(_Modal):
    def compose(self) -> ComposeResult:
        text = "\n".join([
            "MENUS: click File / Pick / Batch / View / Help  (F9 opens File)",
            "",
            "NAVIGATION",
            "  ↑/↓        move between episodes (Tab switches to the tracks table)",
            "  F3         open/close the file browser (Enter picks a folder)",
            "  ctrl+g     jump to any folder by path",
            "",
            "PICKING (you are always the one deciding)",
            "  F2 / p     make the highlighted track THE default for its type",
            "             — subs also get forced; rival defaults+forced cleared",
            "             If other episodes carry a matching track you choose:",
            "             all of them at once, or this episode only.",
            "  g          auto-pick this episode (jpn audio + full subs)",
            "  G          auto-pick all episodes (or only the marked ones)",
            "  e / d / f  toggle enabled / default / forced on the highlighted track",
            "  m          mark/unmark an episode (limits G to marked)",
            "",
            "BATCH",
            "  F4 / P     preview pending changes + exact commands (dry run)",
            "  F5 / !     apply pending changes (asks first, journals for undo)",
            "  F8 / u     undo a previous apply from its journal",
            "  r          rescan folder (drop cache)",
            "",
            "OTHER",
            "  t          toggle dark / AMOLED pure-black theme",
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

    def __init__(self, tools: dict[str, str], settings: config.Settings) -> None:
        super().__init__()
        self.tools = tools
        self.settings = settings

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
