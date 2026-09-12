/* global window, document, localStorage, fetch */
/*
 * SourceFilter — "filter by specific manual" for every search bar.
 *
 * A compact multi-select: 📚 button opens a panel with a search box and one
 * checkbox per ingested manual (plus "All manuals"). The selection is a list of
 * book slugs (["FGFD", "TTP"]), persisted per surface in localStorage, and is
 * applied either client-side (matching a row's data-source string) or
 * server-side (endpoints accept ?source=SLUG[,SLUG]).
 *
 * Public API (window.SourceFilter):
 *   init(container, {key, onChange, label})  -> mounts the widget, returns it
 *   slugs(key)                               -> current selection ([])
 *   matches(sourceString, slugs)             -> bool (mirrors the Python matcher)
 *   slugForSource(sourceString)              -> "FGFD" | "MM" | "" ...
 *   applyToList(selector, slugs, attr)       -> visible count (hides the rest)
 */
(function () {
  'use strict';

  var MAP = null;          // {slug: {title, display, path}}
  var REVERSE = null;      // {displayLower: slug}
  var SELS = {};           // widget key -> [slug]
  var WIDGETS = {};        // widget key -> widget state
  var STYLE_ID = 'sf-styles';
  var LS_PREFIX = 'srcFilter:';
  var _autoTimer = null;

  // ── source-string → slug (mirrors routes/characters/helpers.slug_for_source) ──
  function load() {
    if (MAP) return Promise.resolve(MAP);
    return fetch('/api/reference/source-map')
      .then(function (r) { return r.json(); })
      .then(function (m) {
        MAP = m || {};
        REVERSE = {};
        Object.keys(MAP).forEach(function (slug) {
          var disp = String((MAP[slug] && MAP[slug].display) || '').trim().toLowerCase();
          if (disp && !(disp in REVERSE)) REVERSE[disp] = slug;
        });
        return MAP;
      })
      .catch(function () { MAP = {}; REVERSE = {}; return MAP; });
  }

  function slugForSource(src) {
    var s = String(src || '').trim();
    if (!s) return '';
    if (s.toUpperCase().indexOf('SRD') === 0) return 'SRD';
    var inner = (s.charAt(0) === '(' && s.charAt(s.length - 1) === ')') ? s.slice(1, -1).trim() : s;
    inner = inner.replace(/,?\s*p\.?\s*\d+(\s*[-\u2013]\s*\d+)?\s*$/i, '').trim();
    if (REVERSE) {
      var hit = REVERSE[inner.toLowerCase()];
      if (hit) return hit;
    }
    var m = inner.match(/^([A-Za-z][A-Za-z0-9'&]{1,7})\b/);
    if (m && MAP) {
      var code = m[1].toUpperCase();
      if (MAP[code]) return code;
      var keys = Object.keys(MAP);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].toUpperCase() === code) return keys[i];
      }
    }
    return '';
  }

  function matches(src, slugs) {
    if (!slugs || !slugs.length) return true;
    var s = slugForSource(src);
    return !!s && slugs.indexOf(s) !== -1;
  }

  // ── styles (injected once) ────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      '.sf-wrap{position:relative;display:inline-block;flex:0 0 auto}',
      '.sf-btn{display:inline-flex;align-items:center;gap:.35rem;padding:.5rem .7rem;background:var(--card-bg,#1c1c22);',
      'border:1px solid var(--border,#333);border-radius:6px;color:var(--text,#eee);font-size:.85rem;cursor:pointer;white-space:nowrap}',
      '.sf-btn:hover{border-color:var(--accent,#c8963e)}',
      '.sf-btn.sf-active{border-color:var(--accent,#c8963e);color:var(--accent,#c8963e)}',
      '.sf-panel{position:absolute;z-index:1200;top:calc(100% + .35rem);left:0;width:min(20rem,90vw);max-height:22rem;overflow:auto;',
      'background:var(--card-bg,#1c1c22);border:1px solid var(--border,#333);border-radius:8px;padding:.5rem;box-shadow:0 8px 24px rgba(0,0,0,.45)}',
      '.sf-panel input.sf-search{width:100%;box-sizing:border-box;padding:.4rem .5rem;margin-bottom:.4rem;background:var(--bg,#121216);',
      'border:1px solid var(--border,#333);border-radius:5px;color:var(--text,#eee);font-size:.85rem}',
      '.sf-row{display:flex;align-items:center;gap:.5rem;padding:.3rem .35rem;border-radius:5px;cursor:pointer;font-size:.85rem}',
      '.sf-row:hover{background:var(--bg,#121216)}',
      '.sf-row input{accent-color:var(--accent,#c8963e);cursor:pointer}',
      '.sf-row .sf-disp{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.sf-row .sf-code{font-size:.7rem;color:var(--text-muted,#999);opacity:.8}',
      '.sf-sep{border-top:1px solid var(--border,#333);margin:.35rem 0}',
      '.sf-empty{padding:.5rem;color:var(--text-muted,#999);font-size:.8rem}',
    ].join('');
    var el = document.createElement('style');
    el.id = STYLE_ID;
    el.textContent = css;
    document.head.appendChild(el);
  }

  // ── persistence ───────────────────────────────────────────────────────────
  function loadSel(key) {
    if (SELS[key]) return SELS[key];
    var raw = null;
    try { raw = localStorage.getItem(LS_PREFIX + key); } catch (e) { raw = null; }
    var arr = [];
    if (raw) { try { arr = JSON.parse(raw) || []; } catch (e) { arr = []; } }
    if (!Array.isArray(arr)) arr = [];
    SELS[key] = arr;
    return arr;
  }

  function saveSel(key, arr) {
    SELS[key] = arr;
    try { localStorage.setItem(LS_PREFIX + key, JSON.stringify(arr)); } catch (e) { /* private mode */ }
  }

  function label(w) {
    var sel = SELS[w.key] || [];
    if (w.compact) return sel.length ? '📚 ' + sel.length : '📚';
    if (!sel.length) return '📚 All manuals';
    if (sel.length === 1) return '📚 ' + sel[0];
    return '📚 ' + sel[0] + ' +' + (sel.length - 1);
  }

  function refresh(w) {
    var sel = SELS[w.key] || [];
    w.btn.textContent = label(w) + (w.compact ? '' : ' ▾');
    w.btn.title = sel.length
      ? 'Filtering by: ' + sel.join(', ') + ' — click to change'
      : 'Filter results by manual (pick as many as you like)';
    w.btn.classList.toggle('sf-active', sel.length > 0);
    w.panel.querySelectorAll('input.sf-book').forEach(function (cb) {
      cb.checked = sel.indexOf(cb.value) !== -1;
    });
    var all = w.panel.querySelector('input.sf-all');
    if (all) all.checked = sel.length === 0;
  }

  function setSelection(w, arr, fire) {
    saveSel(w.key, arr);
    refresh(w);
    if (fire && typeof w.onChange === 'function') w.onChange(arr.slice());
  }

  function buildPanel(w) {
    var panel = document.createElement('div');
    panel.className = 'sf-panel';
    panel.style.display = 'none';
    // Clicks inside the panel must not reach the document handler (which closes it).
    panel.addEventListener('click', function (ev) { ev.stopPropagation(); });

    var search = document.createElement('input');
    search.className = 'sf-search';
    search.placeholder = 'Filter manuals…';
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      panel.querySelectorAll('.sf-row').forEach(function (row) {
        var txt = row.getAttribute('data-search') || '';
        row.style.display = (!q || txt.indexOf(q) !== -1) ? '' : 'none';
        if (row.classList.contains('sf-all')) row.style.display = '';
      });
    });
    panel.appendChild(search);

    var allRow = document.createElement('label');
    allRow.className = 'sf-row sf-all';
    allRow.setAttribute('data-search', 'all manuals');
    var allCb = document.createElement('input');
    allCb.type = 'checkbox';
    allCb.className = 'sf-all';
    allCb.addEventListener('change', function () {
      setSelection(w, [], true);
    });
    var allTxt = document.createElement('span');
    allTxt.className = 'sf-disp';
    allTxt.textContent = 'All manuals';
    allRow.appendChild(allCb);
    allRow.appendChild(allTxt);
    panel.appendChild(allRow);

    var sep = document.createElement('div');
    sep.className = 'sf-sep';
    panel.appendChild(sep);

    var list = document.createElement('div');
    list.className = 'sf-list';
    panel.appendChild(list);
    w.listEl = list;

    return panel;
  }

  function fillList(w) {
    var sel = SELS[w.key] || [];
    var slugs = Object.keys(MAP || {});
    var books = slugs.map(function (slug) {
      var disp = String((MAP[slug] && (MAP[slug].display || MAP[slug].title)) || slug);
      return { slug: slug, disp: disp };
    }).sort(function (a, b) { return a.disp.toLowerCase() < b.disp.toLowerCase() ? -1 : 1; });

    // Books that actually appear in this surface come first? Keep it simple:
    // alphabetical, but the ones with cached text/searchable content first is
    // not knowable here — alphabetical is predictable.
    w.listEl.innerHTML = '';
    if (!books.length) {
      var e = document.createElement('div');
      e.className = 'sf-empty';
      e.textContent = 'No manuals available';
      w.listEl.appendChild(e);
      return;
    }
    books.forEach(function (b) {
      var row = document.createElement('label');
      row.className = 'sf-row';
      row.setAttribute('data-search', (b.disp + ' ' + b.slug).toLowerCase());
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'sf-book';
      cb.value = b.slug;
      cb.checked = sel.indexOf(b.slug) !== -1;
      cb.addEventListener('change', function () {
        var cur = (SELS[w.key] || []).slice();
        if (cb.checked) { if (cur.indexOf(b.slug) === -1) cur.push(b.slug); }
        else { cur = cur.filter(function (s) { return s !== b.slug; }); }
        setSelection(w, cur, true);
      });
      var disp = document.createElement('span');
      disp.className = 'sf-disp';
      disp.textContent = b.disp;
      var code = document.createElement('span');
      code.className = 'sf-code';
      code.textContent = b.slug;
      row.appendChild(cb);
      row.appendChild(disp);
      row.appendChild(code);
      w.listEl.appendChild(row);
    });
  }

  function init(container, opts) {
    opts = opts || {};
    injectStyles();
    var host = (typeof container === 'string') ? document.querySelector(container) : container;
    if (!host) return null;
    var key = opts.key || (host.id || 'default');

    var w = WIDGETS[key];
    if (w) return w;  // already mounted for this surface

    loadSel(key);
    var wrap = document.createElement('span');
    wrap.className = 'sf-wrap';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sf-btn';
    btn.title = 'Filter results by manual (pick as many as you like)';

    // ONE state object: buildPanel() stores the option list on it (w.listEl),
    // and fillList()/refresh() read from it — a throwaway object here silently
    // breaks the option list (listEl undefined -> TypeError in the promise).
    var state = { key: key, wrap: wrap, btn: btn, panel: null,
                  onChange: opts.onChange, compact: !!opts.compact };
    var panel = buildPanel(state);
    state.panel = panel;

    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var open = panel.style.display !== 'none';
      closeAll();
      if (!open) {
        panel.style.display = '';
        var s = panel.querySelector('input.sf-search');
        if (s) s.focus();
      }
    });

    wrap.appendChild(btn);
    wrap.appendChild(panel);
    host.appendChild(wrap);

    WIDGETS[key] = state;
    refresh(state);

    load().then(function () {
      try {
        fillList(state);
        refresh(state);
      } catch (e) {
        if (window.console) console.error('SourceFilter: failed to build manual list', e);
      }
    });

    return state;
  }

  function closeAll() {
    Object.keys(WIDGETS).forEach(function (k) {
      var w = WIDGETS[k];
      if (w && w.panel) w.panel.style.display = 'none';
    });
  }

  document.addEventListener('click', closeAll);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAll(); });

  // Mount declarative hosts now and whenever new DOM shows up.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(autoInit, 0); });
  } else {
    setTimeout(autoInit, 0);
  }
  try {
    new MutationObserver(function () {
      if (_autoTimer) return;
      _autoTimer = setTimeout(function () { _autoTimer = null; autoInit(); }, 50);
    }).observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) { /* observer unavailable — autoInit still ran above */ }

  /* Declarative mounting: any element carrying
   *   data-src-filter="<key>"      (optionally data-src-compact,
   *                                 data-src-onchange="<globalFnName>")
   * gets a widget automatically — including elements rendered later by JS
   * (modals, level-up steps, encounter builder). */
  function autoInit() {
    var hosts = document.querySelectorAll('[data-src-filter]');
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i];
      var key = host.getAttribute('data-src-filter');
      if (!key || WIDGETS[key]) continue;
      var cb = null;
      var cbName = host.getAttribute('data-src-onchange');
      if (cbName && typeof window[cbName] === 'function') {
        cb = (function (name) { return function () { window[name](); }; })(cbName);
      }
      init(host, {
        key: key,
        compact: host.hasAttribute('data-src-compact'),
        onChange: cb
      });
    }
  }

  function applyToList(selector, slugs, attr) {
    var attrName = attr || 'data-source';
    var visible = 0;
    document.querySelectorAll(selector).forEach(function (el) {
      var ok = matches(el.getAttribute(attrName) || '', slugs);
      el.style.display = ok ? '' : 'none';
      if (ok) visible++;
    });
    return visible;
  }

  window.SourceFilter = {
    init: init,
    autoInit: autoInit,
    load: load,
    slugs: function (key) { return (SELS[key] || []).slice(); },
    setSlugs: function (key, arr) { saveSel(key, arr || []); },
    matches: matches,
    slugForSource: slugForSource,
    applyToList: applyToList,
    closeAll: closeAll
  };
})();
