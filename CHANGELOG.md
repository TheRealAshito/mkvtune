# Changelog

## 0.1.1 — 2026-11-17

- Fix crash on startup/empty tables: DataTable emits `RowHighlighted` with
  no row key (cursor_row=-1) while tables are empty or mid-clear — on slow
  machines (e.g. a Pi scanning a folder) keys arrive in that window and the
  file-list handler raised AttributeError. Row-key handling is now
  None-safe everywhere (file list, journal picker) and cursor restore clamps
  at row 0. Regression test hammers keys before/after the scan window.

## 0.1.0 — 2026-11-17

First release.

- TUI (Textual): two-pane file/track browser, exclusive default picking
  (p), per-flag toggles (e/d/f), smart-pick proposals (g/G), preview (P),
  apply (!), undo (u), dark + AMOLED themes, diagnostics (D).
- CLI: scan (--json), apply (--pick/--policy/--dry-run), undo (--list),
  policies, doctor.
- mkvpropedit-backed flag editing (enabled/default/forced) via Matroska
  track numbers — no remux, no re-encode.
- Undo journals + post-apply verification for every batch.
- Debug logging ON by default; zero network access, zero telemetry.
