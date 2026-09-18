"""Generates the MotionDrive app icon set from the real high-resolution logo artwork (assets/logo.png).

Extracts the "MD" emblem, tight-crops it to its real non-transparent bounds
(removing wide empty margins), centers it on a square canvas with a safe ~5% margin
(so the emblem fills ~86-91% of the square icon canvas), and downsamples with
high-quality LANCZOS resampling at each target size into a proper multi-resolution .ico.

Run: python assets/generate_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).resolve().parent
SOURCE_LOGO = OUT_DIR / "logo.png"

# Non-transparent bounding box of just the "MD" wheel emblem within the logo artwork.
# Removes the wide empty margins so the emblem fills ~90% of the square icon canvas.
EMBLEM_REGION = (32, 51, 899, 872)

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _emblem_on_square_canvas(padding_frac: float = 0.05) -> Image.Image:
    """Crops the emblem artwork tightly and pastes it onto a square canvas with padding_frac margin."""
    source = Image.open(SOURCE_LOGO).convert("RGBA")
    emblem = source.crop(EMBLEM_REGION)

    # Tight crop to true non-transparent bounding box
    bbox = emblem.getbbox()
    if bbox is not None:
        emblem = emblem.crop(bbox)

    max_dim = max(emblem.width, emblem.height)
    margin = int(max_dim * padding_frac)
    canvas_side = max_dim + 2 * margin

    canvas = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    offset = ((canvas_side - emblem.width) // 2, (canvas_side - emblem.height) // 2)
    canvas.paste(emblem, offset, emblem)
    return canvas


def _resize_for(square: Image.Image, size: int) -> Image.Image:
    return square.resize((size, size), Image.LANCZOS)


def main() -> None:
    square = _emblem_on_square_canvas(padding_frac=0.05)

    square.resize((1024, 1024), Image.LANCZOS).save(OUT_DIR / "icon_1024.png")
    square.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "icon_256.png")

    largest = _resize_for(square, max(ICON_SIZES))
    largest.save(
        OUT_DIR / "icon.ico",
        format="ICO",
        sizes=[(sz, sz) for sz in ICON_SIZES],
    )
    print(f"Successfully generated icon.ico ({ICON_SIZES}), icon_1024.png, icon_256.png in {OUT_DIR}")


if __name__ == "__main__":
    main()
