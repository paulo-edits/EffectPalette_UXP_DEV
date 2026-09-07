"""Generate the FX.palette app icon assets from a single vector-ish description.

PLACEHOLDER ART. The mark is the product's own prompt glyph -- the ">" the search field
shows -- plus a baseline underscore, on a rounded indigo/violet tile. It is a shape rather
than letterforms so it survives the 16 px "Installed apps" row in Windows Settings.

Delete this script once real artwork replaces companion/assets/fx_palette.*.

Usage:  python scripts/make_app_icon.py
Writes: companion/assets/fx_palette.ico  (16/24/32/48/64/128/256)
        companion/assets/fx_palette.png  (512, for Qt setWindowIcon and the tray)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[1] / "companion" / "assets"

# Master grid. Everything below is expressed on a 1024x1024 canvas and rendered at
# SUPERSAMPLE times that before being resized down, which is what smooths the edges.
GRID = 1024
SUPERSAMPLE = 4

TILE_RADIUS = 232
GRADIENT_START = (76, 91, 245)    # indigo, top-left
GRADIENT_END = (139, 49, 217)     # violet, bottom-right
FOREGROUND = (255, 255, 255, 255)

# Prompt glyph: the classic ">_" -- chevron plus a baseline underscore. Coordinates are
# chosen so the glyph's bounding box (including the round caps) is centred on the tile.
CHEVRON = ((291, 340), (471, 512), (291, 684))
CHEVRON_WIDTH = 78
CURSOR = (536, 661, 772, 723)
CURSOR_RADIUS = 31

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 512


def _diagonal_gradient(size: int) -> Image.Image:
    """A 45-degree linear gradient from GRADIENT_START to GRADIENT_END."""
    ramp = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # t runs 0..1 across the diagonal, so average the x and y ramps.
    t = (ramp[None, :] + ramp[:, None]) / 2.0
    start = np.array(GRADIENT_START, dtype=np.float32)
    end = np.array(GRADIENT_END, dtype=np.float32)
    rgb = start[None, None, :] + t[:, :, None] * (end - start)[None, None, :]
    return Image.fromarray(rgb.round().astype(np.uint8), mode="RGB")


def _scaled(value: float, scale: float) -> float:
    return value * scale


def render_master() -> Image.Image:
    size = GRID * SUPERSAMPLE
    scale = size / GRID

    tile = _diagonal_gradient(size).convert("RGBA")

    # Squircle-ish mask so the corners are transparent rather than gradient.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=_scaled(TILE_RADIUS, scale),
        fill=255,
    )
    tile.putalpha(mask)

    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)

    points = [(_scaled(x, scale), _scaled(y, scale)) for x, y in CHEVRON]
    width = _scaled(CHEVRON_WIDTH, scale)
    draw.line(points, fill=FOREGROUND, width=int(round(width)), joint="curve")
    # ImageDraw has no round line caps, so cap the three vertices by hand.
    for x, y in points:
        r = width / 2.0
        draw.ellipse((x - r, y - r, x + r, y + r), fill=FOREGROUND)

    draw.rounded_rectangle(
        tuple(_scaled(v, scale) for v in CURSOR),
        radius=_scaled(CURSOR_RADIUS, scale),
        fill=FOREGROUND,
    )

    tile.alpha_composite(glyph)
    return tile.resize((GRID, GRID), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render_master()

    png_path = ASSETS / "fx_palette.png"
    master.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS).save(png_path)

    # Pillow resamples each ICO frame itself, but doing it explicitly with LANCZOS keeps
    # the 16 px frame legible instead of muddy.
    ico_path = ASSETS / "fx_palette.ico"
    frames = [master.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    frames[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])

    print(f"wrote {png_path.relative_to(ASSETS.parents[1])} ({PNG_SIZE}px)")
    print(f"wrote {ico_path.relative_to(ASSETS.parents[1])} ({', '.join(str(s) for s in ICO_SIZES)})")


if __name__ == "__main__":
    main()
