# Security policy

## Scope

P3R Save Transfer Tool is a local, offline utility. It has no network code, no
telemetry, no updater and no background service: it reads save files from your
own user folders, writes converted copies to a folder you choose, and keeps a
backup of the destination folder.

That also defines what counts as a security issue here:

- writing outside the directory the user selected (path traversal through a
  slot number, a crafted file name, or a symlink);
- overwriting or deleting data outside `--out-dir` / the chosen Steam profile;
- silently following links, junctions or reparse points out of the save tree;
- a denial of service caused by a crafted *file* (crash, unbounded memory,
  infinite loop);
- packaging problems in the release archive: a modified executable, an extra
  file, a checksum that does not match.

Not security issues, by design:

- the tool does not transfer achievements, licences, entitlements or DRM state;
- it does not log into Xbox/Microsoft/Steam accounts and cannot fetch cloud
  saves;
- it does not modify game files or the Microsoft Store container beyond reading
  it;
- converting a save you do not own is your problem, not a vulnerability.

## Reporting

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: repository → **Security** → **Report a
vulnerability** (or the "Report a vulnerability" button on the Security tab).
Include the version, the Windows build, what you did, and the smallest file or
command that reproduces it. Do not attach real save files, `config.json`,
screenshots with your user name or paths, or anything containing a SteamID64.

You can expect an acknowledgement within a few days. This is a personal project
without a bounty programme, so the honest promise is: a fix in the next patch
release and credit in the changelog if you want it.

## Verifying a release

Every release ships `P3R-Save-Transfer-<version>-win64-portable.zip` and a
`.sha256` file next to it:

```powershell
Get-FileHash .\P3R-Save-Transfer-1.0.0-rc1-win64-portable.zip -Algorithm SHA256
Get-Content .\P3R-Save-Transfer-1.0.0-rc1-win64-portable.zip.sha256
```

The executables are unsigned, so SmartScreen warns on first launch. Compare the
hash before choosing "Run anyway".
