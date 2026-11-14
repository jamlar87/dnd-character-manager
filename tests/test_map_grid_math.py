"""Pin the hex maths to the canvas' arithmetic.

services/map_grid.py is the one implementation; its twin is static/grid.js, and routes/maps.py calls
it for both the token snap and the fog cell key. If the two drift, a token sits on one hex while the
server reveals the one beside it — which is exactly the bug this test guards.

CLIENT_TABLE was produced by running the pre-unification static/vtt.js expressions (undo the
GridMath calls and the body is the same) in Node over these exact points, so the numbers in it are
what the canvas actually drew before the server was brought into line. The server must reproduce
them bit for bit, not merely closely: a last-digit difference is invisible on screen but can flip a
cube-round comparison at a hex corner and name the neighbouring cell.

Each row: (size, ox, oy, world_x, world_y, hex, snap_x, snap_y, turned_hex, turned_cx, turned_cy).
"""
import pytest

from services import map_grid

CLIENT_TABLE = [
    (30, 7, -13, 7, -13, '0,0',
     7, -13,
     '0,0', 7, -13),
    (30, 7, -13, 32.98076211353316, -13, '1,0',
     32.98076211353316, -13,
     '-1,1', 29.5, -25.99038105676658),
    (30, 7, -13, -5.990381056766578, 9.5, '-1,1',
     -5.990381056766578, 9.5,
     '1,0', 7, 12.980762113533157),
    (30, 7, -13, 71.95190528383289, 9.5, '2,1',
     71.95190528383289, 9.5,
     '-1,3', 74.5, -0.009618943233421717),
    (30, 7, -13, -44.96152422706631, 32, '-3,2',
     -44.96152422706631, 32,
     '3,-2', -38, 38.96152422706631),
    (30, 7, -13, 253.817240078565, 99.5, '7,5',
     253.817240078565, 99.5,
     '-1,11', 254.5, 103.9134295108992),
    (50, 0, 0, 0, 0, '0,0',
     0, 0,
     '0,0', 0, 0),
    (50, 0, 0, 43.30127018922193, 0, '1,0',
     43.30127018922193, 0,
     '-1,1', 37.5, -21.650635094610966),
    (50, 0, 0, -21.650635094610966, 37.5, '-1,1',
     -21.650635094610966, 37.5,
     '1,0', 0, 43.30127018922193),
    (50, 0, 0, 108.25317547305482, 37.5, '2,1',
     108.25317547305482, 37.5,
     '-1,3', 112.5, 21.650635094610966),
    (50, 0, 0, -86.60254037844386, 75, '-3,2',
     -86.60254037844386, 75,
     '3,-2', -75, 86.60254037844386),
    (50, 0, 0, 411.36206679760835, 187.5, '7,5',
     411.36206679760835, 187.5,
     '-1,11', 412.5, 194.8557158514987),
    (100, -21, 9, -21, 9, '0,0',
     -21, 9,
     '0,0', -21, 9),
    (100, -21, 9, 65.60254037844386, 9, '1,0',
     65.60254037844386, 9,
     '-1,1', 54, -34.30127018922193),
    (100, -21, 9, -64.30127018922192, 84, '-1,1',
     -64.30127018922192, 84,
     '1,0', -21, 95.60254037844386),
    (100, -21, 9, 195.50635094610965, 84, '2,1',
     195.50635094610965, 84,
     '-1,3', 204, 52.30127018922193),
    (100, -21, 9, -194.20508075688772, 159, '-3,2',
     -194.20508075688772, 159,
     '3,-2', -171, 182.20508075688772),
    (100, -21, 9, 801.7241335952167, 384, '7,5',
     801.7241335952167, 384,
     '-1,11', 804, 398.7114317029974),
]

# A spread that straddles the origin in all six directions, so the sign handling and the
# cube-round tie-breaks all get exercised.
HEXES = [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1), (1, -1), (-1, 1),
         (2, 1), (-3, 2), (4, -3), (-2, -4), (7, 5), (-7, -5)]


