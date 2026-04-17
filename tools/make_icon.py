"""Regenerate assets/icon.ico from scratch.

Run with: python tools/make_icon.py
Produces a simple CC monogram on dark background in 16/32/48/64/128/256 px.
Replace assets/icon.ico with a designer asset whenever you like; PyInstaller
picks up whatever is on disk.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"

SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

BG = (24, 24, 27)
FG = (228, 228, 231)
ACCENT = (16, 185, 129)


def render(size):
    img = Image.new("RGBA", (size, size), BG + (255,))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 10)
    draw.rectangle([pad, pad, size - pad, size - pad], outline=ACCENT, width=max(1, size // 24))
    try:
        font = ImageFont.truetype("arial.ttf", size // 2)
    except OSError:
        font = ImageFont.load_default()
    text = "CC"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=FG, font=font)
    return img


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [render(s[0]) for s in SIZES]
    images[0].save(OUT, format="ICO", sizes=SIZES, append_images=images[1:])
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
