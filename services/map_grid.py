"""Hex-grid geometry — the Python side of static/grid.js.

The canvas decides which hex a point falls in (to name a fog cell, to snap a token) and the server
has to reach the same verdict when it projects the party's view, otherwise a token and the fog it
stands in disagree by a whole hex the moment the grid is nudged or turned. Both sides therefore use
ONE piece of arithmetic: the browser copy is static/grid.js, this is its mirror, and
tests/test_map_grid_math.py pins them to the same numbers.

World space is image pixels and a token's (x, y) is its CENTRE. Pointy-top axial layout with hex
radius R = size / 2, where `size` is the grid setting (not the radius):

    x = sqrt(3) * R * (q + r / 2) + ox
    y = 1.5 * R * r + oy
    q = (sqrt(3)/3 * (x - ox) - 1/3 * (y - oy)) / R
    r = (2/3 * (y - oy)) / R

A turned grid (dm_maps.hex_turn) transposes the axes about the grid origin — see hex_frame — so both
directions pass through the same swap. Every expression here is written in the same order as its
JavaScript twin so the two produce bit-identical floats, not merely close ones.
"""
from __future__ import annotations

import math

SQRT3 = 3 ** 0.5


def round_half_up(value: float) -> int:
    """JavaScript's Math.round: halves go UP, not to even (Python's round() would go to even)."""
    return int(math.floor(value + 0.5))


def hex_frame(x: float, y: float, turned: int = 0) -> tuple[float, float]:
    """Map a point between the canvas frame and the lattice frame. A transpose, and its own inverse."""
    return (y, x) if turned else (x, y)


def hex_radius(size: float) -> float:
    """The hex radius implied by the grid `size` setting."""
    return size / 2.0


def world_to_axial(x: float, y: float, size: float, ox: float = 0.0, oy: float = 0.0,
                   turned: int = 0) -> tuple[float, float]:
    """A world point -> fractional axial coordinates."""
    radius = hex_radius(size)
    gx, gy = hex_frame(x - ox, y - oy, turned)
    q = (SQRT3 / 3 * gx - 1 / 3 * gy) / radius
    r = (2 / 3 * gy) / radius
    return (q, r)


def axial_round(q: float, r: float) -> tuple[int, int]:
    """Cube rounding: fractional axial coordinates -> the nearest whole hex."""
    cx, cz = q, r
    cy = -cx - cz
    rx = round_half_up(cx)
    ry = round_half_up(cy)
    rz = round_half_up(cz)
    dx = abs(rx - cx)
    dy = abs(ry - cy)
    dz = abs(rz - cz)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return (rx, rz)


def axial_to_world(q: float, r: float, size: float, ox: float = 0.0, oy: float = 0.0,
                   turned: int = 0) -> tuple[float, float]:
    """A hex -> the world point at its centre. The offset is added after the frame, because it
    belongs to the caller's frame and not to the lattice."""
    radius = hex_radius(size)
    wx, wy = hex_frame(SQRT3 * radius * (q + r / 2), 1.5 * radius * r, turned)
    return (wx + ox, wy + oy)


def world_to_hex(x: float, y: float, size: float, ox: float = 0.0, oy: float = 0.0,
                 turned: int = 0) -> tuple[int, int]:
    """A world point -> the whole hex it falls in."""
    q, r = world_to_axial(x, y, size, ox, oy, turned)
    return axial_round(q, r)


def snap_hex(x: float, y: float, size: float, ox: float = 0.0, oy: float = 0.0,
             turned: int = 0) -> tuple[float, float]:
    """Where a token dropped at (x, y) belongs: the centre of that hex."""
    q, r = world_to_hex(x, y, size, ox, oy, turned)
    return axial_to_world(q, r, size, ox, oy, turned)


def hex_cell_key(x: float, y: float, size: float, ox: float = 0.0, oy: float = 0.0,
                 turned: int = 0) -> str:
    """The fog/draw key of the hex a world point falls in — the same string the canvas writes."""
    q, r = world_to_hex(x, y, size, ox, oy, turned)
    return f"{q},{r}"
