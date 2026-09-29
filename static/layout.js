/* Shared layout JS — CSRF fetch glue, theme toggle and the manual search panel, moved
   out of templates/layout.html. Loads exactly where the inline block did, before the
   page's own scripts, so its globals (searchManuals, toggleTheme) are in scope for
   inline handlers and later scripts. */
// Add the double-submit CSRF token to same-origin fetch writes.
(function () {
  const nativeFetch = window.fetch.bind(window);
  function csrfCookie() {
    const item = document.cookie.split('; ').find(row => row.startsWith('csrf_token='));
    return item ? decodeURIComponent(item.split('=')[1]) : '';
  }
  window.fetch = function (input, init) {
    init = init || {};
    const method = (init.method || (input && input.method) || 'GET').toUpperCase();
    const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
    if (url.origin === window.location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
      const token = csrfCookie();
      if (token) headers.set('X-CSRF-Token', token);
      init.headers = headers;
    }
    return nativeFetch(input, init);
  };
})();

// ── Theme toggle (light/dark) — persisted to localStorage ──
(function () {
  const KEY = 'dnd_theme';
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'light' ? '☀️' : '🌙';
  }
  function current() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') apply(saved);
  } catch (e) { /* localStorage unavailable — default dark */ }
  window.toggleTheme = function () {
    const next = current() === 'light' ? 'dark' : 'light';
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  };
})();

