/* dm/rollfeed.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Roll feed (the table channel) ────────────────────────────────────────────────────
// Every roll made anywhere in the app lands in one campaign-scoped log, pushed over SSE.
// The panel follows the campaign picked in the combat bar; the players' devices read the
// same feed through a campaign key at /table/{key}, with hidden rolls withheld by the server.
let _rfCampId = null;
let _rfShowHidden = true;        // view preference for THIS screen only
let _rfMyRollsHidden = false;    // whether the DM's own rolls go out hidden

function initRollFeed() {
  const list = document.getElementById('rollFeedList');
  if (!window.RollFeed || !list) return;

  if (!_combatCampId) {
    _rfCampId = null;
    window.RollFeed.disconnect();
    // Detach too: with no campaign there is nowhere to publish a roll, and a sink that
    // still fires would fill the console with failures.
    if (window.DiceRoller && window.DiceRoller.setSink) window.DiceRoller.setSink(null);
    list.textContent = '';
    const hint = document.createElement('div');
    hint.className = 'rf-empty';
    hint.textContent = 'Select a campaign to follow its rolls.';
    list.appendChild(hint);
    return;
  }
  if (_rfCampId === _combatCampId) return;   // already following this one

  _rfCampId = _combatCampId;
  window.RollFeed.init({
    listEl: list,
    statusEl: document.getElementById('rfStatus'),
    streamUrl: '/api/campaign/' + _rfCampId + '/stream',
    rollsUrl: '/api/campaign/' + _rfCampId + '/rolls',
    rollUrl: '/api/campaign/' + _rfCampId + '/roll',
    source: 'dm-tools',
    hidden: _rfMyRollsHidden
  });
  window.RollFeed.setFilter(r => _rfShowHidden || !r.hidden);

  // The same stream carries sheet events, so the tracker stops needing its own 2s timer: a
  // player rests, a condition lands, a summon takes damage — the tracker refreshes on the poke
  // and the interval drops to a safety net. It comes back to 2s if the stream drops.
  window.RollFeed.on('hp', _refreshTrackerIfTracked);
  window.RollFeed.on('sheet', _refreshTrackerIfTracked);
  window.RollFeed.on('open', function () { startCombatPolling(COMBAT_POLL_SAFE_MS); });
  window.RollFeed.on('error', function () { startCombatPolling(COMBAT_POLL_FAST_MS); });

  loadCampaignXp();          // the campaign's ledger, on the same selection as everything else
  refreshUndoLabel();        // and what undo would put back, if anything
}

/** Refresh the tracker only for a character it actually shows — on whose sheet this is, or on a
 *  summon's owner. Anything else is a request that can only return rows we do not track. */
function _refreshTrackerIfTracked(payload) {
  if (!payload || payload.character_id === undefined) return;
  if (_tracksCharacter(payload.character_id)) syncPlayerAndSummonHP();
}

function _tracksCharacter(charId) {
  const id = String(charId);
  for (const p of _campaignPlayers) if (String(p.char_id) === id) return true;
  for (const p of _combatParticipants) {
    if (String(p.char_id) === id) return true;
    if (p.owner_char_id !== undefined && p.owner_char_id !== null && String(p.owner_char_id) === id) return true;
  }
  return false;
}

function setRollFeedMode() {
  const sel = document.getElementById('rfRollMode');
  _rfMyRollsHidden = !!(sel && sel.value === '1');
  if (window.RollFeed) window.RollFeed.setOptions({ hidden: _rfMyRollsHidden });
  const st = document.getElementById('rfStatus');
  if (st) st.textContent = _rfMyRollsHidden ? 'Your rolls are hidden from the players' : '';
}

function toggleRollFeedHidden() {
  _rfShowHidden = !_rfShowHidden;
  const btn = document.getElementById('rfHiddenToggle');
  if (btn) {
    btn.textContent = _rfShowHidden ? '🙈 Hide my hidden rolls' : '👁️ Show my hidden rolls';
  }
  if (window.RollFeed) window.RollFeed.setFilter(r => _rfShowHidden || !r.hidden);
}

