/* sheet/live.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── The table's roll feed (read-only on the sheet) ───────────────────────────────────
// The sheet's owner is usually a player, not the campaign's DM, so this reads the
// character-scoped feed endpoint (membership proven by their own character) rather than the
// DM stream. Hidden rolls never arrive — the server withholds them.
async function initSheetRollFeed() {
  const card = document.getElementById('sheetRollFeedCard');
  const list = document.getElementById('sheetRollFeedList');
  if (!card || !list || !window.RollFeed) return;
  try {
    const r = await fetch('/api/character/' + CHAR_ID + '/feed?limit=50');
    if (!r.ok) return;
    const d = await r.json();
    if (!d.campaign) return;                 // no campaign, no feed, no empty box
    card.style.display = '';
    const label = document.getElementById('sheet-rollfeed-camp');
    if (label) label.textContent = '· ' + (d.campaign.name || '');
    window.RollFeed.init({
      listEl: list,
      statusEl: document.getElementById('sheet-rollfeed-status'),
      streamUrl: '/api/character/' + CHAR_ID + '/feed/stream',
      rollsUrl: '/api/character/' + CHAR_ID + '/feed?limit=50',
      source: 'sheet',
      publish: false                          // the sheet does not roll into the table feed
    });

    // The stream is now the mechanism for this character's HP, with a slow safety net behind it.
    window.RollFeed.on('hp', _onSheetChanged);
    window.RollFeed.on('sheet', _onSheetChanged);
    window.RollFeed.on('open', function () { startSheetPolling(SHEET_POLL_SAFE_MS); });
    window.RollFeed.on('error', function () { startSheetPolling(SHEET_POLL_FAST_MS); });
  } catch (e) { /* the sheet is fine without a feed */ }
}

/** A sheet event: HP moved (kind 'hp') or something a tracker mirrors did ('sheet' — conditions,
 *  summons). Either way the honest response is the same tested read, which refreshes them all. */
function _onSheetChanged(payload) {
  if (!payload || String(payload.character_id) !== String(CHAR_ID)) return;
  syncSheetHP();
  const st = document.getElementById('sheet-rollfeed-status');
  if (st) {
    st.textContent = payload.kind === 'hp' ? 'HP updated elsewhere' : 'Sheet updated elsewhere';
    clearTimeout(st._clear);
    st._clear = setTimeout(function () { st.textContent = ''; }, 4000);
  }
  // Damage landed while this character holds a spell: the rules owe a save, and the server sent
  // the DC with the event because only the write path knows the damage (PHB p.203).
  if (payload.concentration_dc) {
    showConcentrationCheck(payload.concentration_dc, payload.damage || 0, payload.concentration || '');
  }
}

// ── Concentration as state, with the save the rules demand ──────────────────────────────

let _concDC = 0;

async function setConcentration(spell) {
  spell = (spell || '').trim();
  if (!spell) return clearConcentration();
  const r = await fetch('/api/character/' + CHAR_ID + '/concentration', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({spell: spell})
  });
  if (!r.ok) { _concSay('Could not save concentration (' + r.status + ')'); return; }
  _concApply(spell);
}

async function clearConcentration() {
  const r = await fetch('/api/character/' + CHAR_ID + '/concentration', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({spell: ''})
  });
  if (!r.ok) return;
  _concApply('');
}

function _concApply(spell) {
  const v = document.getElementById('conc-value');
  if (v) {
    v.textContent = spell || 'None';
    v.style.color = spell ? '#f59e0b' : 'var(--text-muted)';
    v.style.opacity = spell ? '1' : '0.6';
  }
  const i = document.getElementById('conc-input');
  if (i) i.value = '';
}

function showConcentrationCheck(dc, damage, spell) {
  _concDC = dc;
  const box = document.getElementById('conc-check');
  const text = document.getElementById('conc-check-text');
  if (!box || !text) return;
  text.innerHTML = 'Took <strong>' + damage + '</strong> damage' + (spell ? ' while concentrating on <strong>' + spell + '</strong>' : '') +
                   ' — Constitution save <strong>DC ' + dc + '</strong>.';
  const res = document.getElementById('conc-check-result');
  if (res) res.textContent = '';
  box.style.display = '';
}

/** The sheet already shows this character's CON save; read it rather than recompute the rules.
 *  The label carries a proficiency dot inside the same element ("●CON"), so the letters have to be
 *  extracted before matching — matching /^CON/ silently found nothing and every save rolled +0. */
function _conSaveBonus() {
  for (const row of document.querySelectorAll('.save-row')) {
    const nameEl = row.querySelector('.stat-name');
    if (!nameEl) continue;
    const label = (nameEl.textContent || '').replace(/[^A-Za-z]/g, '').toUpperCase();
    if (label !== 'CON') continue;
    const val = row.querySelector('.stat-val');
    const n = parseInt(((val ? val.textContent : '') || '').replace(/[^0-9-]/g, ''), 10);
    return isNaN(n) ? 0 : n;
  }
  return 0;
}