@pytest.mark.parametrize("size", [30, 50, 100])
def test_axial_round_trips_through_world(size):
    for q, r in HEXES:
        x, y = map_grid.axial_to_world(q, r, size)
        assert map_grid.world_to_hex(x, y, size) == (q, r), f"hex {(q, r)} at size {size}"


@pytest.mark.parametrize("size", [30, 50, 100])
def test_axial_round_trips_through_world_turned(size):
    for q, r in HEXES:
        x, y = map_grid.axial_to_world(q, r, size, turned=1)
        assert map_grid.world_to_hex(x, y, size, turned=1) == (q, r)


@pytest.mark.parametrize("size", [30, 50, 100])
def test_axial_round_trips_with_offset(size):
    for q, r in HEXES:
        x, y = map_grid.axial_to_world(q, r, size, 7, -13)
        assert map_grid.world_to_hex(x, y, size, 7, -13) == (q, r)


@pytest.mark.parametrize("size", [30, 50, 100])
def test_snap_returns_the_hex_centre(size):
    for q, r in HEXES:
        cx, cy = map_grid.axial_to_world(q, r, size, 11, -4)
        assert map_grid.snap_hex(cx, cy, size, 11, -4) == (cx, cy)


def test_hex_centre_and_key_agree():
    for q, r in HEXES:
        x, y = map_grid.axial_to_world(q, r, 50, -3, 6)
        assert map_grid.hex_cell_key(x, y, 50, -3, 6) == f"{q},{r}"
        assert map_grid.axial_to_world(*map_grid.world_to_hex(x, y, 50, -3, 6), 50, -3, 6) == (
            pytest.approx(x), pytest.approx(y))


@pytest.mark.parametrize("row", CLIENT_TABLE, ids=lambda row: f"{row[0]}-{row[5]}")
def test_matches_the_canvas_bit_for_bit(row):
    size, ox, oy, x, y, key, snap_x, snap_y, turned_key, turned_cx, turned_cy = row

    assert map_grid.hex_cell_key(x, y, size, ox, oy) == key
    assert map_grid.snap_hex(x, y, size, ox, oy) == (snap_x, snap_y)
    assert map_grid.world_to_hex(x, y, size, ox, oy) == tuple(int(v) for v in key.split(","))

    assert map_grid.hex_cell_key(x, y, size, ox, oy, turned=1) == turned_key
    tq, tr = map_grid.world_to_hex(x, y, size, ox, oy, turned=1)
    assert map_grid.axial_to_world(tq, tr, size, ox, oy, turned=1) == (turned_cx, turned_cy)


def test_round_half_up_goes_up():
    # Python's round() would send these halves to even; the canvas' Math.round sends them up, and
    # a token exactly on a cell boundary takes whichever answer this gives.
    assert map_grid.round_half_up(0.5) == 1
    assert map_grid.round_half_up(-0.5) == 0
    assert map_grid.round_half_up(1.5) == 2
    assert map_grid.round_half_up(-1.5) == -1
    assert map_grid.round_half_up(2.49) == 2


def test_maps_routes_delegate_to_the_shared_grid():
    """routes/maps.py must not keep a second copy: its helpers have to agree with map_grid."""
    from routes import maps

    row = {"grid_type": "hex", "grid_size": 50, "grid_offset_x": 0, "grid_offset_y": 0,
           "hex_turn": 0}
    turned = dict(row, hex_turn=1)
    for q, r in HEXES:
        x, y = map_grid.axial_to_world(q, r, 50)
        assert maps._snap_to_cell(x, y, row) == map_grid.snap_hex(x, y, 50)
        assert maps._cell_key_of(x, y, row) == map_grid.hex_cell_key(x, y, 50)
        assert maps._cell_key_of(x, y, turned) == map_grid.hex_cell_key(x, y, 50, turned=1)
    assert maps._round_half_up(0.5) == 1
