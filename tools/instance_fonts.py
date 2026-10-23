"""Turn the upstream variable fonts into static, renamed instances.

Only needed when refreshing the assets; the resulting TTFs are committed.

    py -3 tools\\instance_fonts.py Oswald[wght].ttf Inter[opsz,wght].ttf

Both fonts are SIL OFL 1.1. Instancing is a modification, so the family names
are replaced with project-specific ones (the upstream names are not reused).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "fonts"

#: output file -> (source tag, axis values, family name, style)
TARGETS = {
    "Oswald-SemiBold.ttf": ("oswald", {"wght": 600}, "P3R Display", "Regular"),
    "Inter-Regular.ttf": ("inter", {"opsz": 14, "wght": 400}, "P3R Text", "Regular"),
    "Inter-Medium.ttf": ("inter", {"opsz": 14, "wght": 500}, "P3R Text Medium", "Regular"),
    "Inter-SemiBold.ttf": ("inter", {"opsz": 14, "wght": 600}, "P3R Text Semi", "Regular"),
}


def rename(font: TTFont, family: str, style: str) -> None:
    full = f"{family} {style}".strip()
    postscript = full.replace(" ", "")
    unique = f"{full}; P3R Save Transfer"
    records = {
        1: family,
        2: style,
        3: unique,
        4: full,
        6: postscript,
        16: family,
        17: style,
    }
    name_table = font["name"]
    name_table.names = []
    for name_id, value in records.items():
        name_table.setName(value, name_id, 3, 1, 0x409)
        name_table.setName(value, name_id, 1, 0, 0)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    sources = {"oswald": Path(argv[1]), "inter": Path(argv[2])}
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, (tag, axes, family, style) in TARGETS.items():
        font = TTFont(sources[tag])
        instantiateVariableFont(font, axes, inplace=True, updateFontNames=False)
        rename(font, family, style)
        target = OUT / filename
        font.save(target)
        print(f"{target.relative_to(ROOT)}  {target.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
