/* Map chrome for /dm-map/<id>: the readout strip and the phone control sheet.
   Nothing here touches the canvas or its sizing beyond asking VTT to resize after
   the side panels appear or go (the same call togglePanels makes). */
(function () {
  'use strict';

  var host = document.getElementById('vttHost');
  var side = document.getElementById('vttSide');
  var btn = document.getElementById('vttPanelBtn');
  var cur = document.getElementById('vttCursorRead');
  var zoom = document.getElementById('vttZoomRead');
  var key = document.getElementById('vttGridKey');
  var narrow = window.matchMedia('(max-width: 900px)');

  function resize() { if (window.VTT && VTT.resizeCanvas) VTT.resizeCanvas(); }

  function setOpen(open) {
    if (!side) return;
    side.hidden = !open;
    if (btn) {
      btn.setAttribute('aria-expanded', String(open));
      btn.classList.toggle('btn-primary', open);
    }    resize();
  }

  function readout() {
    if (!window.VTT) return;
    var st = VTT.state || {};
    if (zoom && st.camera) zoom.textContent = Math.round(st.camera.zoom * 100) + '%';
    if (key && st.grid) key.textContent = st.grid.size + 'px';
  }

  function point(ev) {
    if (!cur || !window.VTT || !VTT.screenToWorld) return;
    var rect = host.getBoundingClientRect();
    var w = VTT.screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
    var st = VTT.state || {};
    var g = st.grid || { size: 50, ox: 0, oy: 0 };
    var size = Math.max(1, g.size || 1);
    var col = Math.floor((w[0] - (g.ox || 0)) / size);
    var row = Math.floor((w[1] - (g.oy || 0)) / size);
    if (col < 0 || row < 0) { cur.textContent = '—'; return; }
    cur.textContent = col + ',' + row;
  }

  if (host) host.addEventListener('mousemove', point);
  if (host) host.addEventListener('mouseleave', function () { if (cur) cur.textContent = '—'; });
  var close = document.getElementById('vttSheetClose');
  if (close) close.addEventListener('click', function () { setOpen(false); });

  // A phone starts with the map full width; a desktop starts with its panels out.
  setOpen(!narrow.matches);
  narrow.addEventListener('change', function (e) { setOpen(!e.matches); });

  setInterval(readout, 400);
  readout();
  window.MapChrome = { resize: resize, setOpen: setOpen, refresh: readout };
})();