// ── Manual search (available on every page) ──
// Defined in <head> so available immediately — before heavy page scripts
let _lastAiSummaryData = null;
async function searchManuals(summarize = false) {
  const input = document.getElementById('manualSearchInput');
  const resultsDiv = document.getElementById('manualSearchResults');
  if (!input || !resultsDiv) { console.warn('search: missing input or resultsDiv'); return; }
  const query = input.value.trim();
  if (query.length < 2) {
    input.style.outline = '2px solid var(--warn)';
    setTimeout(() => input.style.outline = '', 1500);
    resultsDiv.style.display = 'none';
    return;
  }

  resultsDiv.style.display = 'block';
  resultsDiv.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-muted)">🔍 Searching manuals...</div>';

  const endpoint = summarize ? '/api/dm/search-manuals/summarize' : '/api/dm/search-manuals';
  if (window.EntitySearch) EntitySearch.markManual(query);

  try {
    // Internal entities come back from the fast in-memory index in parallel with
    // the (slower) server-side manual text search — one combined render below.
    const entityPromise = window.EntitySearch ? EntitySearch.fetchFor(query) : Promise.resolve(null);
    const r = await fetch(endpoint, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({query, source: (window.SourceFilter ? SourceFilter.expand(SourceFilter.slugs('nav-manuals')) : [])})
    });
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      resultsDiv.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--warn)">⚠️ Please log in to search the manuals.</div>';
      return;
    }
    const data = await r.json();
    const entityData = await entityPromise;
    const internal = (window.EntitySearch && entityData)
      ? EntitySearch.internalHtml(entityData, query) : '';
    if (window.EntitySearch) EntitySearch.finishManual(query);

    if (summarize && data.summary) {
      // Store for PDF button
      _lastAiSummaryData = {query, summary: data.summary, sources: data.results};
      let html = internal + '<div style="padding:0.75rem;font-size:0.8rem;line-height:1.6;color:var(--text)">';
      html += '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">';
      html += '<span style="font-size:0.7rem;color:var(--text-muted)">🤖 AI Summary · ' + data.results.length + ' sources across manuals</span>';
      html += '<button onclick="downloadAiSummaryPdf(_lastAiSummaryData)" style="margin-left:auto;padding:0.2rem 0.5rem;font-size:0.7rem;background:var(--accent2);color:var(--accent);border:1px solid var(--accent);border-radius:4px;cursor:pointer;white-space:nowrap" title="Download AI Summary as PDF" class="ai-pdf-btn">📄 PDF</button>';
      html += '</div>';
      html += '<div style="white-space:pre-wrap">' + escapeHtml(data.summary) + '</div>';
      html += '<hr style="border-color:var(--border);margin:0.5rem 0">';
      html += '<div style="font-size:0.7rem;color:var(--text-muted)">Sources:</div>';
      for (const r of data.results) {
        if (r.page) {
          const pdfUrl = '/api/reference/open/' + encodeURIComponent(r.book) + '#page=' + r.page;
          html += '<div style="font-size:0.75rem;padding:0.25rem 0.4rem;border-bottom:1px solid var(--border);cursor:pointer" onclick="window.open(\'' + pdfUrl + '\',\'_blank\')" onmouseover="this.style.background=\'var(--accent2)\'" onmouseout="this.style.background=\'\'">📖 <strong>' + r.book + '</strong> p.' + r.page + ' — ' + escapeHtml(r.snippet).substring(0,150) + '...</div>';
        } else {
          html += '<div style="font-size:0.75rem;padding:0.25rem 0.4rem;border-bottom:1px solid var(--border)">📄 <strong>' + r.book + '</strong> — ' + escapeHtml(r.snippet).substring(0,150) + '...</div>';
        }
      }
      html += '</div>';
      resultsDiv.innerHTML = html;
    } else if (data.results && data.results.length) {
      let html = internal + '<div style="padding:0.5rem;font-size:0.8rem">';
      html += '<div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:0.5rem">' + data.total + ' matches across manuals for "' + escapeHtml(query) + '"</div>';
      for (const r of data.results) {
        if (r.page) {
          const pdfUrl = '/api/reference/open/' + encodeURIComponent(r.book) + '#page=' + r.page;
          html += '<div style="padding:0.4rem 0.5rem;border-bottom:1px solid var(--border);cursor:pointer" onclick="window.open(\'' + pdfUrl + '\',\'_blank\')" onmouseover="this.style.background=\'var(--accent2)\'" onmouseout="this.style.background=\'\'">';
          html += '<div style="font-size:0.7rem;color:var(--accent);margin-bottom:0.15rem">📖 <strong>' + r.book + '</strong> p.' + r.page + ' ↗</div>';
          html += '<div style="font-size:0.75rem;color:var(--text);line-height:1.4">' + highlightMatch(escapeHtml(r.snippet), query) + '</div>';
          html += '</div>';
        } else {
          html += '<div style="padding:0.4rem 0.5rem;border-bottom:1px solid var(--border)">';
          html += '<div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:0.15rem">📄 <strong>' + r.book + '</strong></div>';
          html += '<div style="font-size:0.75rem;color:var(--text);line-height:1.4">' + highlightMatch(escapeHtml(r.snippet), query) + '</div>';
          html += '</div>';
        }
      }
      html += '</div>';
      resultsDiv.innerHTML = html;
    } else {
      resultsDiv.innerHTML = internal ||
        '<div style="padding:1rem;text-align:center;color:var(--text-muted)">🔍 No matches found for "' + escapeHtml(query) + '" in any manual.</div>';
    }
  } catch(e) {
    resultsDiv.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--danger)">Search failed: ' + e.message + '</div>';
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function downloadAiSummaryPdf(data) {
  try {
    const r = await fetch('/api/ai/summary/pdf', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    if (!r.ok) { alert('PDF generation failed'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch(e) {
    alert('PDF error: ' + e.message);
  }
}

function highlightMatch(text, query) {
  const words = query.split(/\s+/).filter(w => w.length > 1);
  let result = text;
  for (const w of words) {
    const re = new RegExp('(' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    result = result.replace(re, '<mark style="background:rgba(255,200,0,0.3);color:var(--text);padding:0 1px;border-radius:2px">$1</mark>');
  }
  return result;
}

// Close search results when clicking outside
document.addEventListener('click', function(e) {
  const resultsDiv = document.getElementById('manualSearchResults');
  const input = document.getElementById('manualSearchInput');
  // isConnected guard: a click whose target was re-rendered out of the DOM mid-bubble
  // (e.g. entity-search expanding a category) must not be treated as an outside click.
  if (resultsDiv && e.target.isConnected && !resultsDiv.contains(e.target) && e.target !== input
      && !e.target.closest('.nav-search')) {
    resultsDiv.style.display = 'none';
  }
});

// Hamburger menu close
document.addEventListener('click', function(e) {
  var nav = document.querySelector('.nav-links');
  var btn = document.querySelector('.hamburger');
  if (nav && nav.classList.contains('open') && !nav.contains(e.target) && !btn.contains(e.target)) {
    nav.classList.remove('open');
    btn.textContent = '☰';
  }
});

// Mark the nav link for the section the page is in, so the nav says where you are. Done here
// rather than in every template: match the link's path against the page's.
function markCurrentNav() {
  // a character sheet IS "My Characters", a campaign or map IS "DM Tools"
  var ALIAS = { '/character': '/dashboard', '/campaign': '/dm-tools', '/dm-map': '/dm-tools',
                '/npcs': '/dm-tools', '/monsters': '/dm-tools' };
  function sectionOf(path) {
    if (path === '/' || !path) return '/';
    var first = '/' + path.split('/')[1];
    return ALIAS[first] || first;
  }
  var section = sectionOf(location.pathname);
  document.querySelectorAll('.nav-links a[href]').forEach(function (a) {
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) !== '/') return;
    if (sectionOf(href) === section) {
      a.classList.add('nav-here');
      a.setAttribute('aria-current', 'page');
    }
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', markCurrentNav);
else markCurrentNav();

// Import a campaign pack from a <input type="file">. Lives here because two pages offer it (the
// DM tools campaign list and a campaign page), and one definition cannot drift from the other.
// The fetch glue above adds the CSRF header, so this can stay a plain fetch.
window.importCampaignPack = function (input) {
  const file = input && input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function () {
    let pack;
    try {
      pack = JSON.parse(reader.result);
    } catch (e) {
      alert('That file is not a campaign pack (it is not JSON).');
      return;
    }
    fetch('/api/dm/campaign/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pack)
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      const d = res.d || {};
      if (!res.ok || !d.ok) {
        alert(d.error || 'The pack could not be imported.');
        return;
      }
      const c = d.counts || {};
      let msg = 'Imported "' + d.name + '": ' + (c.characters || 0) + ' characters, ' +
        (c.npcs || 0) + ' NPCs, ' + (c.maps || 0) + ' maps, ' + (c.tokens || 0) +
        ' tokens, ' + (c.encounters || 0) + ' encounters.';
      if (d.warnings && d.warnings.length) msg += '\n\nNotes:\n' + d.warnings.join('\n');
      alert(msg);
      if (d.campaign_id) window.location.href = '/campaign/' + d.campaign_id;
    }).catch(function (e) {
      alert('The pack could not be imported: ' + e.message);
    });
  };
  reader.readAsText(file);
  input.value = '';
};
