# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to a `MAJOR.MINOR.PATCH-beta` version string while it is
in beta.

## [0.1.0-beta] - 2026-10-23

First public beta.

### Added

- Command line tool `p3r-save-transfer` with `scan`, `convert`, `convert-all`,
  `verify` and `install` commands.
- Portable Windows GUI (`P3R-Save-Transfer.exe`): save list with level, play
  time, date and container type; destination detection with SteamID selection;
  backup and dry-run switches; progress with cancel; overwrite confirmation;
  help panel; log panel; Russian and English interface.
- Byte-exact conversion between the Microsoft Store GVAS container and the Steam
  obfuscation, with a round-trip check before anything is written.
- Automatic backup of the destination folder (`_p3r_backup_<timestamp>` with a
  SHA-256 manifest), enabled by default.
- `--onedir` PyInstaller build with two executables sharing one `_internal`
  folder, a build script and a portable zip layout.

### Known limitations

- Achievements are not transferred between Xbox and Steam.
- Cloud saves must be downloaded to disk first (the tool never touches accounts).
- Slot numbers are not stored in Microsoft Store containers, so slots are
  assigned by play time unless set explicitly.
- Converting back to the Microsoft Store format is provided for verification and
  manual experiments only.
- The executables are unsigned, so Windows SmartScreen warns on first launch.

[0.1.0-beta]: https://github.com/Thunderouse/p3r-save-transfer/releases/tag/v0.1.0-beta
