/* dm/maps.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Maps ────────────────────────────────────────────────────────────────────────────
// The map layer's entry point. A map's canvas lives on its own page (/dm-map/{id}) — this
// tab is only the list, so switching tabs (or coming back) does not pay for a canvas.
//
// It is also the one list in the app that grows without limit: the corpus import left 689 maps
// here, and rendering them all as rows meant ~470 KB of HTML and 689 nodes to build before
// anything appeared. So the list is grouped by source book into COLLAPSED sections, filtered by a
// search box, and a section renders its rows only when it is open. The default view is a handful of
// headers, whatever the library grows to.
let _mapsCache = null;
let _mapFilter = '';
let _openBooks = new Set();
let _bookLabels = {};
// The rendered order of the book groups, so a header's handler can pass an INDEX rather than the
// book's name. Interpolating a name into an inline handler is what broke this: dmEsc turns an
// apostrophe into &#39;, the browser decodes it back inside onclick, and the handler becomes
// `toggleMapBook(this, 'Explorer's Guide to Wildemount')` — a syntax error, so the click did nothing.
let _bookOrder = [];

function bookLabels(maps) {
  // The ingest names a map "<Title> — <Book>" and a page "<Book> p<N>", so the book's human label
  // is already in the name; the slug alone ("TFS") would be a worse group heading than the label.
  const out = {};
  for (const m of maps) {
    const key = m.source_manual;
    if (!key) continue;
    const parts = String(m.name || '').split(' — ');
    let label = '';
    if (parts.length > 1) {
      label = parts[parts.length - 1].trim();          // "<Title> — <Book>"
    } else {
      // a page map is named "<Book> p<N>": the book is what is left when the page goes
      const m2 = String(m.name || '').match(/^(.*?)\s+p\s*\d+$/);
      if (m2) label = m2[1].trim();
    }
    if (label) out[key] = label;
  }
  return out;
}

function mapBook(m) {
  return m.source_manual ? (_bookLabels[m.source_manual] || m.source_manual) : 'No source book';
}

function mapThumb(m) {
  // 689 rows must not mean 689 full-size images: the thumb is 96px WebP, generated once and cached
  // on disk under the art's content hash. `loading="lazy"` is what makes it safe — only the rows in
  // an OPEN group are in the DOM, and only the ones on screen are fetched.
  if (!m.image_path) return '';
  const hash = String(m.image_path).replace(/^.*-([0-9a-f]{6,})\\.[a-z]+$/i, '$1');
  return `<img src="/api/dm/map/${m.id}/thumb?size=96&v=${encodeURIComponent(hash)}"
    alt="" loading="lazy" decoding="async"
    onerror="this.style.display='none'"
    style="width:52px;height:52px;flex:0 0 52px;object-fit:contain;border-radius:4px;background:var(--bg);border:1px solid var(--border)">`;
}

function mapRow(m) {
  // Only show what exists: "0 tokens · 0 setups" on every fresh import is noise that costs a line
  // of screen on a phone.
  const meta = [m.grid_type + ' grid', m.grid_size + 'px'];
  if (m.token_count) meta.push(m.token_count === 1 ? '1 token' : m.token_count + ' tokens');
  if (m.scene_count) meta.push(m.scene_count === 1 ? '1 setup' : m.scene_count + ' setups');
  if (!m.image_path) meta.push('no image yet');
  // The source link gets its own line and the theme's link colour. Set in the muted grey at 0.72rem
  // it was indistinguishable from the metadata beside it — a link nobody can see is not a link.
  const src = m.source_manual
    ? `<a href="/api/reference/open/${encodeURIComponent(m.source_manual)}${m.source_page ? '?page=' + m.source_page + '#page=' + m.source_page : ''}"
         target="_blank" rel="noopener" title="Open this map's page in the source manual"
         style="color:var(--accent);text-decoration:underline;font-size:.74rem;display:inline-block;margin-top:.15rem"
       >📖 Source: ${dmEsc(mapBook(m))}${m.source_page ? ' p.' + m.source_page : ''} ↗</a>`
    : '<span style="font-size:.72rem;color:var(--text-muted);display:inline-block;margin-top:.15rem">no source recorded</span>';
  return `<div style="display:flex;align-items:center;gap:.5rem;padding:.4rem .5rem;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;margin-bottom:.4rem">
    ${mapThumb(m) || '<span style="font-size:1.2rem;width:44px;flex:0 0 44px;text-align:center">🗺️</span>'}
    <span style="flex:1 1 auto;min-width:0">
      <strong style="display:block;overflow-wrap:anywhere">${dmEsc(m.name)}</strong>
      <span style="font-size:.72rem;color:var(--text-muted);display:block">${meta.join(' · ')}</span>
      ${src}
    </span>
    <a class="btn btn-primary btn-sm" href="/dm-map/${m.id}" style="flex:0 0 auto">Open</a>
    <button class="btn btn-danger btn-sm" style="flex:0 0 auto;padding:.15rem .4rem" title="Delete this map" onclick="deleteMap(${m.id})">✕</button>
  </div>`;
}

function mapMatches(m, q) {
  if (!q) return true;
  return (m.name + ' ' + mapBook(m) + ' ' + m.grid_type).toLowerCase().includes(q);
}

function renderMapList() {
  const host = document.getElementById('mapList');
  if (!host) return;
  const maps = _mapsCache || [];
  const q = _mapFilter.trim().toLowerCase();
  const shown = maps.filter(m => mapMatches(m, q));
  const books = new Map();
  for (const m of shown) {
    const b = mapBook(m);
    if (!books.has(b)) books.set(b, []);
    books.get(b).push(m);
  }
  const ordered = [...books.entries()].sort((a, b) =>
    a[0] === 'No source book' ? 1 : b[0] === 'No source book' ? -1 : a[0].localeCompare(b[0]));
  const searching = !!q;
  _bookOrder = ordered.map(([book]) => book);
  let html = '';
  for (let bi = 0; bi < ordered.length; bi++) {
    const [book, items] = ordered[bi];
    // while searching, sections open themselves: a hit you have to expand twice is not a hit
    const open = searching || _openBooks.has(book);
    html += `<div style="margin-bottom:.5rem;border:1px solid var(--border);border-radius:6px;overflow:hidden">
      <button type="button" onclick="toggleMapBook(${bi})"
        aria-expanded="${open}" style="width:100%;display:flex;align-items:center;gap:.5rem;padding:.5rem .6rem;background:var(--card-bg);border:0;border-radius:0;color:var(--text);cursor:pointer;font:inherit;text-align:left">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${open ? '▾' : '▸'} <strong>${dmEsc(book)}</strong></span>
        <span style="font-size:.75rem;color:var(--text-muted)">${items.length} map${items.length === 1 ? '' : 's'}</span>
      </button>
      ${open ? `<div style="padding:.4rem .5rem">${items.map(mapRow).join('')}</div>` : ''}
    </div>`;
  }
  host.innerHTML = `<p style="font-size:.8rem;color:var(--text-muted);margin:.2rem 0 .5rem">
      ${shown.length} of ${maps.length} map${maps.length === 1 ? '' : 's'}${searching ? ' matching “' + dmEsc(_mapFilter) + '”' : ''}
    </p>
    <input id="mapSearch" type="search" placeholder="Search maps by name or book…" value="${dmEsc(_mapFilter)}"
      oninput="filterMaps(this.value)"
      style="width:100%;max-width:26rem;margin-bottom:.6rem;padding:.35rem .5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.85rem">
    ${html || '<p style="font-size:.85rem;color:var(--text-muted)">No maps match that search.</p>'}`;
}

function filterMaps(value) {
  _mapFilter = value || '';
  // keep the caret where it was: re-rendering the input on every keystroke would drop focus
  const host = document.getElementById('mapList');
  const focused = document.activeElement && document.activeElement.id === 'mapSearch';
  const pos = focused ? document.activeElement.selectionStart : 0;
  renderMapList();
  if (focused) {
    const el = document.getElementById('mapSearch');
    if (el) { el.focus(); try { el.setSelectionRange(pos, pos); } catch (e) {} }
  }
}

function toggleMapBook(index) {
  const book = _bookOrder[index];
  if (book === undefined) return;
  if (_openBooks.has(book)) _openBooks.delete(book); else _openBooks.add(book);
  renderMapList();
}

async function renderMaps() {
  const host = document.getElementById('mapList');
  if (!host) return;
  if (_mapsCache) { renderMapList(); return; }      // tab switches must not refetch
  host.innerHTML = '<p style="font-size:.85rem;color:var(--text-muted)">Loading maps…</p>';
  try {
    const d = await (await fetch('/api/dm/maps')).json();
    _mapsCache = d.maps || [];
    _bookLabels = bookLabels(_mapsCache);
    if (!_mapsCache.length) {
      host.innerHTML = '<p style="font-size:.85rem;color:var(--text-muted)">No maps yet. Create one above, then upload a background image from the map page.</p>';
      return;
    }
    renderMapList();
  } catch (e) {
    host.innerHTML = '<p style="color:var(--danger)">Failed to load maps.</p>';
  }
}

async function createMap() {
  const nameEl = document.getElementById('newMapName');
  const gridEl = document.getElementById('newMapGrid');
  const name = ((nameEl && nameEl.value) || '').trim();
  if (!name) { if (nameEl) nameEl.focus(); return; }
  const r = await fetch('/api/dm/map/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, grid_type: (gridEl && gridEl.value) || 'square'})
  });
  const d = await r.json();
  if (d && d.ok) window.location = '/dm-map/' + d.id;
  else alert((d && d.error) || 'Could not create the map');
}

async function deleteMap(id) {
  // the name is looked up rather than passed in: a map called "The King's Road" would otherwise
  // break its own delete button exactly the way the Wildemount group heading did
  const m = (_mapsCache || []).find(x => x.id === id);
  const name = m ? m.name : 'this map';
  if (!confirm('Delete the map "' + name + '" and everything placed on it?')) return;
  try {
    const d = await api('/api/dm/map/' + id + '/delete', { method: 'POST' });
    if (d && d.ok) { _mapsCache = null; renderMaps(); }   // the cached list still holds the deleted row
  } catch (e) {
    alert('Could not delete the map: ' + e.message);
  }
}

