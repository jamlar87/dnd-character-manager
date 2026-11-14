/* grid.js — the ONE copy of the hex-grid arithmetic, shared by the canvas and the server.
 *
 * The canvas (static/vtt.js) has to turn four things into axial hex coordinates — a token's drop
 * point, a fog cell's name, a cell's centre, and a lattice position — and the server has to make
 * the same conversion to decide which fog cell a token of the party's is standing on
 * (services/map_grid.py, used by routes/maps.py). Those used to be two hand-copied blocks of
 * arithmetic. They drifted: the server's copy ignored the hex orientation, so on a turned map a
 * token and the fog cell under it disagreed by a whole hex. The arithmetic now lives here, the
 * Python mirror lives in services/map_grid.py, and tests/test_map_grid_math.py pins the two to
 * the same numbers.
 *
 * World space is image pixels; a token's (x, y) is its CENTRE. Pointy-top axial layout with hex
 * radius R = size / 2, i.e. `size` is the grid setting, not the radius:
 *     x = sqrt(3) * R * (q + r / 2) + ox
 *     y = 1.5 * R * r + oy
 *     q = (sqrt(3)/3 * (x - ox) - 1/3 * (y - oy)) / R ; r = (2/3 * (y - oy)) / R
 * A turned grid (hex_turn) transposes the axes about the grid origin, so both directions go through
 * the same swap — see frame() below and the comment in vtt.js about why a transpose, not a rotation.
 *
 * Plain classic script: it defines window.GridMath and also exports itself for Node, because the
 * canvas is served without a build step and nothing here may require one.
 */
(function (root) {
  'use strict';

  var SQRT3 = Math.sqrt(3);

  // A transpose, not a rotation, and its own inverse — so one function serves both directions.
  function frame(x, y, turned) { return turned ? [y, x] : [x, y]; }

  // A point in world space -> fractional axial coordinates in lattice space.
  function worldToAxial(x, y, opts) {
    var R = opts.size / 2;
    var f = frame(x - (opts.ox || 0), y - (opts.oy || 0), opts.turned ? 1 : 0);
    var hx = f[0], hy = f[1];
    return [(SQRT3 / 3 * hx - 1 / 3 * hy) / R, (2 / 3 * hy) / R];
  }

  // Fractional axial -> the centre of the hex as a world point. Offset is added after the frame,
  // because it belongs to the caller's frame and not to the lattice.
  function axialToWorld(q, r, opts) {
    var R = opts.size / 2;
    var w = frame(SQRT3 * R * (q + r / 2), 1.5 * R * r, opts.turned ? 1 : 0);
    return [w[0] + (opts.ox || 0), w[1] + (opts.oy || 0)];
  }

  // Cube rounding: snap fractional axial coordinates to the nearest whole hex.
  function cubeRound(q, r) {
    var cx = q, cz = r, cy = -cx - cz;
    var rx = Math.round(cx), ry = Math.round(cy), rz = Math.round(cz);
    var dx = Math.abs(rx - cx), dy = Math.abs(ry - cy), dz = Math.abs(rz - cz);
    if (dx > dy && dx > dz) rx = -ry - rz; else if (dy > dz) ry = -rx - rz; else rz = -rx - ry;
    return [rx, rz];
  }

  // World point -> the whole hex it falls in.
  function worldToHex(x, y, opts) {
    var a = worldToAxial(x, y, opts);
    return cubeRound(a[0], a[1]);
  }

  // The fog/draw key for a world point, and the inverse: a key's centre (null if the key is junk).
  function hexKey(x, y, opts) {
    var a = worldToHex(x, y, opts);
    return a[0] + ',' + a[1];
  }

  function hexCentre(key, opts) {
    var parts = String(key).split(',');
    var q = parseFloat(parts[0]), r = parseFloat(parts[1]);
    if (isNaN(q) || isNaN(r)) return null;
    return axialToWorld(q, r, opts);
  }

  // Where a dropped token lands: the centre of the hex it was dropped on.
  function hexSnap(x, y, opts) {
    var a = worldToHex(x, y, opts);
    return axialToWorld(a[0], a[1], opts);
  }

  var api = {
    frame: frame,
    worldToAxial: worldToAxial,
    axialToWorld: axialToWorld,
    cubeRound: cubeRound,
    worldToHex: worldToHex,
    hexKey: hexKey,
    hexCentre: hexCentre,
    hexSnap: hexSnap
  };

  root.GridMath = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
