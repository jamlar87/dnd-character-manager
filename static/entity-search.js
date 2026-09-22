/* entity-search.js — nav search bar's "Internal data" section.
 *
 * Live (debounced) internal-entity hits while typing; the Enter/🔍 manual PDF
 * search stays server-side and pulls the same panel in via EntitySearch.fetchFor
 * + EntitySearch.internalHtml so one render shows both sections.
 *
 * Data: GET /api/reference/entities?q=&source=   (typed, linkable rows)
 *       GET /api/reference/entity?kind=&name=    (detail payload for the modal)
 *
 * Self-contained: injects its own CSS + modal markup, so it works on every page
 * (the DM tools' item/monster modals live in dm_tools.js and are DM-page only).
 */
window.EntitySearch = (function () {
  'use strict';

  var DEBOUNCE_MS = 250;
  var PAGE = 50;            // rows fetched per click of "show N more"
  var timer = null;
  var seq = 0;              // drops out-of-order responses
  var manualMode = false;   // a manual (Enter) search is showing its own panel
  var manualQuery = '';
  var lastData = null;
  var lastKinds = {};       // kind meta from the last response (icons/labels)
  var previewByKind = {};   // preview rows per kind, for collapsing back
  var totalByKind = {};     // true per-kind totals from the last response
  var expand = {};          // kind -> {rows, total, hasMore, loading} (current query only)
  var expandQuery = '';     // query the expansion state belongs to

  // ── helpers ──────────────────────────────────────────────────────────────

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function slugs() {
    try {
      return window.SourceFilter ? (SourceFilter.expand(SourceFilter.slugs('nav-manuals')) || []) : [];
    } catch (e) { return []; }
  }

  function injectStyle() {
    if (document.getElementById('es-style')) return;
    var css = '' +
      '.es-panel{border-bottom:2px solid var(--border);margin-bottom:0.25rem}' +
      '.es-panel-head{padding:0.5rem 0.6rem 0.3rem;font-size:0.68rem;letter-spacing:0.03em;' +
        'text-transform:uppercase;color:var(--text-muted);display:flex;gap:0.4rem;align-items:center}' +
      '.es-panel-head .es-hint{margin-left:auto;text-transform:none;letter-spacing:0;opacity:0.75}' +
      '.es-group{padding:0 0.3rem 0.3rem}' +
      '.es-group-head{display:flex;align-items:center;gap:0.4rem;font-size:0.7rem;color:var(--accent);' +
        'padding:0.28rem 0.3rem;font-weight:600;cursor:pointer;border-radius:4px;user-select:none}' +
      '.es-group-head:hover,.es-group-head:focus{background:var(--accent2);outline:none}' +
      '.es-count{color:var(--text-muted);font-weight:400}' +
      '.es-caret{margin-left:auto;color:var(--text-muted);font-size:0.72rem}' +
      '.es-more{font-size:0.68rem;color:var(--accent);padding:0.32rem 0.45rem;cursor:pointer;border-radius:4px}' +
      '.es-more:hover,.es-more:focus{background:var(--accent2);outline:none}' +
      '.es-loading{font-size:0.68rem;color:var(--text-muted);padding:0.32rem 0.45rem}' +
      '.es-row{display:flex;gap:0.5rem;align-items:baseline;padding:0.32rem 0.45rem;border-radius:4px;' +
        'cursor:pointer;font-size:0.78rem;line-height:1.35;overflow:hidden}' +
      '.es-row:hover,.es-row:focus{background:var(--accent2);outline:none}' +
      '.es-name{color:var(--text);font-weight:500;flex-shrink:0;max-width:60%}' +
      '.es-sub{color:var(--text-muted);font-size:0.68rem;min-width:0;overflow:hidden;' +
        'text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto}' +
      '.es-empty{padding:0.6rem;font-size:0.75rem;color:var(--text-muted);text-align:center}' +
      '.es-modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:4000;' +
        'overflow-y:auto;padding:2rem 1rem}' +
      '.es-modal-overlay.open{display:block}' +
      '.es-modal{max-width:780px;margin:0 auto;background:var(--bg);border:1px solid var(--border);' +
        'border-radius:8px;padding:1.1rem 1.2rem;color:var(--text)}' +
      '.es-modal h2{margin:0;font-size:1.15rem}' +
      '.es-modal .es-modal-sub{color:var(--text-muted);font-size:0.8rem;margin:0.3rem 0 0.5rem}' +
      '.es-modal .es-src{font-size:0.72rem;color:var(--text-muted);margin-bottom:0.6rem}' +
      '.es-modal .es-src a{color:var(--accent);text-decoration:none}' +
      '.es-stats{display:flex;flex-wrap:wrap;gap:0.4rem 1rem;font-size:0.8rem;margin:0.5rem 0}' +
      '.es-stat b{color:var(--text-muted);font-weight:600;margin-right:0.25rem}' +
      '.es-body{margin-top:0.7rem;font-size:0.85rem;line-height:1.55;white-space:pre-wrap}' +
      '.es-close{float:right;background:transparent;border:1px solid var(--border);color:var(--text-muted);' +
        'border-radius:4px;cursor:pointer;padding:0.15rem 0.5rem;font-size:0.75rem}';
    var el = document.createElement('style');
    el.id = 'es-style';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // ── data ─────────────────────────────────────────────────────────────────

  async function fetchFor(query) {
    if (!query || query.trim().length < 2) return null;
    try {
      var url = '/api/reference/entities?q=' + encodeURIComponent(query.trim()) +
                '&source=' + encodeURIComponent(slugs().join(','));
      var r = await fetch(url);
      if (!(r.headers.get('content-type') || '').includes('application/json')) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // ── render ───────────────────────────────────────────────────────────────

  // Reference art thumb (monsters/items/NPCs share one library route). A not-yet
  // generated image stays invisible rather than showing a broken glyph.
  function artKind(kind) {
    return kind === 'creature' || kind === 'item' || kind === 'npc' ? kind : '';
  }
  function rowArt(row) {
    var k = artKind(row.kind);
    if (!k) return '';
    return '<img class="es-art" loading="lazy" decoding="async" alt="" ' +
      'style="width:22px;height:22px;border-radius:4px;object-fit:cover;flex-shrink:0;border:1px solid var(--border);background:var(--bg)" ' +
      'src="/api/ref-image/' + k + '/' + encodeURIComponent(row.name) + '?size=48" ' +
      "onerror=\"this.style.visibility='hidden'\">";
  }
  function rowHtml(row) {
    return '<div class="es-row" role="button" tabindex="0" data-kind="' + esc(row.kind) +
      '" data-name="' + esc(row.name) + '">' +
      rowArt(row) +
      '<span class="es-name">' + esc(row.name) + '</span>' +
      (row.subtitle ? '<span class="es-sub">' + esc(row.subtitle) + '</span>' : '') +
      '</div>';
  }

  function groupHeadHtml(kind, shown, total) {
    var expanded = !!expand[kind];
    return '<div class="es-group-head" role="button" tabindex="0" data-kind-toggle="' + esc(kind) + '"' +
      (expanded ? ' aria-expanded="true"' : ' aria-expanded="false"') + '>' +
      groupHeadInner(kind, shown, total) + '</div>';
  }

  function groupHeadInner(kind, shown, total) {
    var meta = lastKinds[kind] || {};
    return esc(meta.icon || '•') + ' ' + esc(meta.label || kind) +
      ' <span class="es-count">' + shown + ' of ' + total + '</span>' +
      '<span class="es-caret">' + (expand[kind] ? '▾' : '▸') + '</span>';
  }

  function groupBodyHtml(kind, previewRows) {
    var ex = expand[kind];
    if (!ex) return (previewRows || []).map(rowHtml).join('');
    var html = ex.rows.map(rowHtml).join('');
    if (!ex.rows.length) {
      html += '<div class="es-loading">' + (ex.loading ? 'Loading…' : 'Nothing to show') + '</div>';
      return html;
    }
    if (ex.hasMore) {
      var step = Math.min(PAGE, Math.max(0, ex.total - ex.rows.length));
      html += '<div class="es-more" role="button" tabindex="0" data-kind-more="' + esc(kind) + '"' +
        (ex.loading ? ' aria-busy="true"' : '') + '>' +
        (ex.loading ? 'Loading…' : 'show ' + step + ' more ▸') + '</div>';
    } else {
      html += '<div class="es-loading">— end of list —</div>';
    }
    return html;
  }

  function syncQuery(query) {
    if (query !== expandQuery) { expand = {}; expandQuery = query; }
  }

  function internalHtml(data, query, opts) {
    opts = opts || {};
    syncQuery(query);
    if (data && data.kinds) lastKinds = data.kinds;
    if (!data || !data.results || !data.results.length) return '';
    var totals = data.total_counts || {};
    var groups = {}, order = [];
    data.results.forEach(function (row) {
      if (!groups[row.kind]) { groups[row.kind] = []; order.push(row.kind); }
      groups[row.kind].push(row);
    });
    previewByKind = groups;
    totalByKind = totals;

    var html = '<div class="es-panel"><div class="es-panel-head">' +
      '📚 Internal data · ' + data.count + ' match' + (data.count === 1 ? '' : 'es') +
      (opts.hint ? '<span class="es-hint">↵ Enter also searches manual text</span>' : '') +
      '</div>';

    order.forEach(function (kind) {
      var shown = expand[kind] ? expand[kind].rows.length : groups[kind].length;
      var total = totals[kind] || groups[kind].length;
      html += '<div class="es-group" data-group="' + esc(kind) + '">' +
        groupHeadHtml(kind, shown, total) +
        '<div class="es-rows">' + groupBodyHtml(kind, groups[kind]) + '</div>' +
        '</div>';
    });
    return html + '</div>';
  }

  function resultsDiv() { return document.getElementById('manualSearchResults'); }

  function render(data, query, opts) {
    var div = resultsDiv();
    if (!div) return;
    var html = internalHtml(data, query, opts);
    if (!html) return;
    div.innerHTML = html;
    div.style.display = 'block';
    lastData = data;
  }

  // ── category expansion ───────────────────────────────────────────────────

  function currentQuery() {
    var inp = document.getElementById('manualSearchInput');
    var q = inp ? (inp.value || '').trim() : '';
    return q || expandQuery;
  }

  async function fetchKind(query, kind, offset) {
    try {
      var url = '/api/reference/entities?q=' + encodeURIComponent(query) +
                '&kind=' + encodeURIComponent(kind) + '&offset=' + offset + '&limit=' + PAGE +
                '&source=' + encodeURIComponent(slugs().join(','));
      var r = await fetch(url);
      if (!(r.headers.get('content-type') || '').includes('application/json')) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  function rerenderGroup(kind) {
    var group = document.querySelector('.es-group[data-group="' + kind + '"]');
    if (!group) return;
    var head = group.querySelector('.es-group-head');
    var rowsEl = group.querySelector('.es-rows');
    if (!head || !rowsEl) return;
    var previewRows = previewByKind[kind] || [];
    var total = totalByKind[kind] || previewRows.length;
    var ex = expand[kind];
    // Mutate the head IN PLACE (never outerHTML): the head (or a "show more" row)
    // is the event target when this runs, and detaching it makes the click's
    // target `isConnected === false`, which defeats layout.html's outside-click
    // guard and closes the whole dropdown.
    head.setAttribute('aria-expanded', ex ? 'true' : 'false');
    head.innerHTML = groupHeadInner(kind, ex ? ex.rows.length : previewRows.length, total);
    rowsEl.innerHTML = groupBodyHtml(kind, previewRows);
  }

  async function toggleKind(kind, query) {
    query = query || currentQuery();
    if (expand[kind]) { delete expand[kind]; rerenderGroup(kind); return; }
    syncQuery(query);
    expand[kind] = { rows: [], total: totalByKind[kind] || 0, hasMore: false, loading: true };
    rerenderGroup(kind);
    var data = await fetchKind(query, kind, 0);
    if (!data) { delete expand[kind]; rerenderGroup(kind); return; }
    expand[kind] = { rows: data.results || [], total: data.total || 0,
                     hasMore: !!data.has_more, loading: false };
    rerenderGroup(kind);
  }

  async function loadMore(kind, query) {
    var ex = expand[kind];
    if (!ex || ex.loading || !ex.hasMore) return;
    query = query || currentQuery();
    ex.loading = true;
    rerenderGroup(kind);
    var data = await fetchKind(query, kind, ex.rows.length);
    if (data) {
      ex.rows = ex.rows.concat(data.results || []);
      ex.total = data.total || ex.total;
      ex.hasMore = !!data.has_more;
    }
    ex.loading = false;
    rerenderGroup(kind);
  }

  // ── live typing ──────────────────────────────────────────────────────────

  function onInput() {
    var input = document.getElementById('manualSearchInput');
    if (!input) return;
    var q = (input.value || '').trim();
    clearTimeout(timer);
    if (manualMode && q === manualQuery) return;   // manual panel already shows both
    manualMode = false;
    if (q.length < 2) return;
    timer = setTimeout(async function () {
      var mine = ++seq;
      var data = await fetchFor(q);
      if (mine !== seq) return;                    // a newer keystroke won
      var input2 = document.getElementById('manualSearchInput');
      if (!input2 || (input2.value || '').trim() !== q) return;
      render(data, q, { hint: true });
    }, DEBOUNCE_MS);
  }

  function markManual(query) { manualMode = true; manualQuery = (query || '').trim(); }
  function finishManual(query) { manualMode = true; manualQuery = (query || '').trim(); }

  // ── detail modal ─────────────────────────────────────────────────────────

  function modalEl() {
    injectStyle();
    var overlay = document.getElementById('es-modal');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'es-modal-overlay';
    overlay.id = 'es-modal';
    overlay.innerHTML = '<div class="es-modal" role="dialog" aria-modal="true">' +
      '<button class="es-close" type="button">✕ close</button>' +
      '<div id="es-modal-body"></div></div>';
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay || e.target.classList.contains('es-close')) close();
    });
    document.body.appendChild(overlay);
    return overlay;
  }

  function close() {
    var overlay = document.getElementById('es-modal');
    if (overlay) overlay.classList.remove('open');
  }

  async function open(kind, name) {
    var overlay = modalEl();
    var body = document.getElementById('es-modal-body');
    body.innerHTML = '<div class="es-empty">Loading…</div>';
    overlay.classList.add('open');
    try {
      var r = await fetch('/api/reference/entity?kind=' + encodeURIComponent(kind) +
                          '&name=' + encodeURIComponent(name));
      // A logged-out/expired session returns the login page (HTML, after a redirect),
      // which would otherwise surface as "Unexpected token '<' ... is not valid JSON".
      if (!(r.headers.get('content-type') || '').includes('application/json')) {
        body.innerHTML = '<div class="es-empty">Please log in to view this entry.</div>';
        return;
      }
      if (!r.ok) {
        var msg = 'Could not load this entry.';
        try { msg = (await r.json()).error || msg; } catch (e) {}
        body.innerHTML = '<div class="es-empty">' + esc(msg) + '</div>';
        return;
      }
      var d = await r.json();
      var html = '<h2>' + esc(d.icon || '') + ' ' + esc(d.name) + '</h2>' +
        (d.subtitle ? '<div class="es-modal-sub">' + esc(d.subtitle) + '</div>' : '');
      if (d.source) {
        var link = '';
        if (d.slug) {
          var url = '/api/reference/open/' + encodeURIComponent(d.slug) +
                    (d.page ? '?page=' + d.page + '#page=' + d.page : '');
          link = ' · <a href="' + esc(url) + '" target="_blank" rel="noopener">open in manual ↗</a>';
        }
        html += '<div class="es-src">📚 ' + esc(d.source) + link + '</div>';
      }
      if (d.stats && d.stats.length) {
        html += '<div class="es-stats">';
        d.stats.forEach(function (s) {
          html += '<span class="es-stat"><b>' + esc(s.label) + '</b>' + esc(s.value) + '</span>';
        });
        html += '</div>';
      }
      html += '<div class="es-body">' + esc(d.body || '') + '</div>';
      body.innerHTML = html;
      overlay.scrollTop = 0;
    } catch (e) {
      body.innerHTML = '<div class="es-empty">Search failed: ' + esc(e.message) + '</div>';
    }
  }

  // ── wiring ───────────────────────────────────────────────────────────────

  function wire() {
    injectStyle();   // panel styles must exist even if no detail modal is ever opened
    var input = document.getElementById('manualSearchInput');
    if (!input) return;
    input.addEventListener('input', onInput);
    input.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    var div = resultsDiv();
    if (div) {
      div.addEventListener('click', function (e) {
        var head = e.target.closest('[data-kind-toggle]');
        if (head) { e.stopPropagation(); toggleKind(head.getAttribute('data-kind-toggle')); return; }
        var more = e.target.closest('[data-kind-more]');
        if (more) { e.stopPropagation(); loadMore(more.getAttribute('data-kind-more')); return; }
        var row = e.target.closest('.es-row');
        if (row) open(row.getAttribute('data-kind'), row.getAttribute('data-name'));
      });
      div.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var head = e.target.closest('[data-kind-toggle]');
        if (head) {
          e.preventDefault();
          toggleKind(head.getAttribute('data-kind-toggle'));
          return;
        }
        var more = e.target.closest('[data-kind-more]');
        if (more) {
          e.preventDefault();
          loadMore(more.getAttribute('data-kind-more'));
          return;
        }
        var row = e.target.closest('.es-row');
        if (row) {
          e.preventDefault();
          open(row.getAttribute('data-kind'), row.getAttribute('data-name'));
        }
      });
    }
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }

  return {
    fetchFor: fetchFor,
    fetchKind: fetchKind,
    internalHtml: internalHtml,
    markManual: markManual,
    finishManual: finishManual,
    toggleKind: toggleKind,
    loadMore: loadMore,
    open: open,
    close: close,
    _onInput: onInput,
    _state: function () { return { expand: expand, expandQuery: expandQuery,
                                   previewByKind: previewByKind, totalByKind: totalByKind }; }
  };
})();
