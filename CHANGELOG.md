# Changelog

## 0.2.0 — 2026-11-17

Major UI rework (Midnight-Commander-shaped shell + duf-style visuals),
driven by field feedback on v0.1.x:

- Top pull-down menus (File / Pick / Batch / View / Help; F9) and an
  MC-style function-key bar (F1 Help, F2 Pick, F3 Browse, F4 Preview,
  F5 Apply, F8 Undo, F10 Quit).
- **Season-wide picking**: pressing p on a track now asks about scope and
  can set the default in EVERY episode carrying a matching track (same
  type, language, name and position) — one pick for 60+ episodes.
- In-app navigation: F3 file browser (directory tree) and ctrl+g
  "go to folder" — no more cd + relaunch to change folders.
- Visible scanning: MC-style progress dialog with progress bar and
  "N / M episodes" counter; explicit empty-folder status. A scan can no
  longer look like a hang.
- Layout: full-width episode list, middle-truncated names (episode tails
  stay visible), colored audio/sub chips, pending-change counts, colored
  flag dots in the tracks table, info bar with live counts.
- Track hover detail moved to the tracks pane title so status feedback
  (apply summaries) is never clobbered.

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
