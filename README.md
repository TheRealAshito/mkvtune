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

Target: a Debian box (tested on bookworm, Python 3.11+). Needs `mkvtoolnix`
(`mkvmerge` + `mkvpropedit`) for the actual work.

### Option A — .deb package (recommended)

One copy-paste block. The `sha256sum -c` line proves the download arrived
intact before you install anything:

```sh
sudo apt install mkvtoolnix
cd ~
curl -L -o mkvtune_0.1.1-1_all.deb https://github.com/TheRealAshito/mkvtune/releases/download/v0.1.1/mkvtune_0.1.1-1_all.deb
echo "95561641d8ade9f80fd91f3d6af770a9f981071acefa5b2054f6ea7b79c8dbef  mkvtune_0.1.1-1_all.deb" | sha256sum -c -
sudo apt install ./mkvtune_0.1.1-1_all.deb
mkvtune doctor
```

- `curl -L` is required: GitHub release downloads redirect, and plain
  `curl` saves an empty file, which makes apt fail with
  "Invalid archive signature".
- The .deb is self-contained (Python dependencies bundled) — no pip needed,
  and `apt install ./file.deb` reads the local file directly (no apt repo).
- `mkvtune doctor` should list mkvmerge/mkvpropedit with their versions.
  If it reports them missing: `sudo apt install mkvtoolnix`.

### Option B — pipx (no root, isolated venv)

```sh
sudo apt install mkvtoolnix
pipx install "git+https://github.com/TheRealAshito/mkvtune@v0.1.1"
pipx ensurepath
exec $SHELL
mkvtune doctor
```

If you skip `pipx ensurepath`, either run `~/.local/bin/mkvtune` or add
`export PATH="$HOME/.local/bin:$PATH"` to your shell's rc file.

### First run

```sh
cd /path/to/anime/folder    # e.g. navigate there in mc, then run mkvtune
mkvtune                     # TUI in the current folder
mkvtune /path/to/folder     # or point it at a folder directly
```

### Updating

- pipx: `pipx install --force "git+https://github.com/TheRealAshito/mkvtune@v<new-version>"`
- .deb: download the newer asset from the
  [releases page](https://github.com/TheRealAshito/mkvtune/releases) and
  `sudo apt install ./mkvtune_<version>_all.deb` again.

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

## Troubleshooting

- **apt: "Invalid archive signature" / "could not locate member
  control.tar…"** — the file on disk isn't the real .deb (empty or HTML,
  typically `curl` without `-L`). Re-download with the Option A block and
  confirm the `sha256sum -c` line prints `OK` before installing.
- **`mkvtune: command not found` after pipx** — `~/.local/bin` isn't on
  PATH. Run `pipx ensurepath` and re-login (or use `~/.local/bin/mkvtune`).
- **`mkvtune doctor` reports `mkvmerge not found`** — install mkvtoolnix:
  `sudo apt install mkvtoolnix`.
- **Crash with `AttributeError: … 'NoneType' … row_key` (v0.1.0)** — fixed
  in v0.1.1; update (see Updating above).
- **Keys do nothing right at startup** — the folder is still being scanned;
  wait for the status bar to show "N file(s) loaded".
