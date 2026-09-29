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

  function hexCentres() {
    // pointy-top hexes, odd-r offset; returns nothing — kept for symmetry with squareGrid
    return null;
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

    var isHex = state.grid.type === 'hex';
    ctx.save();
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

  function redraw() {
    if (!ctx) return;
    if (state.raf) return;
    state.raf = window.requestAnimationFrame(function () {
      state.raf = null;
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
      state.tokens.slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0) || a.id - b.id; })
        .forEach(drawToken);
    });
  }

  // ── saving ──────────────────────────────────────────────────────────────────────────
  function markDirty(msg) {
    state.dirty = true;
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
      if (!state.drag && !state.panning) return;
      var rect = canvas.getBoundingClientRect();
      var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
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
      if (state.drag) markDirty();
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
      else if (ev.key === 'g' || ev.key === 'G') toggleGrid();
      else if (ev.key === 's' || ev.key === 'S') toggleSnap();
      else if (ev.key === '0') fit();
      else if (ev.key === 'Escape') { state.selected = null; renderSelected(); redraw(); }
    });
    window.addEventListener('pagehide', function () { saveNow(); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) saveNow(); });
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
      if (d && d.ok) { state.tokens.push(d.token); state.selected = d.token.id; renderSelected(); redraw(); }
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
      if (saved) { try { state.camera = JSON.parse(saved); } catch (e) {} }
      else if (state.map.camera) { try { state.camera = JSON.parse(state.map.camera); } catch (e) {} }
      else { setTimeout(fit, 60); }
      var label = $('vttGridSize'); if (label) label.textContent = state.grid.size + 'px';
      renderSelected();
      renderScenes();
      resizeCanvas();
      redraw();
      setTimeout(function () { resizeCanvas(); fit(); }, 300);  // wait for the background to decode
    });
  }

  window.VTT = {
    init: init, redraw: redraw, zoomBy: zoomBy, fit: fit, toggleGrid: toggleGrid,
    setGridType: setGridType, nudgeSize: nudgeSize, toggleSnap: toggleSnap,
    addToken: addToken, removeToken: removeToken, updateToken: updateToken,
    bumpHp: bumpHp, resize: resizeToken, resizeCanvas: resizeCanvas, toggleHidden: toggleHidden,
    snapshot: snapshot, restoreScene: restoreScene, deleteScene: deleteScene,
    searchPalette: searchPalette, chooseImage: chooseImage, uploadImage: uploadImage,
    saveNow: saveNow, screenToWorld: screenToWorld, worldToScreen: worldToScreen,
    snapPoint: snapPoint, state: state
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
