"""The mkvtune Textual TUI: browse files, pick tracks, preview, batch-apply.

Interaction model (user requirement): *you* are the one picking. Auto-pick
(g/G) only fills a pending plan you can edit; nothing touches a file until
you preview (P) and explicitly apply (!).
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

from .. import config
from .. import journal as journal_mod
from .. import policy as policy_mod
from ..edit import apply_changes, build_command, plan, target_flags, verify_flags
from ..model import (
    FileTracks, FlagChange, Overrides, PartialFlags, Track, TrackKind,
    effective_flags, merge_flags,
)
from ..scan import ToolNotFound, clear_cache, find_tools, identify, list_mkv_files

log = logging.getLogger("mkvtune.tui")

FLAG_INDEX = {"e": 0, "d": 1, "f": 2}


class MkvtuneApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "mkvtune"

    BINDINGS = [
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
        self.scan_done = threading.Event()

    # ------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="files-pane"):
                yield Label(f"DIR  {self.directory}", id="folder")
                yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                yield Static("m mark · g auto-pick · G all", id="files-hint")
            with Vertical(id="tracks-pane"):
                yield Label("TRACKS", id="tracks-title")
                yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
                yield Static("p pick · e/d/f toggle flags", id="tracks-hint")
        yield Static("Loading…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        if self.settings.amoled:
            self.add_class("amoled")
            self.screen.add_class("amoled")
        files = self.query_one("#files", DataTable)
        files.add_columns("✓", "File", "Audio", "Sub", "Δ")
        tracks = self.query_one("#tracks", DataTable)
        tracks.add_columns("#", "Type", "Codec", "Lang", "Name", "E", "D", "F")
        self.load_files()

    # ----------------------------------------------------------- scanning

    @work(thread=True, exclusive=True, group="scan")
    def load_files(self, rescan: bool = False) -> None:
        if rescan:
            clear_cache()
        self.scan_done.clear()
        files = list_mkv_files(self.directory)
        states: dict[str, FileTracks] = {}
        for index, path in enumerate(files):
            self.call_from_thread(
                self._set_status, f"Scanning {path.name} ({index + 1}/{len(files)})…", "-busy")
            states[str(path)] = identify(path, self.tools, use_cache=not rescan)

        def finish() -> None:
            self.states = states
            self.order = list(states)
            if self.current_file not in self.states:
                self.current_file = self.order[0] if self.order else None
            self._refresh_files()
            self._refresh_tracks()
            if self._after_load_status is not None:
                self._set_status(self._after_load_status)
                self._after_load_status = None
            else:
                self._set_status(f"{len(self.order)} file(s) loaded from {self.directory}")

        self.call_from_thread(finish)
        self.scan_done.set()

    # ---------------------------------------------------------- rendering

    def _refresh_files(self) -> None:
        table = self.query_one("#files", DataTable)
        keep = table.cursor_row or 0
        table.clear()
        for path in self.order:
            state = self.states[path]
            mark = "●" if path in self.marked else ""
            audio, sub = self._pick_summaries(state)
            table.add_row(mark, Path(path).name, audio, sub,
                          str(self._change_count(state)) if self._change_count(state) else "—",
                          key=path)
        if self.order:
            table.move_cursor(row=min(keep, len(self.order) - 1))

    def _refresh_tracks(self) -> None:
        table = self.query_one("#tracks", DataTable)
        keep = table.cursor_row or 0
        table.clear()
        self._track_rows = []
        state = self.states.get(self.current_file or "")
        if state is None:
            self.query_one("#tracks-title", Label).update("TRACKS")
            return
        overrides = self.pending.get(self.current_file or "", {})
        self.query_one("#tracks-title", Label).update(f"TRACKS  {Path(state.path).name}")
        for track in state.tracks:
            eff = effective_flags(track, overrides.get(track.number))
            changed = eff != track.flags
            self._track_rows.append(track)
            table.add_row(
                f"{'•' if changed else ' '}{track.number}",
                track.kind.value, track.codec, track.language, track.name or "",
                *(("●" if v else "○") for v in eff),
                key=str(track.number),
            )
        if self._track_rows:
            table.move_cursor(row=min(keep, len(self._track_rows) - 1))

    def _pick_summaries(self, state: FileTracks) -> tuple[str, str]:
        overrides = self.pending.get(state.path, {})

        def winner(kind: TrackKind) -> str:
            for track in state.tracks_of(kind):
                eff = effective_flags(track, overrides.get(track.number))
                if eff[1]:
                    label = f"#{track.number} {track.language}"
                    if track.name:
                        label += f' "{track.name[:18]}"'
                    return label
            return "—"

        return winner(TrackKind.AUDIO), winner(TrackKind.SUBTITLES)

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
        table = self.query_one("#tracks", DataTable)
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

    # ------------------------------------------------------------- events

    @on(DataTable.RowHighlighted, "#files")
    def _file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        path = event.row_key.value
        if path != self.current_file and path in self.states:
            self.current_file = path
            self._refresh_tracks()

    @on(DataTable.RowHighlighted, "#tracks")
    def _track_highlighted(self, event: DataTable.RowHighlighted) -> None:
        track = self._current_track()
        if track is not None:
            self.query_one("#tracks-hint", Static).update(track.describe())

    # ------------------------------------------------------------ actions

    def action_pick(self) -> None:
        state, track = self._current_state(), self._current_track()
        if state is None or track is None:
            self._set_status("Nothing to pick — no track selected", "-busy")
            return
        if track.kind is TrackKind.VIDEO:
            self._set_status("Video tracks don't take part in default/forced picking", "-busy")
            return
        overrides = policy_mod.pick_overrides(
            state, track.kind, track.number,
            also_force=self.settings.force_picked_subs,
            disable_others=(self.settings.disable_other_subs
                            if track.kind is TrackKind.SUBTITLES else False))
        self.pending.setdefault(state.path, {}).update(overrides)
        self._refresh_files()
        self._refresh_tracks()
        force_note = " (+forced)" if (track.kind is TrackKind.SUBTITLES
                                      and self.settings.force_picked_subs) else ""
        self._set_status(
            f"Picked {track.kind.value} #{track.number}{force_note} in {Path(state.path).name} "
            f"as exclusive default — other {track.kind.value} defaults cleared", "-ok")

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
            self._set_status("No file selected", "-busy")
            return
        policy = policy_mod.PickPolicy.from_settings(self.settings)
        result = policy_mod.smart_pick(state, policy)
        self.pending[state.path] = result.overrides
        self._refresh_files()
        self._refresh_tracks()
        self._set_status(f"{Path(state.path).name}: {'; '.join(result.notes)} — review, then P / !", "-ok")

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
        self._set_status(f"Auto-picked {picked} {scope} file(s) — review with P, apply with !", "-ok")

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
            self._set_status("Nothing to apply — press g to auto-pick or p on a track", "-busy")
            return
        n_changes = sum(len(c) for _, c in diffs)
        self.push_screen(
            ConfirmScreen(f"Apply {n_changes} flag change(s) across {len(diffs)} file(s)?"),
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

        # successes leave the pending plan; failures stay for retry
        for result in ok_results:
            self.pending.pop(result.path, None)

        if bad_results:
            detail = "; ".join(f"{Path(r.path).name}: {r.error}" for r in bad_results)
            summary, cls = f"FAILED on {len(bad_results)} file(s): {detail}", "-error"
        elif problems:
            summary, cls = f"Applied but VERIFY found issues: {'; '.join(problems)}", "-error"
        else:
            summary, cls = (f"Applied {total_changes} change(s) on {len(ok_results)} file(s) — all verified",
                            "-ok")
        self._after_load_status = summary
        self._set_status(summary, cls)
        self.load_files()

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
        summary = f"Undo {journal_id}: restored {restored}/{len(entries)} file(s)"
        self._after_load_status = summary
        self._set_status(summary, cls)
        self.load_files()

    def action_rescan(self) -> None:
        self.pending.clear()
        self._set_status("Rescanning…", "-busy")
        self.load_files(rescan=True)

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


# --------------------------------------------------------------- modals

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
        lines.append(f"TOTAL: {total} change(s) on {len(self.diffs)} file(s) — nothing written yet")
        body = "\n".join(lines)
        with Vertical(classes="modal-box", id="preview-box"):
            yield Static("PREVIEW (dry run)", classes="modal-title")
            with VerticalScroll():
                yield Static(body, classes="modal-text", markup=False)
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
        self.dismiss(str(event.row_key.value))

    @on(Button.Pressed, "#cancel")
    def _cancel_button(self) -> None:
        self.dismiss(None)


class HelpScreen(_Modal):
    def compose(self) -> ComposeResult:
        text = "\n".join([
            "NAVIGATION",
            "  ↑/↓ or j/k   move between rows (Tab switches pane)",
            "  Enter/click  select file / journal",
            "",
            "PICKING (you are always the one deciding)",
            "  p            make the highlighted track THE default for its type",
            "               (subs also get forced; other tracks' default+forced cleared)",
            "  g            auto-pick this file (jpn audio + full subs) as a PROPOSAL",
            "  G            auto-pick all files (or only the marked ones)",
            "  e / d / f   toggle enabled / default / forced on the highlighted track",
            "  m            mark/unmark a file for batch scope",
            "",
            "BATCH",
            "  P            preview pending changes + exact commands (dry run)",
            "  !            apply pending changes (asks first, journals for undo)",
            "  u            undo a previous apply from its journal",
            "  r            rescan folder (drop cache)",
            "",
            "OTHER",
            "  t            toggle dark / AMOLED pure-black theme",
            "  D            diagnostics (tools, paths, settings)",
            "  q            quit",
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
