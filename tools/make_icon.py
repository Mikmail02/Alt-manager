"""Regenerate assets/icon.{ico,icns,png} from scratch.

Run with: python tools/make_icon.py
Produces a simple CC monogram on dark background:
  - icon.ico  (Windows)
  - icon.icns (macOS)
  - icon.png  (fallback for tray/window icon on non-Windows)
Replace these with designer assets whenever you like; PyInstaller picks up
whatever is on disk.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

ICO_PATH = ASSETS / "icon.ico"
ICNS_PATH = ASSETS / "icon.icns"
PNG_PATH = ASSETS / "icon.png"

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
# macOS ICNS wants power-of-two sizes including 512 and 1024 (retina).
ICNS_SIZES = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)]

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
    ASSETS.mkdir(parents=True, exist_ok=True)

    ico_imgs = [render(s[0]) for s in ICO_SIZES]
    ico_imgs[0].save(ICO_PATH, format="ICO", sizes=ICO_SIZES, append_images=ico_imgs[1:])
    print(f"Wrote {ICO_PATH}")

    # High-res master, used by tray/window icon on non-Windows.
    master = render(512)
    master.save(PNG_PATH, format="PNG")
    print(f"Wrote {PNG_PATH}")

    # ICNS: Pillow handles the container when given a multi-size list.
    try:
        master_1024 = render(1024)
        master_1024.save(ICNS_PATH, format="ICNS", sizes=ICNS_SIZES)
        print(f"Wrote {ICNS_PATH}")
    except Exception as exc:
        # ICNS write support requires Pillow with libimagequant-like fallback
        # on some platforms. A missing .icns is non-fatal for Win/Linux builds.
        print(f"Skipped {ICNS_PATH}: {exc}")


if __name__ == "__main__":
    main()
