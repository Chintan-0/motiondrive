"""Derives the app icon (icon.ico / icon_*.png) from the user-supplied full
MotionDrive logo lockup (assets/logo_full.png), which includes the wordmark
and tagline. For a taskbar/window icon we only want the emblem (the hands +
M/D + steering wheel mark) cropped tightly and padded to a square, since text
is illegible at 16-32px.

Run: python assets/process_logo.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

OUT_DIR = Path(__file__).resolve().parent
SOURCE = OUT_DIR / "logo_full.png"


def extract_emblem() -> Image.Image:
    img = Image.open(SOURCE).convert("RGBA")
    arr = np.array(img)
    bg = arr[5, 5][:3].astype(int)

    # Search only the top portion of the lockup (well above the wordmark) to
    # isolate the hands/M-D/wheel emblem, with a safety gap before the text.
    text_cutoff = int(img.height * 0.60)
    region = arr[0:text_cutoff, :, :3].astype(int)
    diff = np.abs(region - bg).sum(axis=2)
    mask = diff > 40
    ys, xs = np.where(mask)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()

    pad_side = int((x1 - x0) * 0.06)
    pad_top = int((y1 - y0) * 0.10)
    pad_bottom = int((y1 - y0) * 0.04)
    x0, y0 = max(0, x0 - pad_side), max(0, y0 - pad_top)
    x1, y1 = min(img.width, x1 + pad_side), min(text_cutoff, y1 + pad_bottom)
    cropped = img.crop((x0, y0, x1, y1))

    side = max(cropped.width, cropped.height)
    bg_rgba = (int(bg[0]), int(bg[1]), int(bg[2]), 255)
    canvas = Image.new("RGBA", (side, side), bg_rgba)
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2), cropped)
    return canvas


def main() -> None:
    emblem = extract_emblem()
    emblem_1024 = emblem.resize((1024, 1024), Image.LANCZOS)
    emblem_1024.save(OUT_DIR / "icon_1024.png")
    emblem.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "icon_256.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [emblem_1024.resize((sz, sz), Image.LANCZOS) for sz in sizes]
    imgs[0].save(OUT_DIR / "icon.ico", format="ICO",
                 sizes=[(sz, sz) for sz in sizes], append_images=imgs[1:])
    print("Wrote icon.ico, icon_1024.png, icon_256.png from", SOURCE.name)


if __name__ == "__main__":
    main()