async function shareTableLink() {
  if (!_combatCampId) return;
  const st = document.getElementById('rfStatus');
  try {
    const r = await fetch('/api/campaign/' + _combatCampId + '/player-key');
    const d = await r.json();
    if (!d.player_key) {
      if (st) st.textContent = 'Could not mint a player link';
      return;
    }
    const url = location.origin + d.url;
    try {
      await navigator.clipboard.writeText(url);
      if (st) st.textContent = 'Copied: ' + url;
    } catch (e) {
      window.prompt('Players\' link (copy it):', url);
    }
  } catch (e) {
    if (st) st.textContent = 'Could not mint a player link';
  }
}

function refreshRollFeed() {
  _rfCampId = null;                       // force a re-init: history is refetched
  if (window.RollFeed) window.RollFeed.disconnect();
  initRollFeed();
}


// ── Experience: the ledger for the selected campaign ────────────────────────────────────
// The panel never computes a level: thresholds and progress come from /xp, which reads
// services/leveling.py — one place decides what a level costs, on both the DM's screen and the
// player's sheet.

function loadCampaignXp() {
  const campId = _combatCampId;
  const panel = document.getElementById('xpPanel');
  if (!campId) {
    if (panel) panel.style.display = 'none';
    return;
  }
  if (panel) panel.style.display = '';
  fetch('/api/dm/campaign/' + campId + '/xp', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      const roster = document.getElementById('xpRoster');
      if (roster) {
        const html = (d.characters || []).map(function (c) {
          const p = c.progress || {};
          const ready = p.ready
            ? ' <span style="color:var(--success,#4ade80);font-weight:700" title="Has earned level ' + p.level + '">★</span>'
            : '';
          const bar = (!p.capped && p.next_at && p.next_at > p.prev_at)
            ? Math.max(0, Math.min(100, Math.round((c.xp - p.prev_at) * 100 / (p.next_at - p.prev_at))))
            : 100;
          return '<span style="display:inline-flex;align-items:center;gap:0.3rem;padding:0.2rem 0.45rem;border:1px solid var(--border);border-radius:12px;font-size:0.7rem" title="' + c.xp + ' XP · ' + (p.to_next || 0) + ' to the next level">'
            + '<strong>' + c.name + '</strong> L' + c.level + ready
            + '<span style="width:46px;height:4px;background:var(--bg);border:1px solid var(--border);border-radius:3px;overflow:hidden"><span style="display:block;height:100%;width:' + bar + '%;background:var(--accent)"></span></span>'
            + '<span style="color:var(--text-muted)">' + c.xp + '</span></span>';
        }).join('');
        roster.innerHTML = html || '<span style="font-size:0.7rem;color:var(--text-muted)">No characters in this campaign</span>';
      }
      const log = document.getElementById('xpLog');
      if (log) {
        log.innerHTML = (d.log || []).slice(0, 5).map(function (e) {
          const line = (e.amount > 0 ? '+' : '') + e.amount + ' · ' + (e.note || 'no note') +
                       ' · ' + (e.recipients || 0) + ' PC' + (e.recipients === 1 ? '' : 's') + ' · ' + e.created_at;
          return e.reversed
            ? '<div style="text-decoration:line-through;opacity:0.55">' + line + ' — undone</div>'
            : '<div>' + line + '</div>';
        }).join('');
      }
    })
    .catch(function () {});
}

async function awardXp() {
  const campId = _combatCampId;
  if (!campId) { setXpStatus('Select a campaign first'); return; }
  const amountEl = document.getElementById('xp-amount');
  const noteEl = document.getElementById('xp-note');
  const amount = parseInt(((amountEl || {}).value || '').trim(), 10);
  if (!amount) { setXpStatus('Amount must be a number'); return; }
  setXpStatus('Awarding…');
  let d;
  try {
    d = await api('/api/dm/campaign/' + campId + '/award-xp', {
      body: {amount: amount, note: ((noteEl || {}).value || '').trim()}
    });
  } catch (e) {
    setXpStatus('⚠ ' + e.message);
    return;
  }
  if (amountEl) amountEl.value = '';
  if (noteEl) noteEl.value = '';
  setXpStatus('+' + d.awarded + ' XP to ' + d.recipients + ' character' + (d.recipients === 1 ? '' : 's'));
  loadCampaignXp();
}

function setXpStatus(msg) {
  const el = document.getElementById('xpStatus');
  if (el) el.textContent = msg;
}


// ── Undo: the last thing this DM changed, put back ──────────────────────────────────────
// The button names what it will restore before you press it (GET /api/undo/peek) and says why when
// it refuses — an undo that silently declines would be as bad as one that clobbers.

