"""Draw the app icon (simple geometry, no game assets) and write assets/icon.ico.

py -3 tools\\make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"

BG = (5, 7, 14, 255)
PANEL = (11, 18, 38, 255)
BLUE = (31, 107, 255, 255)
CYAN = (55, 224, 255, 255)
WHITE = (242, 245, 255, 255)


def draw(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    def p(value: float) -> float:
        return value * scale

    pen.rounded_rectangle([p(4), p(4), p(252), p(252)], radius=p(26), fill=BG)
    # cut-corner plate
    pen.polygon(
        [
            (p(30), p(30)),
            (p(200), p(30)),
            (p(226), p(56)),
            (p(226), p(226)),
            (p(56), p(226)),
            (p(30), p(200)),
        ],
        fill=PANEL,
        outline=(255, 255, 255, 40),
    )
    # two electric shards
    pen.polygon([(p(70), p(196)), (p(120), p(58)), (p(152), p(58)), (p(102), p(196))], fill=BLUE)
    pen.polygon([(p(126), p(196)), (p(176), p(58)), (p(196), p(58)), (p(146), p(196))], fill=CYAN)
    # white notch
    pen.rectangle([p(76), p(208), p(186), p(218)], fill=WHITE)
    return image


def main() -> int:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw(size) for size in sizes]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])
    print(f"{OUT} {OUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
