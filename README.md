# P3R Save Transfer Tool

[![CI](https://github.com/Thunderouse/p3r-save-transfer/actions/workflows/ci.yml/badge.svg)](https://github.com/Thunderouse/p3r-save-transfer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Thunderouse/p3r-save-transfer?include_prereleases&label=release)](https://github.com/Thunderouse/p3r-save-transfer/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011%20x64-0078d4)](#requirements)

Convert **Persona 3 Reload** save files from the Microsoft Store / Xbox Game Pass
version to the Steam version, so you can continue your playthrough after buying
the game on Steam.

[Русская версия](README.ru.md) · [Changelog](CHANGELOG.md) · [Notice](NOTICE.md)

The tool only rewrites the **wrapper**: it removes (or applies) the small
obfuscation that the Steam build puts around an ordinary Unreal `GVAS` container.
Save contents - progress, inventory, social links, personas - are never parsed
for editing and never modified. The Microsoft Store folder is opened read-only.

```
MS Store:  <garbage name>   GVAS ......        (plain container)
Steam:     SaveDataNNN.sav  0B 65 00 15 ...    (same container, obfuscated)
```

## Screenshots

![Save list](docs/screenshots/gui-found.png)

![Conversion finished](docs/screenshots/gui-success.png)

## Requirements

- Windows 10 or Windows 11, x64
- No Python needed for the portable build
- The portable build uses Tkinter and ships its own runtime - WebView2, .NET and
  Visual C++ redistributables are **not** required
- No administrator rights: everything happens inside your user folders

## Install (portable zip)

1. Open the [latest release](https://github.com/Thunderouse/p3r-save-transfer/releases)
   and download `P3R-Save-Transfer-0.1.0-beta-win64-portable.zip` plus its
   `.sha256` file.
2. Unpack the **whole** folder anywhere - Desktop, `D:\Tools`, a USB stick. Paths
   with spaces and non-Latin characters are fine.
3. Verify the download (optional but recommended):

   ```powershell
   Get-FileHash .\P3R-Save-Transfer-0.1.0-beta-win64-portable.zip -Algorithm SHA256
   ```

   ```bat
   certutil -hashfile P3R-Save-Transfer-0.1.0-beta-win64-portable.zip SHA256
   ```

4. Run `P3R-Save-Transfer.exe`.

The executables are **not code-signed**, so SmartScreen may show "Windows
protected your PC" on the first start: choose **More info** -> **Run anyway**.
The hash in the release notes lets you confirm the file you downloaded is the one
that was published.

## Usage

### GUI (three clicks)

1. Close the game and the Xbox app.
2. Press **Find automatically** - the tool looks in
   `%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs`.
   You can also drop the `wgs` folder or a `.sav` file onto the window, or press
   `Ctrl+O`.
3. Tick the saves you need and press **Convert**. The Steam folder
   (`%APPDATA%\Sega\P3R\Steam\<SteamID64>`) is detected automatically; if several
   profiles exist, pick one in the **Account** list. If a target already exists,
   the tool asks before overwriting.
4. Press **Open folder** and start the Steam version.

Hotkeys: `Ctrl+O` choose a folder, `F5` rescan, `Ctrl+Enter` convert/cancel,
`Esc` close help / modal / cancel a run. `Tab` walks through every control,
`Enter`/`Space` activate.

Settings live in `config.json` next to the executable; if that folder is
read-only, the file goes to `%LOCALAPPDATA%\P3R-Save-Transfer\config.json` and the
log says so.

The tool never writes to the Microsoft Store save folder and creates a backup of
the destination folder before writing (`_p3r_backup_<timestamp>` with a SHA-256
manifest).

### CLI

`p3r-save-transfer-cli.exe` sits in the same folder and mirrors the Python CLI:

```powershell
.\p3r-save-transfer-cli.exe scan
.\p3r-save-transfer-cli.exe convert-all --wgs "$env:LOCALAPPDATA\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs" --out-dir "$env:USERPROFILE\p3r_out" --dry-run
.\p3r-save-transfer-cli.exe verify "$env:USERPROFILE\p3r_out\SaveData001.sav"
```

```bat
p3r-save-transfer-cli.exe scan
p3r-save-transfer-cli.exe convert-all --wgs "%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs" --out-dir "%USERPROFILE%\p3r_out"
p3r-save-transfer-cli.exe install --in "%USERPROFILE%\p3r_out\SaveData001.sav" --slot 1
```

From source, `py -3 -m pip install .` gives the `p3r-save-transfer` command.

| Command | What it does |
|---|---|
| `scan [--wgs PATH] [--json] [--steam]` | find saves, show slot, date, level, play time, size, type |
| `convert --in FILE --out-dir DIR [--slot N] [--reverse] [--dry-run] [--force]` | convert one file |
| `convert-all --wgs PATH --out-dir DIR [--dry-run] [--force]` | convert everything found |
| `verify FILE...` | check magic, size and a byte-exact round-trip |
| `install --in FILE --slot N [--steam-id ID]` | copy straight into the Steam profile |

Exit codes: `0` success, `1` problem (locked file, busy slot, failed check),
`2` bad arguments.

## How it works

A Steam save is the same Unreal `GVAS` container with two tiny transforms applied
to every byte, using a fixed 31-byte key that is identical in every copy of the
game:

1. swap bit pairs 0-1 and 4-5 inside the byte;
2. XOR the result with the next key byte (the key repeats).

Decoding does the XOR first and then the same bit swap. The transform is its own
inverse and never changes the file size, so conversion is a lossless
byte-for-byte operation - and the tool proves it: before writing anything it
applies the opposite transform to the result and compares it with the input.
`p3r-save-transfer verify` exposes the same check for any file.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `File is locked or unavailable` | Close the game and the Xbox app, wait a few seconds, retry. |
| `wgs folder not found` | The Microsoft Store version was never launched on this PC, or `LOCALAPPDATA` points to another drive. Pass `--wgs` or pick the folder manually. |
| `Unknown magic 0x...` | The file is not a P3R save (or it is a `container.*` service file). |
| `Slot N is already taken` | Use the suggested free slot, or allow overwriting (`--force` creates a backup first). |
| `n/a` in the metadata columns | That field could not be read for this save-game version. Conversion still works. |
| Mojibake in a redirected file | The CLI writes UTF-8 when output is redirected; open the file in a UTF-8 editor or run `chcp 65001` first. |

## Known limitations

- **Achievements are not transferred.** Game Pass achievements stay on Xbox;
  Steam achievements start from the moment you load the converted save.
- **Cloud saves must be on disk first.** Install and launch the game once with
  your own Xbox account, let it sync, close it, then run this tool. The tool never
  logs into accounts.
- **Slots are not stored in Microsoft Store containers.** `convert-all` numbers
  them by play time; pass `--slot` explicitly if you care about the order.
- **Converting back** (`--reverse`) produces a plain `GVAS` file. This is meant for
  verification and manual experiments; the Microsoft Store layout also needs its
  `container.*` service file, so there is no automatic "install to Xbox".
- **This is beta software** built and tested by one person on a limited set of
  saves. Keep your own backups.

## Disclaimer

Unofficial fan tool. Not affiliated with Atlus, SEGA, Microsoft or Valve; all
trademarks belong to their owners. The repository contains no game assets, no
game fonts and no save files. It is meant for converting **your own** saves
between copies of the game you own, and it is not a DRM circumvention tool. See
[NOTICE.md](NOTICE.md).

## Development

```powershell
py -3 -m pip install -e ".[dev]"
py -3 -m pytest
py -3 -m ruff check .
py -3 tools\windows_smoke.py     # Windows-only end-to-end check
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

Layout: `src/p3r_transfer` (engine: codec, discovery, convert, meta, CLI),
`ui/` (Tkinter window over `ui/bridge.py`), `tools/` (build, guard, smoke test),
`tests/`.

## License

MIT - see [LICENSE](LICENSE).