async function refreshUndoLabel() {
  const btn = document.getElementById('btnUndo');
  if (!btn) return;
  try {
    const r = await fetch('/api/undo/peek', {cache: 'no-store'});
    if (!r.ok) return;
    const d = await r.json();
    const e = d.entry;
    if (e) {
      btn.disabled = false;
      btn.textContent = '↩ Undo: ' + (e.label || e.kind || 'last change');
      btn.title = 'Put back: ' + (e.label || e.kind || '');
    } else {
      btn.disabled = true;
      btn.textContent = '↩ Undo';
      btn.title = 'Nothing to undo';
    }
  } catch (err) { /* the button is a convenience; a failed peek must not break the page */ }
}

async function undoLast() {
  const btn = document.getElementById('btnUndo');
  if (btn && btn.disabled) return;
  setUndoStatus('Undoing…');
  let r;
  try {
    r = await fetch('/api/undo', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: '{}'});
  } catch (err) {
    setUndoStatus('⚠ Could not reach the server');
    return;
  }
  const d = await r.json().catch(function () { return {}; });
  if (!r.ok) {
    setUndoStatus('⚠ ' + (d.error || ('HTTP ' + r.status)));
    refreshUndoLabel();
    return;
  }
  setUndoStatus('↩ Undone: ' + (d.label || d.kind || 'last change'));
  refreshUndoLabel();

  // If the undo healed a player the tracker had benched, the bench has to go with it: a card at
  // 51/51 wearing a 💀 is the same contradiction this slice set out to remove, pointing the other
  // way. The tracker's flag is the DM's shorthand for "down", and the sheet now says otherwise.
  let unbenched = false;
  (d.restored || []).forEach(function (item) {
    const values = item.values || {};
    if ((values.hp_current === undefined) || values.hp_current <= 0) return;
    const p = (_combatParticipants || []).find(x => x.char_id === item.character_id);
    if (p && p.defeated) {
      p.defeated = 0;
      unbenched = true;
      _combatOrder = _combatParticipants.filter(x => !x.defeated).map(x => x.en_id);
    }
  });
  if (unbenched) {
    renderInitiativeTrack();
    saveCombatState();
  }
  // The publish refreshes the tracker and every player's sheet by itself; the XP panel reads on
  // demand, so ask it again.
  if (typeof loadCampaignXp === 'function' && _combatCampId) loadCampaignXp();
}

function setUndoStatus(msg) {
  const el = document.getElementById('undoStatus');
  if (el) {
    el.textContent = msg;
    clearTimeout(el._clear);
    el._clear = setTimeout(function () { el.textContent = ''; }, 6000);
  }
}

// Ctrl+Z / Cmd+Z, but never while the DM is typing into a field (that is the browser's own undo).
document.addEventListener('keydown', function (e) {
  if (!((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z'))) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  e.preventDefault();
  undoLast();
});


// ── "Defeated" for a player character means 0 HP on their sheet ─────────────────────────
// The tracker could bench a player's row while their sheet still said 51/51 — the same control
// disagreeing with itself that slice 7 fixed for HP. Marking a player defeated drops them to 0 on
// the character itself, through the ordinary HP route: same authority, same live publish (their
// sheet hears it), and the same undo record, so a misclick is one tap from being taken back.
//
// Un-marking deliberately does NOT heal: un-ticking a box is not a resurrection, and the DM heals
// normally (or undoes the defeat, which restores the exact HP that was there).

async function defeatPlayerWriteThrough(enId, defeating) {
  if (!defeating || !_combatParticipants) return false;
  const p = _combatParticipants.find(x => x.en_id === enId);
  if (!p || !p.is_player || !p.char_id) return false;   // NPCs and monsters live in the encounter
  if ((p.hp_current || 0) <= 0) return false;           // already down: never write twice
  const res = await postDmHp('/api/dm/character-hp', {character_id: p.char_id, hp_current: 0});
  if (!res) return false;
  p.hp_current = 0;
  if (typeof refreshUndoLabel === 'function') refreshUndoLabel();   // it names the action it just made
  const status = document.getElementById('combatStatus');
  if (status) {
    status.textContent = (p.name || 'Player') + ': defeated → 0 HP on their sheet (↩ Undo puts it back)';
    clearTimeout(status._clear);
    status._clear = setTimeout(function () { status.textContent = ''; }, 8000);
  }
  return true;
}
