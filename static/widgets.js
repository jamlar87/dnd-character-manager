/* Table widgets — counters and timers beside the combat tracker.
 *
 * Atlas VTT's widget bar, small version: a named counter you tick up and down ("fear"), and a
 * timer you start, pause and reset. The DM never leaves the fight to update them, and the state
 * is stored on the encounter (see routes/dm.py `/api/dm/encounter/{id}/widgets`).
 *
 * Contract: WidgetBar.load(scope, id) renders into #widgetBar; every mutation saves debounced.
 * Timers tick locally and only their text nodes are rewritten, so a running clock never
 * destroys the buttons or the input the DM is typing in.
 */
(function () {
  'use strict';

  var ICONS = ['⚔️', '🔥', '⏱', '💀', '✨', '🩸', '🛡', '🎲', '🌟', '🌙'];
  var state = { scope: null, id: null, widgets: [], timer: null, saveTimer: null, dirty: false };

  function css() {
    if (document.getElementById('wb-style')) return;
    var st = document.createElement('style');
    st.id = 'wb-style';
    st.textContent =
      '.widget-bar{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;padding:.4rem .5rem;' +
      'background:var(--card-bg);border:1px solid var(--border);border-radius:6px;margin-bottom:.6rem;min-height:2.2rem}' +
      '.widget-bar .wb-title{font-size:.75rem;color:var(--text-muted);margin-right:.2rem}' +
      '.wb-chip{display:inline-flex;align-items:center;gap:.3rem;padding:.2rem .45rem;background:var(--bg);' +
      'border:1px solid var(--border);border-radius:14px;font-size:.8rem}' +
      '.wb-chip .wb-val{font-weight:700;min-width:1.2rem;text-align:center}' +
      '.wb-chip button{background:none;border:none;color:var(--text);cursor:pointer;padding:0 .15rem;font-size:.85rem}' +
      '.wb-chip button:hover{color:var(--accent)}' +
      '.wb-chip.wb-running{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}' +
      '.wb-add{display:inline-flex;align-items:center;gap:.25rem}' +
      '.wb-add input{width:7rem;padding:.15rem .35rem;background:var(--bg);border:1px solid var(--border);' +
      'border-radius:4px;color:var(--text);font-size:.75rem}' +
      '.wb-add select{background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:.75rem}';
    document.head.appendChild(st);
  }

  function bar() { return document.getElementById('widgetBar'); }

  function mmss(total) {
    var s = Math.max(0, Math.floor(total));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  function save() {
    if (!state.scope || !state.id) return;
    state.dirty = true;
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(flush, 500);
  }

  function flush() {
    if (!state.dirty || !state.scope || !state.id) return;
    state.dirty = false;
    fetch('/api/dm/' + state.scope + '/' + state.id + '/widgets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ widgets: state.widgets }),
      // A reload during a fight must not lose the last half-minute of a running clock:
      // keepalive lets the request outlive the page it was sent from.
      keepalive: true
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok && Array.isArray(d.widgets)) {
        // the server clamps and cleans: adopt its version, but keep local run state
        var running = {};
        state.widgets.forEach(function (w) { running[w.id] = !!w.running; });
        state.widgets = d.widgets.map(function (w) { return Object.assign(w, { running: !!running[w.id] }); });
      }
    }).catch(function () {});
  }

  function render() {
    var host = bar();
    if (!host) return;
    css();
    host.textContent = '';
    var title = document.createElement('span');
    title.className = 'wb-title';
    title.textContent = '🧰 Table';
    host.appendChild(title);

    state.widgets.forEach(function (w, i) {
      var chip = document.createElement('span');
      chip.className = 'wb-chip' + (w.type === 'timer' && w.running ? ' wb-running' : '');
      chip.setAttribute('data-widget', w.id || String(i));

      var icon = document.createElement('span');
      icon.textContent = w.icon || '⭐';
      chip.appendChild(icon);

      var name = document.createElement('span');
      name.textContent = w.name;
      chip.appendChild(name);

      var val = document.createElement('span');
      val.className = 'wb-val';
      val.textContent = w.type === 'timer' ? mmss(w.seconds) : String(w.value);
      chip.appendChild(val);

      if (w.type === 'timer') {
        var start = document.createElement('button');
        start.textContent = w.running ? '⏸' : '▶';
        start.title = w.running ? 'Pause' : 'Start';
        start.onclick = function () { w.running = !w.running; render(); save(); };
        chip.appendChild(start);
        var reset = document.createElement('button');
        reset.textContent = '↺';
        reset.title = 'Reset';
        reset.onclick = function () { w.running = false; w.seconds = 0; render(); save(); };
        chip.appendChild(reset);
      } else {
        var minus = document.createElement('button');
        minus.textContent = '−';
        minus.title = 'Decrease';
        minus.onclick = function () { w.value = Math.max(-999, w.value - 1); render(); save(); };
        chip.appendChild(minus);
        var plus = document.createElement('button');
        plus.textContent = '+';
        plus.title = 'Increase';
        plus.onclick = function () { w.value = Math.min(999, w.value + 1); render(); save(); };
        chip.appendChild(plus);
      }

      var del = document.createElement('button');
      del.textContent = '✕';
      del.title = 'Remove';
      del.onclick = function () {
        state.widgets.splice(i, 1);
        render();
        save();
      };
      chip.appendChild(del);

      host.appendChild(chip);
    });

    if (state.scope === 'encounter') {
      var add = document.createElement('span');
      add.className = 'wb-add';
      var input = document.createElement('input');
      input.id = 'wbNewName';
      input.placeholder = 'widget name';
      var kind = document.createElement('select');
      kind.id = 'wbNewType';
      ['counter', 'timer'].forEach(function (t) {
        var o = document.createElement('option');
        o.value = t;
        o.textContent = t;
        kind.appendChild(o);
      });
      var iconSel = document.createElement('select');
      iconSel.id = 'wbNewIcon';
      ICONS.forEach(function (ic) {
        var o = document.createElement('option');
        o.value = ic;
        o.textContent = ic;
        iconSel.appendChild(o);
      });
      var addBtn = document.createElement('button');
      addBtn.className = 'btn btn-outline btn-sm';
      addBtn.textContent = '+ Add';
      addBtn.onclick = function () {
        var name = (input.value || '').trim();
        if (!name) { input.focus(); return; }
        state.widgets.push({
          id: 'w' + Date.now().toString(36) + Math.floor(Math.random() * 1000),
          name: name.slice(0, 24),
          icon: iconSel.value,
          type: kind.value,
          value: 0,
          seconds: 0,
          running: false
        });
        input.value = '';
        render();
        save();
      };
      input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') addBtn.click(); });
      add.appendChild(input);
      add.appendChild(iconSel);
      add.appendChild(kind);
      add.appendChild(addBtn);
      host.appendChild(add);
    }
  }

  function tick() {
    var changed = false;
    state.widgets.forEach(function (w) {
      if (w.type === 'timer' && w.running) { w.seconds = (w.seconds || 0) + 1; changed = true; }
    });
    if (!changed) return;
    // rewrite only the clock text: a full render would blow away the add form and focus
    var host = bar();
    if (!host) return;
    state.widgets.forEach(function (w, i) {
      var chip = host.querySelector('[data-widget="' + (w.id || i) + '"]');
      var val = chip && chip.querySelector('.wb-val');
      if (val && w.type === 'timer') val.textContent = mmss(w.seconds);
    });
    // Persist every few seconds while a clock runs, so a reload is never far behind it.
    if (state.widgets.some(function (w) { return w.type === 'timer' && w.running && w.seconds % 5 === 0; })) save();
  }

  function load(scope, id) {
    state.scope = scope;
    state.id = id;
    var host = bar();
    if (host) {
      css();
      host.textContent = 'Loading widgets…';
      host.className = 'widget-bar';
    }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    return fetch('/api/dm/' + scope + '/' + id + '/widgets')
      .then(function (r) { return r.ok ? r.json() : { widgets: [] }; })
      .then(function (d) {
        state.widgets = (d.widgets || []).map(function (w) { return Object.assign({ running: false }, w); });
        render();
        state.timer = setInterval(tick, 1000);
        return state.widgets;
      })
      .catch(function () { render(); });
  }

  function destroy() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.scope = null;
    state.id = null;
    state.widgets = [];
  }

  window.WidgetBar = { load: load, destroy: destroy, flush: flush, state: state };
  // A pending save must not be lost when the tab goes away mid-fight (keepalive in flush()
  // is what makes these two events reliable).
  window.addEventListener('visibilitychange', function () { if (document.hidden) flush(); });
  window.addEventListener('pagehide', flush);
})();
