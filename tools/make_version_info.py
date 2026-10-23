"""Write a PyInstaller ``--version-file`` resource so the exe shows a version.

py -3 tools\\make_version_info.py 0.2.0 build\\version_info.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, 0),
    prodvers=({a}, {b}, {c}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'P3R Save Transfer'),
         StringStruct('FileDescription', 'Persona 3 Reload save transfer'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', '{name}'),
         StringStruct('OriginalFilename', '{name}.exe'),
         StringStruct('ProductName', 'P3R Save Transfer'),
         StringStruct('ProductVersion', '{version}'),
         StringStruct('LegalCopyright', 'MIT licence. No game assets included.')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    version, destination = argv[1], Path(argv[2])
    # "0.1.0-beta" -> (0, 1, 0): Windows resources only accept four numbers
    parts = [int(re.match(r"\d+", piece).group()) for piece in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    body = TEMPLATE.format(
        a=parts[0], b=parts[1], c=parts[2], version=version, name="P3R-Save-Transfer"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    # a second resource for the CLI exe name
    cli = destination.with_name("version_info_cli.txt")
    cli.write_text(
        TEMPLATE.format(
            a=parts[0], b=parts[1], c=parts[2], version=version, name="p3r-save-transfer-cli"
        ),
        encoding="utf-8",
    )
    print(f"{destination} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
