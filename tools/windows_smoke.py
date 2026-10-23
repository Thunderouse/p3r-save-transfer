"""Windows end-to-end smoke test on synthetic saves.

Run on a real Windows 10/11 machine (or in CI) from the repository root:

    py -3 tools\\windows_smoke.py

It exercises what Linux CI cannot: ``%LOCALAPPDATA%``/``%APPDATA%`` lookup,
260+ character paths, a redirected console with code page 866, Cyrillic
directory names, and the console script entry point.  Nothing here touches real
save files: everything is generated under %TEMP% and removed afterwards.

Exit code 0 = all checks passed.  Every failed check is printed as FAIL.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TESTS))

# The runner may use a legacy code page; keep the harness itself from crashing
# on the Cyrillic summary line after all checks have already passed.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import synth  # noqa: E402
from p3r_transfer import codec, fsutil, meta  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL  {name}  {detail}")


def run_tool(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "p3r_transfer", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def long_dir(base: Path, segments: int = 5, length: int = 45) -> Path:
    """Create a directory deeper than MAX_PATH using the \\\\?\\ prefix."""
    path = base
    for index in range(segments):
        path = path / (f"segment{index}_" + "x" * length)
        os.makedirs(fsutil.extended_path(path), exist_ok=True)
    return path


def main() -> int:
    if os.name != "nt":
        print("Этот скрипт рассчитан на Windows: пути LOCALAPPDATA/APPDATA и cp866.")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="p3r_smoke_"))
    local = tmp / "LocalAppData"
    roaming = tmp / "Roaming"
    wgs = local / "Packages" / "SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt" / "SystemAppData" / "wgs"
    user = wgs / "25354ABCDEF01234"
    steam_id = "11111111111111111"
    steam = roaming / "Sega" / "P3R" / "Steam" / steam_id
    steam.mkdir(parents=True)
    guid = "8F3A1C2E-0B4D-4E6F-9A11-223344556677"

    for index, seconds in enumerate((600, 3600, 72000), start=1):
        leaf = user / f"0000000{index}-1111-2222-3333-4444444444{index}" / guid
        leaf.mkdir(parents=True)
        words = {
            synth.word_index("play_time"): seconds * meta.TICKS_PER_SECOND,
            synth.word_index("level"): 3 + index,
            synth.word_index("difficulty"): synth.DIFFICULTY_NORMAL_WITH_NOISE,
        }
        (leaf / f"C0FFEE0000000000000000000000000{index}").write_bytes(synth.save(words))
        (leaf / "container.0000").write_bytes(b"\x00" * 64)

    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(local)
    env["APPDATA"] = str(roaming)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(TESTS)])
    env.pop("PYTHONIOENCODING", None)

    source_hashes = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in wgs.rglob("*")
        if path.is_file()
    }

    # 1. scan --json finds all three saves and reads their metadata
    result = run_tool("scan", "--json", env=env)
    check("scan: exit code 0", result.returncode == 0, result.stderr)
    records = json.loads(result.stdout or "[]")
    check("scan: 3 saves found", len(records) == 3, f"got {len(records)}")
    check(
        "scan: metadata parsed",
        any(record["meta"] and record["meta"]["level"] for record in records),
        json.dumps(records[:1], ensure_ascii=False)[:200],
    )
    check("scan: container GUID", all(r["container_guid"] == guid for r in records))

    # 2. Legacy console code pages must not raise UnicodeEncodeError.
    #    PYTHONIOENCODING is what the interpreter uses when stdout is not a real
    #    console, so it reproduces the cp866/cp1251 write path exactly.
    for codepage in ("cp866", "cp1251"):
        legacy = dict(env, PYTHONIOENCODING=codepage)
        con = run_tool("scan", "--wgs", str(wgs), env=legacy)
        check(f"{codepage}: exit code 0", con.returncode == 0, con.stderr[-400:])
        check(
            f"{codepage}: Russian text intact",
            "Найдено сохранений: 3" in con.stdout,
            con.stdout[:300],
        )

    # 3. convert-all
    out = tmp / "out"
    result = run_tool("convert-all", "--wgs", str(wgs), "--out-dir", str(out), env=env)
    check("convert-all: exit code 0", result.returncode == 0, result.stderr)
    produced = sorted(out.glob("SaveData*.sav"))
    check("convert-all: 3 files", len(produced) == 3, str([p.name for p in produced]))
    if produced:
        magic = int.from_bytes(produced[0].read_bytes()[:4], "little")
        check("convert-all: sealed magic 0x0B650015", magic == 0x0B650015, hex(magic))

    # 4. verify
    result = run_tool("verify", *[str(p) for p in produced], env=env)
    check("verify: exit code 0", result.returncode == 0, result.stderr + result.stdout)

    # 5. reverse must restore the original bytes
    back = tmp / "back"
    result = run_tool(
        "convert",
        "--in",
        str(produced[0]),
        "--out-dir",
        str(back),
        "--slot",
        "1",
        "--reverse",
        env=env,
    )
    check("reverse: exit code 0", result.returncode == 0, result.stderr)
    restored = (back / "SaveData001.sav").read_bytes()
    originals = [
        path.read_bytes()
        for path in wgs.rglob("*")
        if path.is_file() and not path.name.startswith("container")
    ]
    check("reverse: byte-identical to an MS file", restored in originals)

    # 6. long path (> 260 chars) for the output directory
    deep = long_dir(tmp)
    result = run_tool(
        "convert", "--in", str(produced[0]), "--out-dir", str(deep), "--slot", "1", env=env
    )
    target = deep / "SaveData001.sav"
    check("long path: exit code 0", result.returncode == 0, result.stderr)
    check(
        "long path: > 260 chars",
        len(str(target)) > 260 and os.path.exists(fsutil.extended_path(target)),
        f"{len(str(target))} chars",
    )

    # 7. Cyrillic user name in the destination path
    cyrillic = tmp / "Пользователь Иван" / "Сохранения"
    result = run_tool(
        "convert", "--in", str(produced[0]), "--out-dir", str(cyrillic), "--slot", "1", env=env
    )
    check("cyrillic path: exit code 0", result.returncode == 0, result.stderr)
    check("cyrillic path: file written", (cyrillic / "SaveData001.sav").exists())

    # 8. install into the Steam tree
    result = run_tool("install", "--in", str(produced[0]), "--slot", "4", env=env)
    installed = steam / "SaveData004.sav"
    check("install: exit code 0", result.returncode == 0, result.stderr)
    check("install: file present", installed.exists())
    if installed.exists():
        check(
            "install: sealed magic",
            int.from_bytes(installed.read_bytes()[:4], "little") == codec.OBFUSCATED_MAGIC_LE,
        )

    # 9. sources untouched
    after = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in wgs.rglob("*")
        if path.is_file()
    }
    check("wgs: hashes unchanged", source_hashes == after)

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"Проверок: {CHECKS}, провалов: {len(FAILURES)}")
    if FAILURES:
        print("Провалено: " + ", ".join(FAILURES))
        return 1
    print("Все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
