Version **1.0**: a small Windows tool that converts **Persona 3 Reload** save
files from the Microsoft Store / Xbox Game Pass version to the Steam version.
Unofficial, personal-project scale, no installer.

## What is new in 1.0

The headline fix: a foreign file in your save folder no longer breaks anything.

Before (0.1.0-beta), a folder that contained `notes.txt`, `desktop.ini` or a
stray download produced an error per file, a non-zero exit code, and could even
crash the whole run:

```
desktop.ini      3     -    19 Б    ошибка: Неизвестный magic 0x68532E5B
notes.txt        5     -    24 Б    ошибка: Неизвестный magic 0x6E20796D
...
Успешно: 2 из 6
```

Now the same folder converts the saves and lists the rest with stable reasons:

```
Сконвертировано 2, пропущено 5.

Пропущено (5):
Файл             Причина     Размер  Пояснение
---------------  ----------  ------  --------------------------------------
empty.sav        empty       -       файл нулевого размера
Thumbs.db        os-junk     -       служебный файл Windows/Finder
notes.txt        not-a-save  9 Б     незнакомый magic 0x6E20796D, начало ...
SaveData003.sav  not-a-save  50 Б    незнакомый magic 0x20544F4E, начало ...
desktop.ini      os-junk     -       служебный файл Windows/Finder
```

Skip reasons: `not-a-save`, `empty`, `os-junk`, `temp-file`, `unreadable`,
`too-large` (over 32 MB, refused before it is read into memory), `no-access`.
`container.*` is counted as bookkeeping, not as a problem.

Also in 1.0:

- new exit code `3` (partial) and `--strict`, `--include-skipped`, `explain FILE`;
- `scan --json` is an object with `counts`, `saves` and `skipped`, and paths are
  relative to the scanned root, so the output is safe to paste into an issue;
- atomic writes with a read-back check, plus a free-space pre-check;
- a "Skipped (N)" section in the window and "converted N, skipped M" in the
  status line;
- the CLI, the JSON fields and `config.json` are frozen (see "Stability" in the
  README).

## Upgrade from 0.1.0-beta

Replace the folder: unpack the new archive over the old one, or into a new
folder. `config.json` is compatible as-is - the six keys did not change, no
migration is needed. Nothing else is written outside your user folders.

## Requirements

- Windows 10 21H2+ or Windows 11, x64
- Python is **not** needed - the archive is a self-contained `--onedir` build
- Tkinter UI: no WebView2, no .NET, no VC++ redistributable
- No administrator rights, no network access, no telemetry

## How to use

1. Unpack the whole archive anywhere (Desktop, another drive, a USB stick).
2. Close the game and the Xbox app, start `P3R-Save-Transfer.exe` and press
   **Find automatically** (it reads
   `%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs`).
3. Tick the saves and press **Convert**; the Steam profile folder
   `%APPDATA%\Sega\P3R\Steam\<SteamID64>` is detected automatically, existing
   files are only overwritten after confirmation, and the destination folder is
   backed up first.

`p3r-save-transfer-cli.exe` in the same folder is the console version
(`scan`, `convert`, `convert-all`, `verify`, `install`, `explain`).

## What was tested

- Automated tests and lint on Windows Server (`windows-latest` in GitHub Actions)
  and Ubuntu, Python 3.10 and 3.12: 260 tests, 90 % branch coverage of the
  package, 100 % of the codec.
- `tools/windows_smoke.py` on Windows: a tree with three generated saves and a
  `notes.txt` in every folder - scan, JSON shape, cp866 and cp1251 console
  output, convert-all (exit 3, both exes' magic), verify, reverse to
  byte-identical input, a path over 260 characters, a Cyrillic path, install,
  and unchanged hashes of the source tree.
- The round-trip property on 1000 random buffers (0-5000 bytes) plus the edge
  sizes 0, 1 and multiples of 31.
- The release archive is extracted on Windows in CI and the console exe is run
  from it (`--version`).

## What was not tested

- This archive on a physical **Windows 10 / Windows 11 desktop**, and a retail
  save loaded by the Steam build - everything below was verified on CI runners
  (Windows Server) with generated saves. Please treat the first real run as a
  test: the destination folder is backed up by default.
- Drag & drop and per-monitor DPI on real hardware.
- The GUI screenshots still show the 0.1.0-beta layout (they predate the
  "Skipped" section).

## Known limitations

- Achievements do not transfer.
- Cloud saves must be on disk first; the tool never touches accounts.
- Slot numbers are not stored in Microsoft Store containers, so slots are
  assigned by play time unless you pass `--slot`.
- A truncated save still round-trips, so `verify` cannot detect truncation.
- `--reverse` produces a plain GVAS file for verification; there is no automatic
  install back into the Microsoft Store layout.

## SmartScreen

The executables are not code-signed, so Windows may show "Windows protected your
PC" on the first launch: **More info** -> **Run anyway**. Compare the SHA-256
below with the file you downloaded before running it.
