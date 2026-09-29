/* vtt.js — the map canvas: pan/zoom, square or hex grid, tokens, camera memory.
 *
 * The spatial half of the DM tools. Everything it draws already exists elsewhere in the app:
 * token art comes from /api/ref-image/{kind}/{name} (creatures, NPCs) or
 * /api/character/{id}/portrait-image (PCs), the map background is a file under /static/maps/,
 * and placements are rows in dm_map_tokens. This file is only the view and the input.
 *
 * Coordinates: world space is image pixels. A token's (x, y) is its CENTRE, so the same value
 * means the same thing under a square grid, a hex grid, or no grid at all.
 *
 * Contract used by tests/test_map_canvas.py: window.VTT with init/redraw/zoomBy/fit/
 * toggleGrid/setGridType/toggleSnap/addToken/saveNow/screenToWorld/worldToScreen, a
 * `vtt-dirty` marker while a save is pending, and a debounced save that flushes on pagehide.
 */
(function () {
  'use strict';

  var IMG_CACHE = {};
  var SAVE_DELAY = 600;
  var HEX_R = function () { return state.grid.size / 2; };

  var state = {
    map: null,
    tokens: [],
    camera: { x: 0, y: 0, zoom: 1 },
    grid: { on: true, type: 'square', size: 50, ox: 0, oy: 0 },
    snap: true,
    selected: null,
    drag: null,
    panning: null,
    readOnly: false,           // true on the player view: no input, nothing DM-only
    pokeChannel: null,
    tool: 'select',            // 'select' | 'fog' | 'draw'
    fog: {},                   // revealed cell keys — an object stands in for a Set here
    fogOn: false,
    strokes: [],
    pen: { color: '#ff5555', width: 4 },
    painting: null,
    stroke: null,
    fogBrush: 'cell',          // 'cell' | 'rect' | 'circle'
    marquee: null,             // rect/circle in progress: {shape, x0, y0, x1, y1}
    ruler: null,               // measure tool: {ax, ay, bx, by} — transient, never saved
    feetPerCell: 5,
    layerDirty: false,
    layerTimer: null,
    dirty: false,
    saveTimer: null,
    raf: null,
    bg: null
  };

  var canvas, ctx, host;

  function $(id) { return document.getElementById(id); }

  // ── geometry ────────────────────────────────────────────────────────────────────────
  function worldToScreen(x, y) {
    return [(x + state.camera.x) * state.camera.zoom, (y + state.camera.y) * state.camera.zoom];
  }
  function screenToWorld(sx, sy) {
    return [sx / state.camera.zoom - state.camera.x, sy / state.camera.zoom - state.camera.y];
  }

  function snapPoint(wx, wy) {
    if (!state.snap || !state.grid.on) return [wx, wy];
    var size = state.grid.size;
    if (state.grid.type === 'hex') {
      var R = HEX_R();
      var q = (Math.sqrt(3) / 3 * wx - 1 / 3 * wy) / R;
      var r = (2 / 3 * wy) / R;
      // cube round
      var cx = q, cz = r, cy = -cx - cz;
      var rx = Math.round(cx), ry = Math.round(cy), rz = Math.round(cz);
      var dx = Math.abs(rx - cx), dy = Math.abs(ry - cy), dz = Math.abs(rz - cz);
      if (dx > dy && dx > dz) rx = -ry - rz; else if (dy > dz) ry = -rx - rz; else rz = -rx - ry;
      return [Math.sqrt(3) * R * (rx + rz / 2), 1.5 * R * rz];
    }
    var col = Math.round((wx - state.grid.ox) / size - 0.5);
    var row = Math.round((wy - state.grid.oy) / size - 0.5);
    return [(col + 0.5) * size + state.grid.ox, (row + 0.5) * size + state.grid.oy];
  }

  function tokenExtent(t) {
    var size = state.grid.size;
    if (state.grid.type === 'hex') {
      var d = HEX_R() * 1.7;
      return [t.x - d / 2, t.y - d / 2, d, d];
    }
    return [t.x - (t.w || 1) * size / 2, t.y - (t.h || 1) * size / 2, (t.w || 1) * size, (t.h || 1) * size];
  }

  function tokenArt(t) {
    if (t.character_id) return '/api/character/' + t.character_id + '/portrait-image?size=256';
    var kind = t.kind === 'npc' ? 'npc' : 'creature';
    if (t.kind === 'marker' || t.kind === 'pin' || t.kind === 'prop') return '';
    return '/api/ref-image/' + kind + '/' + encodeURIComponent(t.ref_name || '') + '?size=256';
  }

  function image(url) {
    if (!url) return null;
    if (!IMG_CACHE[url]) {
      var img = new Image();
      img.onload = function () { redraw(); };
      img.src = url;
      IMG_CACHE[url] = img;
    }
    return IMG_CACHE[url];
  }

  // ── drawing ─────────────────────────────────────────────────────────────────────────
  function resizeCanvas() {
    if (!canvas || !host) return;
    var dpr = window.devicePixelRatio || 1;
    var w = host.clientWidth, h = host.clientHeight;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.max(1, Math.floor(h * dpr));
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    redraw();
  }

  function drawGrid() {
    if (!state.grid.on) return;
    var size = state.grid.size, cam = state.camera;
    var W = canvas.clientWidth, H = canvas.clientHeight;
    ctx.save();
    ctx.strokeStyle = 'rgba(0,0,0,0.35)';
    ctx.lineWidth = 1;
    if (state.grid.type === 'hex') {
      var R = HEX_R();
      var cols = Math.ceil(W / (Math.sqrt(3) * R * cam.zoom)) + 2;
      var rows = Math.ceil(H / (1.5 * R * cam.zoom)) + 2;
      var originCol = Math.floor((-cam.x) / (Math.sqrt(3) * R)) - 1;
      var originRow = Math.floor((-cam.y) / (1.5 * R)) - 1;
      for (var r = originRow; r < originRow + rows; r++) {
        for (var c = originCol; c < originCol + cols; c++) {
          var cx = Math.sqrt(3) * R * (c + (r % 2 ? 0.5 : 0));
          var cy = 1.5 * R * r;
          var p = worldToScreen(cx, cy);
          hexPath(p[0], p[1], R * cam.zoom);
          ctx.stroke();
        }
      }
    } else {
      var step = size * cam.zoom;
      if (step < 4) { ctx.restore(); return; }
      var x0 = ((state.grid.ox + cam.x) * cam.zoom) % step;
      var y0 = ((state.grid.oy + cam.y) * cam.zoom) % step;
      ctx.beginPath();
      for (var x = x0; x < W; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
      for (var y = y0; y < H; y += step) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
      ctx.stroke();
    }
    ctx.restore();
  }

  function hexPath(cx, cy, r) {
    ctx.beginPath();
    for (var i = 0; i < 6; i++) {
      var a = Math.PI / 180 * (60 * i - 30);
      var px = cx + r * Math.cos(a), py = cy + r * Math.sin(a);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
  }

  function drawToken(t) {
    var box = tokenExtent(t);
    var p = worldToScreen(box[0], box[1]);
    var w = box[2] * state.camera.zoom, h = box[3] * state.camera.zoom;
    if (w < 3 || h < 3) return;
    if (p[0] + w < -40 || p[1] + h < -40 || p[0] > canvas.clientWidth + 40 || p[1] > canvas.clientHeight + 40) return;

    var unrevealed = state.fogOn && !isCellRevealed(cellKeyFor(t.x, t.y));
    // The DM's screen dims what is unseen (it still has to move it); the party's screen does not
    // get it at all. The server already withholds them, this is the second line of defence.
    if (state.readOnly && unrevealed) return;
    var isHex = state.grid.type === 'hex';
    ctx.save();
    if (unrevealed) ctx.globalAlpha = 0.35;
    ctx.beginPath();
    if (isHex) ctx.arc(p[0] + w / 2, p[1] + h / 2, Math.min(w, h) / 2, 0, Math.PI * 2);
    else roundRect(p[0], p[1], w, h, Math.min(6, w * 0.12));
    ctx.closePath();
    ctx.fillStyle = t.hidden ? 'rgba(90,90,100,0.55)' : 'rgba(30,30,36,0.85)';
    ctx.fill();

    var art = image(tokenArt(t));
    if (art && art.complete && art.naturalWidth) {
      ctx.save();
      ctx.clip();
      ctx.globalAlpha = t.hidden ? 0.5 : 1;
      var ar = art.naturalWidth / art.naturalHeight;
      var dw = w, dh = h;
      if (ar > 1) { dh = h; dw = h * ar; } else { dw = w; dh = w / ar; }
      ctx.drawImage(art, p[0] + (w - dw) / 2, p[1] + (h - dh) / 2, dw, dh);
      ctx.restore();
    } else {
      ctx.fillStyle = 'rgba(220,220,230,0.85)';
      ctx.font = Math.max(9, Math.min(w, h) * 0.42) + 'px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText((t.label || t.ref_name || '?').slice(0, 2).toUpperCase(), p[0] + w / 2, p[1] + h / 2);
    }

    // HP bar
    if (t.hp_max > 0) {
      var pct = Math.max(0, Math.min(1, (t.hp_current || 0) / t.hp_max));
      var barH = Math.max(3, h * 0.09);
      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.fillRect(p[0], p[1] + h - barH, w, barH);
      ctx.fillStyle = pct > 0.5 ? '#4ade80' : pct > 0.25 ? '#facc15' : '#f87171';
      ctx.fillRect(p[0], p[1] + h - barH, w * pct, barH);
    }

    ctx.lineWidth = Math.max(1.5, w * 0.045);
    ctx.strokeStyle = t.id === state.selected ? '#c9a227' : (t.kind === 'character' ? '#60a5fa' : (t.hidden ? '#94a3b8' : '#e5e7eb'));
    if (isHex) { ctx.beginPath(); ctx.arc(p[0] + w / 2, p[1] + h / 2, Math.min(w, h) / 2, 0, Math.PI * 2); }
    else { ctx.beginPath(); roundRect(p[0], p[1], w, h, Math.min(6, w * 0.12)); }
    ctx.stroke();

    if (w > 34 && (t.label || t.ref_name)) {
      var text = (t.label || t.ref_name);
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      var tw = ctx.measureText(text).width + 8;
      var ty = p[1] + h + 2;
      ctx.fillStyle = 'rgba(0,0,0,0.65)';
      ctx.fillRect(p[0] + w / 2 - tw / 2, ty, tw, 14);
      ctx.fillStyle = '#f4f4f5';
      ctx.fillText(text, p[0] + w / 2, ty + 1);
    }
    ctx.restore();
  }

  function roundRect(x, y, w, h, r) {
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y);
  }

  // ── grid cells (fog) ────────────────────────────────────────────────────────────────
  // A cell key is "col,row" on a square grid and "q,r" (axial) on a hex one. The server
  // validates the shape and does not care which grid it is, so switching grid type keeps the
  // revealed area instead of throwing it away.
  function cellKeyFor(wx, wy) {
    var g = state.grid;
    if (g.type === 'hex') {
      var R = g.size / 2;
      var q = (Math.sqrt(3) / 3 * wx - 1 / 3 * wy) / R;
      var r = (2 / 3 * wy) / R;
      var cx = q, cz = r, cy = -cx - cz;
      var rx = Math.round(cx), ry = Math.round(cy), rz = Math.round(cz);
      var dx = Math.abs(rx - cx), dy = Math.abs(ry - cy), dz = Math.abs(rz - cz);
      if (dx > dy && dx > dz) rx = -ry - rz; else if (dy > dz) ry = -rx - rz; else rz = -rx - ry;
      return rx + ',' + rz;
    }
    return Math.floor((wx - g.ox) / g.size) + ',' + Math.floor((wy - g.oy) / g.size);
  }

  function cellCentreWorld(key) {
    var parts = String(key).split(',');
    var a = parseFloat(parts[0]), b = parseFloat(parts[1]);
    if (isNaN(a) || isNaN(b)) return null;
    var g = state.grid;
    if (g.type === 'hex') {
      var R = g.size / 2;
      return [Math.sqrt(3) * R * (a + b / 2), 1.5 * R * b];
    }
    return [a * g.size + g.size / 2 + g.ox, b * g.size + g.size / 2 + g.oy];
  }

  function hexOnPath(path, cx, cy, r) {
    for (var i = 0; i < 6; i++) {
      var a = Math.PI / 180 * (60 * i - 30);
      var px = cx + r * Math.cos(a), py = cy + r * Math.sin(a);
      if (i === 0) path.moveTo(px, py); else path.lineTo(px, py);
    }
    path.closePath();
  }

  function addCellToPath(path, key) {
    var c = cellCentreWorld(key);
    if (!c) return;
    var p = worldToScreen(c[0], c[1]);
    var size = state.grid.size * state.camera.zoom;
    if (state.grid.type === 'hex') {
      hexOnPath(path, p[0], p[1], size / 2);
    } else {
      path.rect(p[0] - size / 2, p[1] - size / 2, size, size);
    }
  }

  function isCellRevealed(key) { return !!state.fog[key]; }

  function drawFog() {
    if (!state.fogOn) return;
    var W = canvas.clientWidth, H = canvas.clientHeight;
    // One even-odd path: the viewport rectangle with the revealed cells punched out. Filling
    // the fog and THEN erasing with destination-out would erase the map itself.
    var path = new Path2D();
    path.rect(0, 0, W, H);
    var keys = Object.keys(state.fog);
    for (var i = 0; i < keys.length; i++) addCellToPath(path, keys[i]);
    ctx.save();
    // the DM's screen keeps the map faintly readable under the fog; the party's screen is solid
    ctx.fillStyle = state.readOnly ? 'rgba(5,5,8,0.99)' : 'rgba(6,6,10,0.88)';
    ctx.fill(path, 'evenodd');
    ctx.restore();
  }

  function drawStrokes() {
    if (!state.strokes.length) return;
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    state.strokes.forEach(function (s) {
      var pts = s.points || [];
      if (pts.length < 2) return;
      ctx.beginPath();
      for (var i = 0; i < pts.length; i++) {
        var p = worldToScreen(pts[i][0], pts[i][1]);
        if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
      }
      ctx.strokeStyle = s.color || '#e5e7eb';
      ctx.lineWidth = Math.max(1, (s.width || 4) * state.camera.zoom);
      ctx.stroke();
    });
    ctx.restore();
  }

  // ── brush shapes + the measure tool ─────────────────────────────────────────────────
  // Both are computed from cell CENTRES. The rect brush means "cells the marquee overlaps"
  // (a square grid can answer that exactly; on a hex grid it is "cells whose centre is within
  // one cell radius of the marquee", the honest analogue on a honeycomb), and the circle means
  // "cells whose centre is inside the circle" on either grid — the way templates are judged.
  function axialToWorld(q, r) {
    var R = state.grid.size / 2;
    return [Math.sqrt(3) * R * (q + r / 2), 1.5 * R * r];
  }

  function cellsInRect(x0, y0, x1, y1) {
    var g = state.grid;
    var loX = Math.min(x0, x1), hiX = Math.max(x0, x1);
    var loY = Math.min(y0, y1), hiY = Math.max(y0, y1);
    var out = [], seen = {};
    if (g.type === 'hex') {
      var R = g.size / 2;
      var pad = R;
      var q0 = Math.floor((Math.sqrt(3) / 3 * (loX - pad) - (hiY + pad) / 3) / R) - 2;
      var q1 = Math.ceil((Math.sqrt(3) / 3 * (hiX + pad) - (loY - pad) / 3) / R) + 2;
      var r0 = Math.floor((2 / 3 * (loY - pad)) / R) - 2;
      var r1 = Math.ceil((2 / 3 * (hiY + pad)) / R) + 2;
      for (var r = r0; r <= r1; r++) {
        for (var q = q0; q <= q1; q++) {
          var c = axialToWorld(q, r);
          if (c[0] >= loX - pad && c[0] <= hiX + pad && c[1] >= loY - pad && c[1] <= hiY + pad) {
            var key = q + ',' + r;
            if (!seen[key]) { seen[key] = 1; out.push(key); }
          }
        }
      }
      return out;
    }
    var size = g.size;
    var col0 = Math.floor((loX - g.ox) / size), col1 = Math.floor((hiX - g.ox) / size);
    var row0 = Math.floor((loY - g.oy) / size), row1 = Math.floor((hiY - g.oy) / size);
    for (var col = col0; col <= col1; col++) {
      for (var row = row0; row <= row1; row++) out.push(col + ',' + row);
    }
    return out;
  }

  function cellsInCircle(cx, cy, radius) {
    var g = state.grid;
    var out = [], seen = {};
    var rr = radius * radius;
    if (g.type === 'hex') {
      var R = g.size / 2;
      var q0 = Math.floor((Math.sqrt(3) / 3 * (cx - radius) - (cy + radius) / 3) / R) - 2;
      var q1 = Math.ceil((Math.sqrt(3) / 3 * (cx + radius) - (cy - radius) / 3) / R) + 2;
      var r0 = Math.floor((2 / 3 * (cy - radius)) / R) - 2;
      var r1 = Math.ceil((2 / 3 * (cy + radius)) / R) + 2;
      for (var r = r0; r <= r1; r++) {
        for (var q = q0; q <= q1; q++) {
          var c = axialToWorld(q, r);
          var dx = c[0] - cx, dy = c[1] - cy;
          if (dx * dx + dy * dy <= rr) {
            var key = q + ',' + r;
            if (!seen[key]) { seen[key] = 1; out.push(key); }
          }
        }
      }
      return out;
    }
    var size = g.size;
    var col0 = Math.floor((cx - radius - g.ox) / size), col1 = Math.floor((cx + radius - g.ox) / size);
    var row0 = Math.floor((cy - radius - g.oy) / size), row1 = Math.floor((cy + radius - g.oy) / size);
    for (var col = col0; col <= col1; col++) {
      for (var row = row0; row <= row1; row++) {
        var px = col * size + size / 2 + g.ox, py = row * size + size / 2 + g.oy;
        var ddx = px - cx, ddy = py - cy;
        if (ddx * ddx + ddy * ddy <= rr) out.push(col + ',' + row);
      }
    }
    return out;
  }

  function measureCells(ax, ay, bx, by) {
    var a = cellKeyFor(ax, ay).split(','), b = cellKeyFor(bx, by).split(',');
    var aq = parseInt(a[0], 10), ar = parseInt(a[1], 10);
    var bq = parseInt(b[0], 10), br = parseInt(b[1], 10);
    var cells;
    if (state.grid.type === 'hex') {
      var dq = aq - bq, dr = ar - br;
      cells = (Math.abs(dq) + Math.abs(dq + dr) + Math.abs(dr)) / 2;   // axial hex distance
    } else {
      // 5e's simplified table rule: a diagonal counts as one cell
      cells = Math.max(Math.abs(aq - bq), Math.abs(ar - br));
    }
    return { cells: cells, feet: cells * state.feetPerCell };
  }

  function drawMarquee() {
    var m = state.marquee;
    if (!m || state.readOnly) return;
    var p0 = worldToScreen(m.x0, m.y0), p1 = worldToScreen(m.x1, m.y1);
    ctx.save();
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = m.erase ? '#f87171' : '#4ade80';
    ctx.fillStyle = m.erase ? 'rgba(248,113,113,0.18)' : 'rgba(74,222,128,0.18)';
    ctx.beginPath();
    if (m.shape === 'circle') {
      var r = Math.hypot(m.x1 - m.x0, m.y1 - m.y0) * state.camera.zoom;
      ctx.arc(p0[0], p0[1], r, 0, Math.PI * 2);
    } else {
      ctx.rect(p0[0], p0[1], p1[0] - p0[0], p1[1] - p0[1]);
    }
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  function drawRuler() {
    var r = state.ruler;
    if (!r || state.readOnly) return;
    var p0 = worldToScreen(r.ax, r.ay), p1 = worldToScreen(r.bx, r.by);
    var dist = measureCells(r.ax, r.ay, r.bx, r.by);
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#c9a227';
    ctx.beginPath();
    ctx.moveTo(p0[0], p0[1]);
    ctx.lineTo(p1[0], p1[1]);
    ctx.stroke();
    ctx.fillStyle = '#c9a227';
    [p0, p1].forEach(function (p) {
      ctx.beginPath();
      ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
      ctx.fill();
    });
    var label = (dist.cells === 1 ? '1 cell' : dist.cells + ' cells') + ' · ' + dist.feet + ' ft';
    ctx.font = '13px system-ui,sans-serif';
    var tw = ctx.measureText(label).width + 12;
    var mx = (p0[0] + p1[0]) / 2 - tw / 2, my = (p0[1] + p1[1]) / 2 - 22;
    ctx.fillStyle = 'rgba(12,12,16,0.9)';
    ctx.fillRect(mx, my, tw, 20);
    ctx.strokeStyle = '#c9a227';
    ctx.lineWidth = 1;
    ctx.strokeRect(mx, my, tw, 20);
    ctx.fillStyle = '#f4f4f5';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, mx + 6, my + 11);
    ctx.restore();
  }

  function redraw() {
    if (!ctx) return;
    if (state.raf) return;
    // requestAnimationFrame never fires in a HIDDEN tab, so a second screen sitting in the
    // background would silently never paint (and a poll would keep "updating" an empty canvas).
    // Draw synchronously there; keep the coalescing for the visible case.
    if (document.hidden) { drawFrame(); return; }
    state.raf = window.requestAnimationFrame(function () {
      state.raf = null;
      drawFrame();
    });
  }

  function drawFrame() {
    {
      var W = canvas.clientWidth, H = canvas.clientHeight;
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = '#0f0f12';
      ctx.fillRect(0, 0, W, H);

      if (state.map && state.map.image_path) {
        var bg = image(state.map.image_path);
        if (bg && bg.complete && bg.naturalWidth) {
          var p = worldToScreen(0, 0);
          ctx.drawImage(bg, p[0], p[1], bg.naturalWidth * state.camera.zoom, bg.naturalHeight * state.camera.zoom);
        }
      } else {
        // no background yet: draw a placeholder field so the grid is visible
        var ph = worldToScreen(0, 0);
        ctx.fillStyle = 'rgba(255,255,255,0.03)';
        ctx.fillRect(ph[0], ph[1], 20 * state.grid.size * state.camera.zoom, 14 * state.grid.size * state.camera.zoom);
        ctx.fillStyle = 'rgba(255,255,255,0.35)';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No map image yet — use 🖼 Image to upload one', W / 2, H / 2);
      }

      drawGrid();
      drawFog();
      // The DM's marks and the tokens stay ON TOP of the fog: an annotation has to be readable
      // (that is the point of drawing it), and the DM is the one looking at this screen and
      // still has to move whatever is waiting in the dark. Dimming, not hiding, is what keeps
      // unrevealed tokens manageable — the player view will simply not receive them.
      drawStrokes();
      state.tokens.slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0) || a.id - b.id; })
        .forEach(drawToken);
      // DM-only overlays: the brush preview and the ruler are never sent to a second screen
      drawMarquee();
      drawRuler();
    }
  }

  // ── saving ──────────────────────────────────────────────────────────────────────────
  function pokePlayers() {
    // Tell second screens "something changed" — they then fetch the PROJECTED state from the
    // server, so the rule about what the party may see lives in exactly one place.
    if (state.readOnly) return;
    try {
      if (!state.pokeChannel && typeof BroadcastChannel === 'function') {
        state.pokeChannel = new BroadcastChannel('vtt-map-' + window.MAP_ID);
      }
      if (state.pokeChannel) state.pokeChannel.postMessage({ type: 'changed', at: Date.now() });
    } catch (e) { /* no BroadcastChannel (or a private window): the poll still covers it */ }
  }

  function markDirty(msg) {
    state.dirty = true;
    pokePlayers();
    var badge = $('vttSaved');
    if (badge) { badge.textContent = msg || 'unsaved…'; badge.className = 'vtt-dirty'; }
    if (state.saveTimer) clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(saveNow, SAVE_DELAY);
  }

  function saveNow() {
    if (!state.dirty) return Promise.resolve();
    state.dirty = false;
    var payload = { tokens: state.tokens };
    var badge = $('vttSaved');
    if (badge) { badge.textContent = 'saving…'; }
    return fetch('/api/dm/map/' + window.MAP_ID + '/tokens', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), keepalive: true
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        state.tokens = d.tokens;                       // adopt server ids for new tokens
        if (badge) { badge.textContent = 'saved'; badge.className = ''; }
        renderScenes && renderSelected();
      } else if (badge) { badge.textContent = 'save failed'; }
      redraw();
    }).catch(function () { if (badge) badge.textContent = 'save failed'; });
  }

  function saveCamera() {
    pokePlayers();
    try { localStorage.setItem('vttCam:' + window.MAP_ID, JSON.stringify(state.camera)); } catch (e) {}
    fetch('/api/dm/map/' + window.MAP_ID + '/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ camera: state.camera }), keepalive: true
    }).catch(function () {});
  }

  // ── input ───────────────────────────────────────────────────────────────────────────
  function hitTest(sx, sy) {
    var list = state.tokens.slice().sort(function (a, b) { return (b.z || 0) - (a.z || 0) || b.id - a.id; });
    for (var i = 0; i < list.length; i++) {
      var box = tokenExtent(list[i]);
      var p = worldToScreen(box[0], box[1]);
      var w = box[2] * state.camera.zoom, h = box[3] * state.camera.zoom;
      if (sx >= p[0] && sx <= p[0] + w && sy >= p[1] && sy <= p[1] + h) return list[i];
    }
    return null;
  }

  function bind() {
    canvas.addEventListener('mousedown', function (ev) {
      var rect = canvas.getBoundingClientRect();
      var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
      // Right-drag always pans, whatever tool is active: the fog brush and the pen need the
      // left button and a mouse may have no middle one.
      if (ev.button === 2) {
        state.panning = { sx: sx, sy: sy, cx: state.camera.x, cy: state.camera.y };
        redraw(); ev.preventDefault(); return;
      }
      if (state.tool === 'fog') {
        var fw = screenToWorld(sx, sy);
        if (state.fogBrush === 'cell') {
          state.painting = true;
          paintCell(fw[0], fw[1], ev.shiftKey);
        } else {
          // rect/circle: anchor here, show the preview while dragging, apply on release
          state.marquee = { shape: state.fogBrush, x0: fw[0], y0: fw[1], x1: fw[0], y1: fw[1],
                            erase: !!ev.shiftKey };
        }
        redraw(); ev.preventDefault(); return;
      }
      if (state.tool === 'measure') {
        var mw = screenToWorld(sx, sy);
        state.ruler = { ax: mw[0], ay: mw[1], bx: mw[0], by: mw[1] };
        redraw(); ev.preventDefault(); return;
      }
      if (state.tool === 'draw') {
        var dw = screenToWorld(sx, sy);
        state.stroke = { color: state.pen.color, width: state.pen.width,
                         points: [[Math.round(dw[0]), Math.round(dw[1])]] };
        ev.preventDefault(); return;
      }
      var t = hitTest(sx, sy);
      if (t && !ev.shiftKey) {
        state.selected = t.id;
        var w = screenToWorld(sx, sy);
        state.drag = { id: t.id, dx: w[0] - t.x, dy: w[1] - t.y };
      } else {
        state.selected = null;
        state.panning = { sx: sx, sy: sy, cx: state.camera.x, cy: state.camera.y };
      }
      renderSelected();
      redraw();
      ev.preventDefault();
    });

    window.addEventListener('mousemove', function (ev) {
      if (!state.drag && !state.panning && !state.painting && !state.stroke &&
          !state.marquee && !state.ruler) return;
      var rect = canvas.getBoundingClientRect();
      var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
      if (state.marquee) {
        var qw = screenToWorld(sx, sy);
        state.marquee.x1 = qw[0];
        state.marquee.y1 = qw[1];
        redraw();
        return;
      }
      if (state.ruler) {
        var rw = screenToWorld(sx, sy);
        state.ruler.bx = rw[0];
        state.ruler.by = rw[1];
        redraw();
        return;
      }
      if (state.painting) {
        var pw = screenToWorld(sx, sy);
        paintCell(pw[0], pw[1], ev.shiftKey);
        redraw();
        return;
      }
      if (state.stroke) {
        var sw = screenToWorld(sx, sy);
        state.stroke.points.push([Math.round(sw[0]), Math.round(sw[1])]);
        redraw();
        return;
      }
      if (state.panning) {
        state.camera.x = state.panning.cx + (sx - state.panning.sx) / state.camera.zoom;
        state.camera.y = state.panning.cy + (sy - state.panning.sy) / state.camera.zoom;
      } else if (state.drag) {
        var t = state.tokens.filter(function (x) { return x.id === state.drag.id; })[0];
        if (t) {
          var w = screenToWorld(sx, sy);
          var pt = snapPoint(w[0] - state.drag.dx + 0.0, w[1] - state.drag.dy + 0.0);
          t.x = Math.round(pt[0] * 10) / 10;
          t.y = Math.round(pt[1] * 10) / 10;
        }
      }
      redraw();
    });

    window.addEventListener('mouseup', function () {
      if (state.marquee) {
        applyMarquee();
        state.marquee = null;
        markLayerDirty();
        redraw();
      } else if (state.ruler) {
        // the ruler stays on screen until the DM clears it or picks another tool
        redraw();
      } else if (state.stroke) {
        if (state.stroke.points.length > 1) state.strokes.push(state.stroke);
        state.stroke = null;
        markLayerDirty();
      } else if (state.painting) {
        state.painting = null;
        markLayerDirty();
      } else if (state.drag) markDirty();
      else if (state.panning) saveCamera();
      state.drag = null;
      state.panning = null;
    });

    canvas.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var rect = canvas.getBoundingClientRect();
      var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
      var before = screenToWorld(sx, sy);
      var factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      state.camera.zoom = Math.max(0.08, Math.min(6, state.camera.zoom * factor));
      var after = screenToWorld(sx, sy);
      state.camera.x += after[0] - before[0];
      state.camera.y += after[1] - before[1];
      redraw();
      saveCamera();
    }, { passive: false });

    canvas.addEventListener('contextmenu', function (ev) {
      ev.preventDefault();
      var rect = canvas.getBoundingClientRect();
      var t = hitTest(ev.clientX - rect.left, ev.clientY - rect.top);
      if (t) { state.selected = t.id; renderSelected(); redraw(); }
    });

    canvas.addEventListener('dblclick', function (ev) {
      var rect = canvas.getBoundingClientRect();
      var t = hitTest(ev.clientX - rect.left, ev.clientY - rect.top);
      if (t) {
        var w = screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
        var pt = snapPoint(w[0], w[1]);
        t.x = pt[0]; t.y = pt[1];
        markDirty();
        redraw();
      }
    });

    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('keydown', function (ev) {
      if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) return;
      if (ev.key === 'Delete' && state.selected) { removeToken(state.selected); }
      else if (ev.key === 'v' || ev.key === 'V') setTool('select');
      else if (ev.key === 'f' || ev.key === 'F') setTool('fog');
      else if (ev.key === 'd' || ev.key === 'D') setTool('draw');
      else if (ev.key === 'm' || ev.key === 'M') setTool('measure');
      else if (ev.key === 'g' || ev.key === 'G') toggleGrid();
      else if (ev.key === 's' || ev.key === 'S') toggleSnap();
      else if (ev.key === '0') fit();
      else if (ev.key === 'Escape') {
        state.selected = null;
        state.ruler = null;
        renderSelected();
        redraw();
      }
    });
    window.addEventListener('pagehide', function () { saveNow(); saveLayer(); });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { saveNow(); saveLayer(); }
    });
  }

  // ── public actions ──────────────────────────────────────────────────────────────────
  function toggleGrid() {
    state.grid.on = !state.grid.on;
    var b = $('vttGridBtn'); if (b) b.style.opacity = state.grid.on ? '1' : '0.55';
    redraw();
  }
  function setGridType(type) {
    state.grid.type = type === 'hex' ? 'hex' : 'square';
    if (state.map) { state.map.grid_type = state.grid.type; }
    persistGrid();
    redraw();
  }
  function nudgeSize(delta) {
    state.grid.size = Math.max(16, Math.min(240, state.grid.size + delta));
    if (state.map) state.map.grid_size = state.grid.size;
    var label = $('vttGridSize'); if (label) label.textContent = state.grid.size + 'px';
    persistGrid();
    redraw();
  }
  function persistGrid() {
    fetch('/api/dm/map/' + window.MAP_ID + '/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grid_type: state.grid.type, grid_size: state.grid.size,
                             grid_offset_x: state.grid.ox, grid_offset_y: state.grid.oy })
    }).catch(function () {});
  }
  function toggleSnap() {
    state.snap = !state.snap;
    var b = $('vttSnapBtn'); if (b) b.textContent = '🧲 Snap: ' + (state.snap ? 'on' : 'off');
  }
  function zoomBy(factor) {
    state.camera.zoom = Math.max(0.08, Math.min(6, state.camera.zoom * factor));
    redraw(); saveCamera();
  }
  function fit() {
    var iw = (state.bg && state.bg.naturalWidth) || 0;
    var ih = (state.bg && state.bg.naturalHeight) || 0;
    if (!iw) {
      var bg = state.map && state.map.image_path ? image(state.map.image_path) : null;
      if (bg && bg.naturalWidth) { iw = bg.naturalWidth; ih = bg.naturalHeight; }
    }
    if (!iw) { iw = canvas.clientWidth; ih = canvas.clientHeight; }
    var zoom = Math.min(canvas.clientWidth / iw, canvas.clientHeight / ih) * 0.96;
    state.camera.zoom = Math.max(0.08, Math.min(6, zoom));
    state.camera.x = (canvas.clientWidth / state.camera.zoom - iw) / 2;
    state.camera.y = (canvas.clientHeight / state.camera.zoom - ih) / 2;
    redraw(); saveCamera();
  }

  function addToken(spec) {
    var centre = screenToWorld(canvas.clientWidth / 2, canvas.clientHeight / 2);
    var pt = snapPoint(centre[0], centre[1]);
    var token = Object.assign({
      kind: 'creature', ref_name: '', label: '', x: pt[0], y: pt[1], w: 1, h: 1,
      hp_current: 0, hp_max: 0, hidden: 0, z: 0
    }, spec || {});
    return fetch('/api/dm/map/' + window.MAP_ID + '/token/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(token)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        state.tokens.push(d.token);
        state.selected = d.token.id;
        pokePlayers();
        renderSelected();
        redraw();
      }
      return d;
    });
  }

  function removeToken(id) {
    state.tokens = state.tokens.filter(function (t) { return t.id !== id; });
    if (state.selected === id) state.selected = null;
    fetch('/api/dm/map/token/' + id + '/delete', { method: 'POST' }).catch(function () {});
    renderSelected(); redraw();
    markDirty();
  }

  function updateToken(id, patch) {
    var t = state.tokens.filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    Object.assign(t, patch);
    pokePlayers();
    fetch('/api/dm/map/token/' + id + '/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch)
    }).catch(function () {});
    renderSelected(); redraw();
  }

  // ── side panels ─────────────────────────────────────────────────────────────────────
  function renderSelected() {
    var host = $('vttSelected');
    if (!host) return;
    var t = state.tokens.filter(function (x) { return x.id === state.selected; })[0];
    if (!t) { host.innerHTML = '<p style="font-size:.75rem;color:var(--text-muted)">Click a token to edit it.</p>'; return; }
    host.innerHTML =
      '<h4 style="margin:.2rem 0">' + (t.label || t.ref_name || 'Token') + '</h4>' +
      '<div style="font-size:.7rem;color:var(--text-muted);margin-bottom:.3rem">' + t.kind + '</div>' +
      '<div class="vtt-row"><span>HP</span>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.bumpHp(' + t.id + ',-5)">−5</button>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.bumpHp(' + t.id + ',-1)">−1</button>' +
        '<strong>' + (t.hp_current || 0) + '/' + (t.hp_max || 0) + '</strong>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.bumpHp(' + t.id + ',1)">+1</button>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.bumpHp(' + t.id + ',5)">+5</button></div>' +
      '<div class="vtt-row"><span>Size</span>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.resize(' + t.id + ',-1)">−</button>' +
        '<span>' + (t.w || 1) + '×' + (t.h || 1) + '</span>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.resize(' + t.id + ',1)">+</button>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.toggleHidden(' + t.id + ')">' + (t.hidden ? '👁 Show' : '🙈 Hide') + '</button></div>' +
      '<div class="vtt-row"><input id="vttLabel" value="' + (t.label || '').replace(/"/g, '&quot;') + '" placeholder="label" ' +
        'onchange="VTT.updateToken(' + t.id + ',{label:this.value})" style="flex:1;min-width:0"></div>' +
      '<div class="vtt-row"><button class="btn btn-danger btn-sm" onclick="VTT.removeToken(' + t.id + ')">Remove token</button></div>';
  }

  function bumpHp(id, delta) {
    var t = state.tokens.filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    updateToken(id, { hp_current: Math.max(-999, Math.min(9999, (t.hp_current || 0) + delta)) });
  }
  function resizeToken(id, delta) {
    var t = state.tokens.filter(function (x) { return x.id === id; })[0];
    if (!t) return;
    var n = Math.max(1, Math.min(12, (t.w || 1) + delta));
    updateToken(id, { w: n, h: n });
  }
  function toggleHidden(id) {
    var t = state.tokens.filter(function (x) { return x.id === id; })[0];
    if (t) updateToken(id, { hidden: t.hidden ? 0 : 1 });
  }

  function renderScenes() {
    var host = $('vttScenes');
    if (!host) return;
    var scenes = state.map && state.map.scenes ? state.map.scenes : [];
    var html = '<h4 style="margin:.2rem 0">💾 Setups</h4>' +
      '<div class="vtt-row"><input id="vttSceneName" placeholder="name this setup" style="flex:1;min-width:0">' +
      '<button class="btn btn-outline btn-sm" onclick="VTT.snapshot()">Save</button></div>';
    html += scenes.length ? scenes.map(function (s) {
      return '<div class="vtt-row"><span style="flex:1;min-width:0;font-size:.8rem">' + s.name + '</span>' +
        '<button class="btn btn-outline btn-sm" onclick="VTT.restoreScene(' + s.id + ')">Restore</button>' +
        '<button class="btn btn-danger btn-sm" onclick="VTT.deleteScene(' + s.id + ')">✕</button></div>';
    }).join('') : '<p style="font-size:.75rem;color:var(--text-muted)">No saved setups yet.</p>';
    host.innerHTML = html;
  }

  function snapshot() {
    var input = $('vttSceneName');
    var name = (input && input.value || '').trim();
    if (!name) { if (input) input.focus(); return; }
    fetch('/api/dm/map/' + window.MAP_ID + '/snapshot', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        state.map.scenes = [{ id: d.id, name: d.name }].concat(state.map.scenes || []);
        if (input) input.value = '';
        renderScenes();
      }
    });
  }

  function restoreScene(sceneId) {
    fetch('/api/dm/map/scene/' + sceneId + '/restore', { method: 'POST' })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) {
          state.tokens = d.tokens;
          if (d.camera) { try { state.camera = JSON.parse(d.camera); } catch (e) {} }
          state.fog = {};
          (d.fog || []).forEach(function (k) { state.fog[String(k)] = 1; });
          state.fogOn = !!d.fog_on;
          state.strokes = d.draw || [];
          var fb = $('vttFogOn');
          if (fb) fb.textContent = '☁ Fog: ' + (state.fogOn ? 'on' : 'off');
          state.selected = null;
          renderSelected(); redraw();
        }
      });
  }

  function deleteScene(sceneId) {
    fetch('/api/dm/map/scene/' + sceneId + '/delete', { method: 'POST' }).then(function () {
      state.map.scenes = (state.map.scenes || []).filter(function (s) { return s.id !== sceneId; });
      renderScenes();
    });
  }

  function searchPalette(q) {
    var host = $('vttPalette');
    if (!host) return;
    if (!q || q.length < 2) { host.innerHTML = ''; return; }
    host.innerHTML = '<p style="font-size:.75rem;color:var(--text-muted)">searching…</p>';
    Promise.all([
      fetch('/api/dm/npcs').then(function (r) { return r.json(); }).catch(function () { return {}; }),
      fetch('/api/dm/characters-for-combat').then(function (r) { return r.json(); }).catch(function () { return {}; })
    ]).then(function (res) {
      var needle = q.toLowerCase();
      var npcs = (res[0].npcs || []).filter(function (n) { return (n.name || '').toLowerCase().indexOf(needle) >= 0; }).slice(0, 6);
      var chars = (res[1].characters || []).filter(function (c) { return (c.name || '').toLowerCase().indexOf(needle) >= 0; }).slice(0, 6);
      var html = '';
      npcs.forEach(function (n) {
        html += '<div class="vtt-row"><span style="flex:1;min-width:0;font-size:.8rem">👤 ' + n.name + '</span>' +
          '<button class="btn btn-primary btn-sm" onclick="VTT.addToken({kind:\'npc\',ref_name:' + JSON.stringify(n.name) + ',label:' + JSON.stringify(n.name) + '})">Place</button></div>';
      });
      chars.forEach(function (c) {
        html += '<div class="vtt-row"><span style="flex:1;min-width:0;font-size:.8rem">🧝 ' + c.name + ' L' + (c.level || 1) + '</span>' +
          '<button class="btn btn-primary btn-sm" onclick="VTT.addToken({kind:\'character\',character_id:' + c.id + ',label:' + JSON.stringify(c.name) + '})">Place</button></div>';
      });
      host.innerHTML = html || '<p style="font-size:.75rem;color:var(--text-muted)">nothing matched</p>';
    });
  }

  // ── fog + drawing ───────────────────────────────────────────────────────────────────
  function applyMarquee() {
    var m = state.marquee;
    if (!m) return 0;
    var keys = m.shape === 'circle'
      ? cellsInCircle(m.x0, m.y0, Math.hypot(m.x1 - m.x0, m.y1 - m.y0))
      : cellsInRect(m.x0, m.y0, m.x1, m.y1);
    keys.forEach(function (k) { if (m.erase) delete state.fog[k]; else state.fog[k] = 1; });
    return keys.length;
  }

  function setFogBrush(shape) {
    state.fogBrush = (shape === 'rect' || shape === 'circle') ? shape : 'cell';
    ['Cell', 'Rect', 'Circle'].forEach(function (n) {
      var b = $('vttBrush' + n);
      if (b) b.style.opacity = (n.toLowerCase() === state.fogBrush) ? '1' : '0.6';
    });
    if (state.tool !== 'fog') setTool('fog');
    var hint = $('vttHint');
    if (hint) {
      hint.textContent = state.fogBrush === 'cell'
        ? 'Click or drag cells to reveal · shift-drag hides · right-drag pans'
        : 'Drag a ' + state.fogBrush + ' to reveal · shift-drag hides · right-drag pans';
    }
  }

  function setFeetPerCell(feet) {
    var v = parseInt(feet, 10);
    if (!v || v < 1 || v > 100) return;
    state.feetPerCell = v;
    var input = $('vttFeet');
    if (input) input.value = v;
    fetch('/api/dm/map/' + window.MAP_ID + '/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feet_per_cell: v })
    }).catch(function () {});
    redraw();
  }

  function clearMeasure() {
    state.ruler = null;
    redraw();
  }

  function paintCell(wx, wy, erase) {
    var key = cellKeyFor(wx, wy);
    if (erase) delete state.fog[key]; else state.fog[key] = 1;
    markLayerDirty();
  }

  function markLayerDirty() {
    state.layerDirty = true;
    pokePlayers();
    var badge = $('vttSaved');
    if (badge) { badge.textContent = 'unsaved…'; badge.className = 'vtt-dirty'; }
    if (state.layerTimer) clearTimeout(state.layerTimer);
    state.layerTimer = setTimeout(saveLayer, SAVE_DELAY);
  }

  function layerPayload() {
    return { fog: Object.keys(state.fog), fog_on: state.fogOn ? 1 : 0, draw: state.strokes };
  }

  function saveLayer() {
    if (!state.layerDirty) return Promise.resolve();
    state.layerDirty = false;
    var badge = $('vttSaved');
    if (badge) badge.textContent = 'saving…';
    return fetch('/api/dm/map/' + window.MAP_ID + '/layer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(layerPayload()), keepalive: true
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok && badge) { badge.textContent = 'saved'; badge.className = ''; }
      else if (badge) { badge.textContent = 'save failed'; }
    }).catch(function () { if (badge) badge.textContent = 'save failed'; });
  }

  function setTool(tool) {
    state.tool = (tool === 'fog' || tool === 'draw' || tool === 'measure') ? tool : 'select';
    if (state.tool !== 'measure') state.ruler = null;      // the ruler is a transient overlay
    ['Select', 'Fog', 'Draw', 'Measure'].forEach(function (n) {
      var b = $('vttTool' + n);
      if (b) b.style.opacity = (n.toLowerCase() === state.tool) ? '1' : '0.6';
    });
    var hint = $('vttHint');
    if (hint) {
      hint.textContent = state.tool === 'fog'
        ? (state.fogBrush === 'cell'
          ? 'Click or drag cells to reveal · shift-drag hides · right-drag pans'
          : 'Drag a ' + state.fogBrush + ' to reveal · shift-drag hides · right-drag pans')
        : (state.tool === 'draw'
          ? 'Draw with the left button · right-drag pans · 🧽 Clear erases everything'
          : (state.tool === 'measure'
            ? 'Drag to measure · distances use the ft/cell setting · Esc clears'
            : 'Drag to pan · wheel to zoom · drag a token to move it'));
    }
  }

  function toggleFog() {
    state.fogOn = !state.fogOn;
    var b = $('vttFogOn');
    if (b) b.textContent = '☁ Fog: ' + (state.fogOn ? 'on' : 'off');
    markLayerDirty();
    redraw();
  }

  function revealAll() {
    var iw = 0, ih = 0;
    var bg = state.map && state.map.image_path ? image(state.map.image_path) : null;
    if (bg && bg.naturalWidth) { iw = bg.naturalWidth; ih = bg.naturalHeight; }
    if (!iw) { iw = state.grid.size * 40; ih = state.grid.size * 30; }
    var g = state.grid;
    if (g.type === 'hex') {
      var R = g.size / 2;
      for (var q = -10; q < iw / (Math.sqrt(3) * R) + 10; q++) {
        for (var r = -10; r < ih / (1.5 * R) + 10; r++) state.fog[q + ',' + r] = 1;
      }
    } else {
      var cols = Math.ceil(iw / g.size), rows = Math.ceil(ih / g.size);
      for (var c = 0; c < cols; c++) {
        for (var row = 0; row < rows; row++) state.fog[c + ',' + row] = 1;
      }
    }
    markLayerDirty();
    redraw();
  }

  function hideAll() {
    state.fog = {};
    markLayerDirty();
    redraw();
  }

  function setPen(color, width) {
    if (color) state.pen.color = String(color).slice(0, 24);
    if (width !== null && width !== undefined && width !== '') {
      state.pen.width = Math.max(1, Math.min(40, parseInt(width, 10) || 4));
    }
  }

  function clearDraw() {
    state.strokes = [];
    state.stroke = null;
    markLayerDirty();
    redraw();
  }

  function spawnEncounter() {
    var pick = $('vttEncounterPick');
    var encId = pick && parseInt(pick.value, 10);
    if (!encId) { alert('Pick an encounter to place.'); return Promise.resolve(); }
    var badge = $('vttSaved');
    if (badge) badge.textContent = 'spawning…';
    return fetch('/api/dm/map/' + window.MAP_ID + '/spawn-encounter', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ encounter_id: encId, replace: false })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        state.tokens = d.tokens;
        pokePlayers();
        if (badge) badge.textContent = 'placed ' + d.added +
          (d.skipped ? ', ' + d.skipped + ' already there' : '') +
          (d.party_linked ? ', ' + d.party_linked + ' party' : '');
        redraw();
      } else if (badge) { badge.textContent = (d && d.error) || 'spawn failed'; }
      return d;
    }).catch(function () { if (badge) badge.textContent = 'spawn failed'; });
  }

  function loadEncounters() {
    var pick = $('vttEncounterPick');
    if (!pick) return;
    fetch('/api/dm/encounters').then(function (r) { return r.json(); }).then(function (d) {
      var list = (d && (d.encounters || d.list)) || (Array.isArray(d) ? d : []);
      pick.innerHTML = '<option value="">Encounter…</option>' + list.map(function (e) {
        return '<option value="' + e.id + '">' + (e.name || 'Encounter') + '</option>';
      }).join('');
    }).catch(function () { pick.innerHTML = '<option value="">Encounter…</option>'; });
  }

  function openPlayer() {
    var badge = $('vttSaved');
    return fetch('/api/dm/map/' + window.MAP_ID + '/player-key')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.url) { if (badge) badge.textContent = 'player view unavailable'; return; }
        window.open(d.url, 'vtt-player');
        var hint = $('vttPlayerHint');
        if (hint) hint.textContent = location.origin + d.url;
        if (badge) { badge.textContent = 'player link ready'; badge.className = ''; }
      });
  }

  function revokePlayer() {
    return fetch('/api/dm/map/' + window.MAP_ID + '/player-key', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: '' })
    }).then(function (r) { return r.json(); }).then(function (d) {
      var badge = $('vttSaved');
      if (badge) badge.textContent = (d && d.ok) ? 'player link revoked' : 'revoke failed';
      var hint = $('vttPlayerHint');
      if (hint) hint.textContent = '';
    });
  }

  function chooseImage() { var f = $('vttImageInput'); if (f) f.click(); }

  function uploadImage(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var badge = $('vttSaved'); if (badge) badge.textContent = 'uploading…';
      fetch('/api/dm/map/' + window.MAP_ID + '/image', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: reader.result })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) {
          state.map.image_path = d.image_path;
          delete IMG_CACHE[d.image_path];
          if (badge) badge.textContent = 'image set';
          fit();
        } else if (badge) { badge.textContent = (d && d.error) || 'upload failed'; }
      }).catch(function () { if (badge) badge.textContent = 'upload failed'; });
    };
    reader.readAsDataURL(file);
    input.value = '';
  }

  // ── init ────────────────────────────────────────────────────────────────────────────
  function init() {
    // The player page loads this same file, so the DM path must stand down there: otherwise it
    // would fetch the state without the key (403), clobber the projection with an empty map, and
    // attach the DM's keyboard tools to a screen the party is looking at.
    if (window.PLAYER_CFG) return;
    canvas = $('vttCanvas');
    host = $('vttHost');
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    bind();
    resizeCanvas();
    // A flex parent can measure 0 on the very first pass, so size again on the next frame.
    window.requestAnimationFrame(resizeCanvas);

    var saved = null;
    try { saved = localStorage.getItem('vttCam:' + window.MAP_ID); } catch (e) {}
    fetch('/api/dm/map/' + window.MAP_ID).then(function (r) { return r.json(); }).then(function (d) {
      state.map = d.map || {};
      state.tokens = d.tokens || [];
      state.map.scenes = d.scenes || [];
      state.grid.type = state.map.grid_type || 'square';
      state.grid.size = state.map.grid_size || 50;
      state.grid.ox = state.map.grid_offset_x || 0;
      state.grid.oy = state.map.grid_offset_y || 0;
      state.feetPerCell = parseInt(state.map.feet_per_cell, 10) || 5;
      var feetInput = $('vttFeet');
      if (feetInput) feetInput.value = state.feetPerCell;
      if (saved) { try { state.camera = JSON.parse(saved); } catch (e) {} }
      else if (state.map.camera) { try { state.camera = JSON.parse(state.map.camera); } catch (e) {} }
      else { setTimeout(fit, 60); }
      // overlay layers: revealed cells and the DM's drawing
      state.fog = {};
      (state.map.fog || []).forEach(function (k) { state.fog[String(k)] = 1; });
      state.fogOn = !!(parseInt(state.map.fog_on, 10) || 0);
      state.strokes = state.map.draw || [];
      var fogBtn = $('vttFogOn');
      if (fogBtn) fogBtn.textContent = '☁ Fog: ' + (state.fogOn ? 'on' : 'off');
      setTool('select');
      loadEncounters();
      var label = $('vttGridSize'); if (label) label.textContent = state.grid.size + 'px';
      renderSelected();
      renderScenes();
      resizeCanvas();
      redraw();
      setTimeout(function () { resizeCanvas(); fit(); }, 300);  // wait for the background to decode
    });
  }

  // ── the player view (second screen) ─────────────────────────────────────────────────
  // Same renderer, read-only: it gets the PROJECTED state from the server (never the DM's
  // drawing, never a hidden token, never anything under the fog) and only lets the viewer pan
  // around while the camera is not being followed.
  var player = { following: true, cfg: null, channel: null, timer: null, failures: 0, last: 0 };

  function playerStatus(text) {
    var s = $('status');
    if (s) s.textContent = text;
  }

  function playerApply(payload) {
    if (!payload || !payload.map) return;
    state.map = payload.map;
    state.map.draw = [];
    state.tokens = payload.tokens || [];
    state.fog = {};
    (payload.fog || []).forEach(function (k) { state.fog[String(k)] = 1; });
    state.fogOn = !!(parseInt(payload.fog_on, 10) || 0);
    state.strokes = [];                 // the DM's notes are not the party's business
    state.grid.type = state.map.grid_type || 'square';
    state.grid.size = state.map.grid_size || 50;
    state.grid.ox = state.map.grid_offset_x || 0;
    state.grid.oy = state.map.grid_offset_y || 0;
    if (player.following && payload.camera) {
      try { state.camera = JSON.parse(payload.camera); } catch (e) {}
    }
    redraw();
  }

  function playerFetch(first) {
    var cfg = player.cfg || {};
    var url = '/api/dm/map/' + cfg.id + '/state' + (cfg.key ? '?k=' + encodeURIComponent(cfg.key) : '');
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (d) {
      player.failures = 0;
      player.last = Date.now();
      var wasEmpty = !state.tokens.length;
      playerApply(d);
      playerStatus('live · ' + new Date().toLocaleTimeString());
      if (first || wasEmpty) playerFit();
      return d;
    }).catch(function (e) {
      player.failures++;
      // Say what is actually happening: a revoked key or a stopped server must not look "live".
      playerStatus(player.failures > 2 ? 'disconnected (' + e.message + ')' : 'reconnecting…');
    });
  }

  function playerFit() { fit(); }

  function toggleFollow() {
    player.following = !player.following;
    var b = $('followBtn');
    if (b) {
      b.textContent = player.following ? '🔒 Following' : '🖐 Free look';
      b.className = player.following ? '' : 'nofollow';
    }
    if (player.following) playerFetch(false);   // snap back to the DM's view immediately
  }

  function initPlayer(cfg) {
    player.cfg = cfg || window.PLAYER_CFG || {};
    canvas = $('vttCanvas');
    host = $('host');
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    state.readOnly = true;
    resizeCanvas();
    window.requestAnimationFrame(resizeCanvas);
    window.addEventListener('resize', resizeCanvas);

    // Free look only while the DM's camera is NOT followed.
    canvas.addEventListener('mousedown', function (ev) {
      if (player.following) return;
      var rect = canvas.getBoundingClientRect();
      state.panning = { sx: ev.clientX - rect.left, sy: ev.clientY - rect.top,
                        cx: state.camera.x, cy: state.camera.y };
    });
    window.addEventListener('mousemove', function (ev) {
      if (!state.panning || player.following) return;
      var rect = canvas.getBoundingClientRect();
      state.camera.x = state.panning.cx + ((ev.clientX - rect.left) - state.panning.sx) / state.camera.zoom;
      state.camera.y = state.panning.cy + ((ev.clientY - rect.top) - state.panning.sy) / state.camera.zoom;
      redraw();
    });
    window.addEventListener('mouseup', function () { state.panning = null; });
    canvas.addEventListener('wheel', function (ev) {
      if (player.following) return;
      ev.preventDefault();
      state.camera.zoom = Math.max(0.08, Math.min(6, state.camera.zoom * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
      redraw();
    }, { passive: false });
    canvas.addEventListener('contextmenu', function (ev) { ev.preventDefault(); });

    // A second window on this machine reacts instantly; the poll covers a TV or tablet, which
    // shares no browser context with the DM's window.
    try {
      if (typeof BroadcastChannel === 'function') {
        player.channel = new BroadcastChannel('vtt-map-' + player.cfg.id);
        player.channel.onmessage = function () { playerFetch(false); };
      }
    } catch (e) { /* the poll is the fallback */ }
    playerFetch(true);
    player.timer = setInterval(function () { playerFetch(false); }, 3000);
  }

  // the player page's controls hang off the player object (its own toggle lives there, not on
  // the DM surface) — a guard test pins this wiring, because a dead button throws only on click
  player.toggleFollow = toggleFollow;
  player.refetch = function () { return playerFetch(false); };
  player.fit = playerFit;

  window.VTT = {
    init: init, redraw: redraw, zoomBy: zoomBy, fit: fit, toggleGrid: toggleGrid,
    setGridType: setGridType, nudgeSize: nudgeSize, toggleSnap: toggleSnap,
    addToken: addToken, removeToken: removeToken, updateToken: updateToken,
    bumpHp: bumpHp, resize: resizeToken, resizeCanvas: resizeCanvas, toggleHidden: toggleHidden,
    snapshot: snapshot, restoreScene: restoreScene, deleteScene: deleteScene,
    searchPalette: searchPalette, chooseImage: chooseImage, uploadImage: uploadImage,
    saveNow: saveNow, saveLayer: saveLayer, screenToWorld: screenToWorld,
    worldToScreen: worldToScreen, snapPoint: snapPoint,
    playerInit: initPlayer, playerApply: playerApply, playerFit: playerFit, player: player,
    pokePlayers: pokePlayers,
    setTool: setTool, toggleFog: toggleFog, revealAll: revealAll, hideAll: hideAll,
    setPen: setPen, clearDraw: clearDraw, paintCell: paintCell, cellKeyFor: cellKeyFor,
    setFogBrush: setFogBrush, setFeetPerCell: setFeetPerCell, clearMeasure: clearMeasure,
    applyMarquee: applyMarquee, cellsInRect: cellsInRect, cellsInCircle: cellsInCircle,
    measureCells: measureCells,
    spawnEncounter: spawnEncounter, loadEncounters: loadEncounters,
    openPlayer: openPlayer, revokePlayer: revokePlayer, state: state
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
