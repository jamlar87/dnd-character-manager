#!/usr/bin/env python3
"""Draw the app's icons — no image files in git history by hand, no emoji fonts to depend on.

A d20 in profile: a hexagonal outline with the three facet lines that make the shape read as a
die rather than a hexagon, in gold on the app's own dark navy. Regenerate with:

    .venv/bin/python3 scripts/make_icons.py

The manifest points at fixed filenames, so an icon change needs the name to change (or a
`?v=` on the manifest's icon URLs) or devices will keep the old one.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parent.parent / "static" / "icons"

BG = (26, 26, 46)          # --bg of the app's dark theme
BG_EDGE = (18, 18, 32)
GOLD = (212, 175, 55)


def _hexagon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Pointy-top hexagon: vertices at 30° + k·60°, which is what a d20 silhouette looks like."""
    return [(cx + r * math.cos(math.radians(30 + k * 60)),
             cy + r * math.sin(math.radians(30 + k * 60))) for k in range(6)]


def draw_icon(size: int, *, maskable: bool = False) -> Image.Image:
    # A maskable icon has to survive being cropped to a circle: keep the art inside the safe zone
    # (the middle 80%), and let the background run to the edges.
    scale = 0.52 if maskable else 0.66
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(size * 0.22) if not maskable else size // 2
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG, outline=BG_EDGE,
                        width=max(1, size // 64))

    cx = cy = size / 2
    r = size * 0.5 * scale
    verts = _hexagon(cx, cy, r)
    stroke = max(3, size // 26)

    # Lines rather than a polygon outline: Pillow's polygon stroke is uneven at the corners, and
    # an icon is exactly where that shows. joint="curve" keeps every vertex the same weight.
    d.line(verts + [verts[0]], fill=GOLD, width=stroke, joint="curve")

    # The Y: centre to alternating vertices. This is what makes the shape a die and not a hexagon.
    for k in (0, 2, 4):
        d.line([cx, cy, verts[k][0], verts[k][1]], fill=GOLD, width=stroke, joint="curve")

    # The eye: without it the hexagon plus a Y reads as a CUBE (the Y becomes a corner meeting at
    # the centre). The inscribed face triangle is what makes the shape a die — at 192px the
    # difference is the whole read — so it stays, in the same gold and slightly thinner.
    eye = []
    for k in (0, 2, 4):
        t = 0.44
        eye.append((cx + (verts[k][0] - cx) * t, cy + (verts[k][1] - cy) * t))
    d.line(eye + [eye[0]], fill=GOLD, width=max(2, stroke // 2 + 1), joint="curve")

    return img


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon-180.png", 180, False),
    ]
    for name, size, maskable in targets:
        img = draw_icon(size, maskable=maskable)
        img.save(STATIC / name)
        print(f"{name}: {size}x{size}{' (maskable)' if maskable else ''} -> {STATIC / name}")


if __name__ == "__main__":
    main()