async function rollConcentrationCheck() {
  const mod = _conSaveBonus();
  const roll = 1 + Math.floor(Math.random() * 20);
  const total = roll + mod;
  const ok = total >= _concDC;
  const el = document.getElementById('conc-check-result');
  if (el) {
    el.innerHTML = 'd20 <strong>' + roll + '</strong> ' + (mod < 0 ? '− ' + Math.abs(mod) : '+ ' + mod) +
                   ' = <strong>' + total + '</strong> vs DC ' + _concDC + ' — ' +
                   (ok ? '<span style="color:#4ade80">held</span>' : '<span style="color:#f87171">lost</span>');
  }
  if (!ok) await clearConcentration();
}

async function resolveConcentration(kept) {
  const el = document.getElementById('conc-check-result');
  if (!kept) {
    await clearConcentration();
    if (el) el.innerHTML += ' — concentration ended.';
  } else if (el) {
    el.innerHTML += ' — still concentrating.';
  }
  const box = document.getElementById('conc-check');
  if (box) setTimeout(function () { box.style.display = 'none'; }, 5000);
}

function _concSay(msg) {
  const el = document.getElementById('conc-check-result');
  if (el) el.textContent = msg;
}


/** The XP row, from the server's own progress numbers — the client must not own the ladder. */
function _renderXpRow(info) {
  if (!info || info.xp === undefined) return;
  const p = info.progress || {};
  const total = document.getElementById('xp-total');
  if (total) total.textContent = (info.xp || 0).toLocaleString() + ' XP';
  const bar = document.getElementById('xp-bar');
  if (bar) {
    const pct = (!p.capped && p.next_at && p.next_at > p.prev_at)
      ? Math.max(0, Math.min(100, Math.round((info.xp - p.prev_at) * 100 / (p.next_at - p.prev_at))))
      : 100;
    bar.style.width = pct + '%';
  }
  const next = document.getElementById('xp-next');
  if (next) {
    next.textContent = p.capped
      ? 'Level 20 — capped'
      : (((p.to_next || 0)).toLocaleString() + ' to L' + ((p.level || 1) + 1));
  }
  const ready = document.getElementById('xp-ready');
  if (ready) {
    if (p.ready && !p.capped) {
      ready.style.display = '';
      ready.textContent = '★ Ready to level up';
    } else {
      ready.style.display = 'none';
      ready.textContent = '';
    }
  }
}


// ── Undo, for this player's own last change ─────────────────────────────────────────────
// The same endpoint the DM's page uses; the record is per user, so a player can only ever put back
// something they did themselves.

async function refreshSheetUndo() {
  const btn = document.getElementById('sheetUndo');
  if (!btn) return;
  try {
    const r = await fetch('/api/undo/peek', {cache: 'no-store'});
    if (!r.ok) return;
    const e = (await r.json()).entry;
    btn.title = e ? ('Put back: ' + (e.label || e.kind || '')) : 'Nothing to undo';
    btn.style.color = e ? 'var(--text)' : 'var(--text-muted)';
  } catch (err) { /* convenience only */ }
}

async function undoLastSheet() {
  const btn = document.getElementById('sheetUndo');
  // Its own element, not the feed's status: the feed writes "HP updated elsewhere" there the moment
  // the undo's own event lands, which would wipe the confirmation the user is looking for.
  const say = function (msg) {
    const st = document.getElementById('sheetUndoStatus');
    if (!st) return;
    st.textContent = msg;
    clearTimeout(st._clear);
    st._clear = setTimeout(function () { st.textContent = ''; }, 6000);
  };
  say('Undoing…');
  let r;
  try {
    r = await fetch('/api/undo', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: '{}'});
  } catch (err) {
    say('⚠ Could not reach the server');
    return;
  }
  const d = await r.json().catch(function () { return {}; });
  if (!r.ok) {
    say('⚠ ' + (d.error || ('HTTP ' + r.status)));
    refreshSheetUndo();
    return;
  }
  say('↩ Undone: ' + (d.label || d.kind || 'last change'));
  refreshSheetUndo();

  // Repaint from the sync itself — it carries HP, XP and summons, so the common undo needs no
  // navigation and the confirmation stays on screen. Only a field it cannot draw (conditions, say)
  // falls back to a reload, and then after the message has been readable for a moment.
  const fields = (d.restored || []).reduce(function (acc, r) { return acc.concat(r.fields || []); }, []);
  const paintable = ['hp_current', 'hp_max', 'temp_hp', 'xp', 'summons'];
  syncSheetHP();
  if (fields.some(function (f) { return paintable.indexOf(f) === -1; })) {
    setTimeout(function () { location.reload(); }, 900);
  }
}
