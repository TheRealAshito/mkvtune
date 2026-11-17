# Changelog

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
