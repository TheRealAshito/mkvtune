# mkvtune

Pick which MKV audio/subtitle track wins — then batch-apply it.

mkvtune is a terminal UI (plus scriptable CLI) around mkvtoolnix that solves
one tiresome ritual: opening each episode, checking its track layout, and
poking `mkvpropedit` until the right subtitle track is the one that plays.
It edits track **flags only** (enabled / default / forced) in place — no
remux, no re-encode, milliseconds per file regardless of size.

- **You are always the one picking.** Auto-pick (`g`) only fills a pending
  plan with a proposal (Japanese audio + full-dialogue subtitles); every
  track choice can be overridden before anything is written.
- **Exclusive by default.** Picking a track makes it *the* one: it gets
  `flag-default=1` (and `flag-forced=1` for subtitles) and every other track
  of the same type gets its default and forced flags explicitly cleared, so
  nothing else can hijack playback. (mkvpropedit does not do this clearing
  by itself — mkvtune always spells it out.)
- **Safe by construction.** Preview before apply (exact commands shown),
  one journal per apply, one-key undo, post-apply verification re-scans
  every file and confirms the flags landed.
- **Zero network access, zero telemetry.** The code contains no networking
  at all — nothing to disable, nothing to strip.

## Install (Debian)

Requires: `mkvtoolnix` (`mkvmerge` + `mkvpropedit`) and Python ≥ 3.11.

```sh
sudo apt install mkvtoolnix
sudo apt install ./mkvtune_0.1.0-1_all.deb   # from the GitHub release assets
```

The .deb is self-contained (Python dependencies bundled) — no pip needed.

Alternative, from source:

```sh
pipx install "git+https://github.com/TheRealAshito/mkvtune@v0.1.0"
```

## Usage

```sh
mkvtune                  # TUI in the current folder (e.g. from mc)
mkvtune ~/anime/SomeShow # TUI in a specific folder
```

| Key        | Action |
|------------|--------------------------------------------------------------|
| `p`        | make the highlighted track THE default for its type (subs also get forced; other tracks' default+forced are cleared) |
| `g` / `G`  | auto-pick this file / all files (marked files only if any) — just a proposal |
| `e` `d` `f`| toggle enabled / default / forced on the highlighted track |
| `m`        | mark a file (limits `G` to marked files) |
| `P`        | preview: per-file diff + exact `mkvpropedit` command (dry run) |
| `!`        | apply pending changes (asks first, journals for undo) |
| `u`        | undo a previous apply |
| `r`        | rescan folder |
| `t`        | toggle dark / AMOLED pure-black theme |
| `D`        | diagnostics (tool paths/versions, dirs, debug toggle) |

CLI (for scripting):

```sh
mkvtune scan ~/anime/Show                 # track layout
mkvtune scan ~/anime/Show --json          # machine-readable
mkvtune apply ~/anime/Show --dry-run      # show what smart-pick would do
mkvtune apply ~/anime/Show                # smart-pick + apply + verify
mkvtune apply ~/anime/Show --pick a1 --pick s2   # manual: 1st audio, 2nd sub
mkvtune undo                              # restore the last apply
mkvtune undo --list                       # list journals
mkvtune policies                          # list smart-pick presets
mkvtune doctor                            # diagnostics
```

## Smart-pick

The auto-pick proposal prefers, per file:

- audio: language `jpn`/`ja`/`jap` (falls back to the first audio track),
- subtitles: full-dialogue tracks (name matches `full|dialog`, unnamed
  tracks count as full) and skips signs/songs/lyrics/credits/forced tracks;
  picked subs get default **and** forced.

Tunable via `~/.config/mkvtune/settings.json` and policy presets in
`~/.config/mkvtune/policies/*.json` (see `mkvtune policies`).

## Journals & undo

Every apply writes `~/.local/share/mkvtune/journals/<stamp>.json` with the
exact before-state of every touched flag. `mkvtune undo` (or `u` in the TUI)
restores it — also verified after the fact.

## Debug mode

Debug logging is ON by default (local file `~/.local/share/mkvtune/logs/mkvtune.log`,
every command line + stderr recorded). Toggle with `D` → `x` in the TUI.

## Notes

- Track selection uses Matroska track numbers (`track:@N`) resolved from
  `mkvmerge -J`, so it is robust against varying track orders between
  releases (PSG vs official subs etc.).
- `flag-enabled=0` on unwanted subtitles is available as an option
  (`disable_other_subs`) but off by default: clearing default+forced is
  enough to keep the right sub on while keeping the others switchable in
  your player.
