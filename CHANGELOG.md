# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-28

Same code as `1.0.0-rc1`, promoted to a final release on request. The contract
is frozen: CLI commands, flags, defaults and exit codes, the JSON fields of
`scan --json` / `convert-all --json`, and the six keys of `config.json`.

## [1.0.0-rc1] - 2026-10-28

First release candidate: the CLI surface, the JSON fields and `config.json` are
frozen from here on.

### Fixed

- A foreign file in a save folder no longer aborts a scan or a batch
  conversion. Previously every file was treated as a save, so `notes.txt`,
  `desktop.ini` or a stray download produced `Неизвестный magic 0x...` errors, a
  non-zero exit code and, for a large file, an uncaught `MemoryError` raised
  from `discovery.py:157` (`fsutil.read_bytes`). Files are now classified before
  they are read: a save, Xbox bookkeeping (`container.*`) or
  `skipped:<reason>` (`not-a-save`, `empty`, `os-junk`, `temp-file`,
  `unreadable`, `too-large`, `no-access`). Nothing in a user folder is modified
  or deleted.
- A file larger than 32 MB is refused before it is read into memory.
- The GUI no longer starts the delayed first scan on top of an already finished
  one, which could wipe the list right after startup.

### Added

- `scan --strict`, `scan --include-skipped`, `scan --explain FILE` and the
  `explain` command; `convert-all --strict` and `--include-skipped`.
- Exit codes: `0` all done, `1` fatal, `2` usage, `3` partial (something
  converted, something skipped).
- `scan --json` now returns an object: `version`, `root`, `counts` (including
  `by_reason`), `saves` and `skipped`. Paths inside the report are relative to
  the scanned root, so the output is safe to paste into an issue.
- `convert-all --json` returns `converted`, `failed`, `skipped` and `files`.
- GUI: a dimmed "Пропущено (N)" section under the list, skip reasons in the
  empty state with a "Показать лог" button, and "Сконвертировано N, пропущено M"
  in the status line.
- Atomic result writing (`<target>.<pid>.tmp` → `fsync` → verify → `os.replace`)
  with a read-back check, so a full disk or a crash cannot leave a half-written
  save.
- Free-space pre-check for batch conversion (total size + 10 %).
- `SECURITY.md`, issue templates, a Stability section in both READMEs, FAQ,
  support matrix and a troubleshooting table keyed by skip reason.

### Changed

- The explicit-file error now reads `Это не сохранение P3R: первые байты 0x...`
  and includes the first 16 bytes in hex.
- `install` writes atomically and verifies the result.
- Tests: 260 passing, 90 % branch coverage of the package, 100 % of `codec.py`.

### Known limitations

- Achievements are not transferred between Xbox and Steam.
- Cloud saves must be downloaded to disk first (the tool never touches accounts).
- Slot numbers are not stored in Microsoft Store containers, so slots are
  assigned by play time unless set explicitly.
- A truncated save still round-trips byte-exactly (the wrapper is byte-wise), so
  `verify` cannot detect truncation - only the game can.
- Converting back to the Microsoft Store format is for verification and manual
  experiments only.
- The executables are unsigned, so Windows SmartScreen warns on first launch.

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

[1.0.0]: https://github.com/Thunderouse/p3r-save-transfer/releases/tag/v1.0.0
[1.0.0-rc1]: https://github.com/Thunderouse/p3r-save-transfer/releases/tag/v1.0.0-rc1
[0.1.0-beta]: https://github.com/Thunderouse/p3r-save-transfer/releases/tag/v0.1.0-beta
