Beta release of a small Windows tool that converts **Persona 3 Reload** save
files from the Microsoft Store / Xbox Game Pass version to the Steam version.
Unofficial, personal-project scale, no installer.

## Requirements

- Windows 10 or Windows 11, x64
- Python is **not** needed - the archive is a self-contained `--onedir` build
- The UI is built on Tkinter, so WebView2 / .NET / VC++ redistributables are not
  required
- No administrator rights; everything happens in your own user folders

## How to use

1. Unpack the whole archive anywhere (Desktop, another drive, a USB stick).
2. Close the game and the Xbox app, start `P3R-Save-Transfer.exe` and press
   **Find automatically** (it reads
   `%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs`).
3. Tick the saves and press **Convert**; the Steam profile folder
   `%APPDATA%\Sega\P3R\Steam\<SteamID64>` is detected automatically, and existing
   files are only overwritten after confirmation. The destination folder is
   backed up first.

`p3r-save-transfer-cli.exe` in the same folder is the console version
(`scan`, `convert`, `convert-all`, `verify`, `install`).

## What was tested

- Automated tests and lint on Windows Server (`windows-latest` in GitHub Actions) and Ubuntu,
  Python 3.10 and 3.12.
- An end-to-end CLI run on Windows with generated save files: scan, convert,
  round-trip verification, install, hashes of the source folder unchanged.
- The round-trip property (encode then decode gives the original bytes) on files
  of many sizes, including 0 and 1 byte.

## What was not tested

- This exact archive on a physical Windows 10 / Windows 11 machine, and with a
  retail save followed by loading it in Steam. Please treat the first run as a
  test: the tool keeps a backup of the destination folder by default.
- The drag & drop handler and per-monitor DPI behaviour on real hardware.

## Known limitations

- Achievements do not transfer: Game Pass achievements stay on Xbox, Steam
  achievements start from the moment the converted save is loaded.
- Cloud saves must be on disk first (install and launch the game once with your
  own account, let it sync, close it). The tool never touches accounts.
- Microsoft Store containers do not store slot numbers, so slots are assigned by
  play time unless you pass `--slot`.
- Converting back (`--reverse`) produces a plain GVAS file for verification and
  manual experiments; there is no automatic install back into the Microsoft
  Store layout.

## SmartScreen

The executables are not code-signed, so Windows may show "Windows protected your
PC" on the first launch: **More info** -> **Run anyway**. Compare the SHA-256
below with the file you downloaded before running it.
