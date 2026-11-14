/* dm/combat.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
let _combatEncId = null;
let _combatCampId = null;
let _combatParticipants = [];  // {en_id, name, initiative, hp_current, hp_max, ac, defeated, is_player, ...}
let _combatCreatureCache = [];  // Combined NPC + monster cache for Quick Add search
let _combatRound = 1;
let _combatTurnIdx = 0;        // index into alive participants
let _combatOrder = [];         // en_id order array
let _savedBenchedEnIds = [];   // restored from combat_state on load
let _savedConditions = {};      // conditions per en_id, persisted across reloads

// ── Init: populate encounter dropdown when Combat tab opened ──
const combatTabObserver = new MutationObserver(() => {
  if (document.getElementById('panel-combat').classList.contains('active')) {
    initCombatPanel();
  }
});
document.querySelectorAll('.dm-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setTimeout(() => {
      if (document.getElementById('panel-combat').classList.contains('active')) {
        initCombatPanel();
      }
      if (document.getElementById('panel-items').classList.contains('active')) {
        loadItemsPanel();
      }
    }, 50);
  });
});

async function initCombatPanel() {
  const sel = document.getElementById('combatEncounterSelect');
  if (sel.dataset.loaded) return;
  sel.dataset.loaded = '1';
  try {
    const r = await fetch('/api/dm/encounters');
    const d = await r.json();
    (d.encounters || []).forEach(e => {
      const opt = document.createElement('option');
      opt.value = e.id; opt.textContent = e.name;
      sel.appendChild(opt);
    });
  } catch(e) { /* silent */ }

  // Also populate campaign dropdown
  const campSel = document.getElementById('combatCampaignSelect');
  if (campSel.dataset.loaded) return;
  campSel.dataset.loaded = '1';
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    (d.campaigns || []).forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id; opt.textContent = c.name;
      campSel.appendChild(opt);
    });
  } catch(e) {}
}

// ── Load encounter for combat ──
async function loadCombatEncounter() {
  const sel = document.getElementById('combatEncounterSelect');
  _combatEncId = sel.value ? parseInt(sel.value) : null;
  // Persist selection so page reload auto-restores
  if (_combatEncId) {
    try { localStorage.setItem('combatLastEncounterId', _combatEncId); } catch(e) {}
  }
  if (!_combatEncId && !_combatCampId) {
    document.getElementById('combatTracker').style.display = 'none';
    return;
  }

  document.getElementById('combatTracker').style.display = '';
  if (_combatEncId) {
    document.getElementById('btnRollInit').style.display = '';
    document.getElementById('btnRerollInit').style.display = 'none';
    document.getElementById('combatStatus').textContent = '';
  }

  // Load combat state first — needed for campaign link + player restore
  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}/combat-state`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'load'})
    });
    const d = await r.json();
    _combatRound = d.round || 1;
    _combatTurnIdx = d.turn_index || 0;
    _combatOrder = d.initiative_order || [];
    _savedBenchedEnIds = d.benched_en_ids || [];
    _savedPlayerParticipants = d.player_participants || [];
    _savedCampaignId = d.campaign_id || null;
    _savedConditions = d.participant_conditions || {};
  } catch(e) { _combatRound = 1; _combatTurnIdx = 0; _combatOrder = []; _savedBenchedEnIds = []; _savedPlayerParticipants = []; _savedCampaignId = null; _savedConditions = {}; }

  // Auto-load linked campaign (localStorage first, combat_state fallback)
  let linkedCampId = null;
  try { linkedCampId = localStorage.getItem('combatEncounterCampaign_' + _combatEncId); } catch(e) {}
  if (!linkedCampId && _savedCampaignId) {
    linkedCampId = _savedCampaignId;
    try { localStorage.setItem('combatEncounterCampaign_' + _combatEncId, _savedCampaignId); } catch(e) {}
  }
  if (linkedCampId) {
    const campSel = document.getElementById('combatCampaignSelect');
    const opt = campSel && campSel.querySelector('option[value="' + linkedCampId + '"]');
    if (opt) {
      campSel.value = linkedCampId;
      await loadCombatCampaign();
    }
  }

  // Build combined NPC + monster + character cache for Quick Add search
  _combatCreatureCache = [];
  try {
    const [nr, mr] = await Promise.all([
      fetch('/api/dm/npcs').then(r => r.json()),
      fetch('/api/dm/monsters').then(r => r.json())
    ]);
    // Monsters first (so they show up before NPCs in All filter)
    const _monsterTypes = new Set();
    (mr.monsters || []).forEach(m => {
      if (m.type) _monsterTypes.add(m.type);
      _combatCreatureCache.push({
        id: `m_${m.index || m.name}`, name: m.name,
        _kind: 'monster', _raw: m,
        _type: m.type || '',
        _cr: m.challenge_rating,
        race: m.type || '', hp_current: m.hit_points || 10,
        hp_max: m.hit_points || 10, ac: (m.armor_class && m.armor_class[0] ? m.armor_class[0].value : 10),
        level: m.challenge_rating || '?', is_enemy: 1,
        role: (m.size || '') + ' ' + (m.type || ''),
        xp_reward: m.xp || 0
      });
    });
    // Populate type dropdown from monster types
    const typeSel = document.getElementById('combatCreatureType');
    if (typeSel) {
      // Keep only the "All Types" default
      while (typeSel.options.length > 1) typeSel.remove(1);
      // Get existing CR sort order
      const crSel = document.getElementById('combatCreatureCr');
      const wasCrVisible = crSel && crSel.style.display !== 'none';
      // Add types in alphabetical order
      [..._monsterTypes].sort().forEach(t => {
        const opt = document.createElement('option');
        opt.value = t;
        opt.textContent = t.charAt(0).toUpperCase() + t.slice(1);
        typeSel.appendChild(opt);
      });
      // Show type/CR filters when monsters are loaded
      typeSel.style.display = '';
      if (crSel) crSel.style.display = '';
    }
    (nr.npcs || []).forEach(n => {
      _combatCreatureCache.push({...n, _kind: 'npc'});
    });
    // Characters — player characters from the character manager (inline from server)
    (COMBAT_CHARACTERS || []).forEach(ch => {
      const dexMod = Math.floor((ch.dexterity || 10) - 10) / 2;
      _combatCreatureCache.push({
        id: `c_${ch.id}`, name: ch.name,
        _kind: 'character', _raw: ch,
        _type: '',
        _cr: null,
        race: ch.race || '', subrace: ch.subrace || '',
        class_name: ch.class_name || '', level: ch.level || 1,
        subclass: ch.subclass || '',
        hp_current: ch.hp_current || ch.hp_max || 10,
        hp_max: ch.hp_max || 10,
        ac: ch.ac || 10,
        char_id: ch.id,
        strength: ch.strength, dexterity: ch.dexterity,
        constitution: ch.constitution, intelligence: ch.intelligence,
        wisdom: ch.wisdom, charisma: ch.charisma,
        speed: ch.speed || 30,
        proficiency_bonus: ch.proficiency_bonus || 2,
        is_enemy: 0,
        has_portrait: !!ch.has_portrait,
        dex_mod: Math.floor(dexMod)
      });
    });
  } catch(e) {}
  toggleCombatFilters();
  filterCombatCreatures();

  // Load participants from encounter
  await refreshCombatParticipants();

  // Restore player characters from saved state
  for (const pp of _savedPlayerParticipants) {
    if (!_combatParticipants.find(p => p.en_id === pp.en_id)) {
      _combatParticipants.push({...pp, is_player: true, is_enemy: 0, role: '', roll: pp.initiative});
    }
  }

  // Restore conditions for all participants (not just players)
  if (Object.keys(_savedConditions).length > 0) {
    for (const p of _combatParticipants) {
      if (_savedConditions[p.en_id]) p.conditions = _savedConditions[p.en_id];
    }
    for (const p of _benchedNpcs) {
      if (_savedConditions[p.en_id]) p.conditions = _savedConditions[p.en_id];
    }
  }
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  renderInitiativeTrack();
  renderPlayersPanel();  // hide players already in initiative from waiting area

  // Restore button state: show Reroll if combat already started
  if (_combatParticipants.length > 0 && _combatParticipants.some(p => (p.initiative || 0) > 0)) {
    document.getElementById('btnRollInit').style.display = 'none';
    document.getElementById('btnRerollInit').style.display = '';
    document.getElementById('combatStatus').textContent =
      `🎲 ${_combatParticipants.length} in initiative`;
  }

  // Restore benched state: move benched participants out of the track
  if (_savedBenchedEnIds.length > 0) {
    const benchedSet = new Set(_savedBenchedEnIds);
    _benchedNpcs = _combatParticipants.filter(p => benchedSet.has(p.en_id));
    _combatParticipants = _combatParticipants.filter(p => !benchedSet.has(p.en_id));
    _combatOrder = _combatOrder.filter(id => !benchedSet.has(id));
    renderInitiativeTrack();
    renderPlayersPanel();
    renderBenchedNpcs();
  }
  // Save full state now that players are restored and everything is in place
  saveCombatState();
  startCombatPolling();
}

// ── Load campaign player characters ──
let _campaignPlayers = []; // {char_id, name, race, class_name, level, ac, hp_current, hp_max, dex_mod, initiative}
/* SUMMON_TEMPLATES set by template */
let _benchedNpcs = [];     // NPCs dragged out of combat — can be dragged back in

async function loadCombatCampaign() {
  const campSel = document.getElementById('combatCampaignSelect');
  _combatCampId = campSel.value ? parseInt(campSel.value) : null;

  if (!_combatCampId) {
    document.getElementById('playersWaiting').innerHTML =
      '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">Select a campaign to load players</span>';
    _campaignPlayers = [];
    initRollFeed();                 // no campaign, no feed
    return;
  }

  // Fetch campaign characters
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const camp = (d.campaigns || []).find(c => c.id === _combatCampId);
    _campaignPlayers = [];
    if (camp && camp.characters) {
      for (const ch of camp.characters) {
        _campaignPlayers.push({
          char_id: ch.id,
          name: ch.name,
          race: ch.race || '',
          class_name: ch.class_name || '',
          level: ch.level || 1,
          ac: ch.ac || 10,
          hp_current: ch.hp_current || 1,
          hp_max: ch.hp_max || 1,
          dex_mod: ch.modifiers?.dexterity || 0,
          has_portrait: !!ch.has_portrait,
          initiative: 0,
          defeated: 0
        });
      }
    }
  } catch(e) {}

  renderPlayersPanel();
  initRollFeed();                   // follow the newly selected campaign's rolls
  // Persist encounter→campaign link so encounter auto-loads this campaign
  if (_combatEncId && _combatCampId) {
    try { localStorage.setItem('combatEncounterCampaign_' + _combatEncId, _combatCampId); } catch(e) {}
  }
  // Don't saveCombatState here — player_participants haven't been restored yet.
  // loadCombatEncounter saves the full state at the end after all data is in place.
}

function renderPlayersPanel() {
  const panel = document.getElementById('playersWaiting');
  if (_campaignPlayers.length === 0) {
    panel.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">No players in campaign</span>';
    return;
  }

  // Don't show players already in the initiative track
  const inTrackIds = new Set(_combatParticipants.filter(p => p.is_player).map(p => p.char_id));

  const available = _campaignPlayers.filter(p => !inTrackIds.has(p.char_id));

  if (available.length === 0) {
    panel.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">All players in initiative ✓</span>';
    return;
  }

  panel.innerHTML = available.map(p => {
    return `<div class="initiative-card player-waiting" draggable="true"
      data-char-id="${p.char_id}"
      ondragstart="playerDragStart(event, ${p.char_id})"
      ondragend="playerDragEnd(event)"
      style="cursor:grab;border-style:dashed;flex-direction:column;align-items:stretch;gap:0.4rem">
      <div style="display:flex;align-items:center;gap:0.4rem">
        ${charPortraitTile(p.char_id, p.name, {size: 28, hasPortrait: p.has_portrait})}
        <div class="init-info" style="flex:1;min-width:0">
          <div class="init-name">${dmEsc(p.name)} <span class="badge" style="background:var(--accent2);color:var(--text);font-size:0.72rem;flex-shrink:0">PC</span></div>
          <div class="init-meta">L${p.level} ${dmEsc(p.class_name)} · AC ${p.ac}</div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
        <span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">Init</span>
        <input type="number" class="init-hp-input" value="${p.initiative || ''}" placeholder="?"
          style="width:36px"
          onchange="playerSetInit(${p.char_id}, this.value)" onclick="event.stopPropagation()">
        <div class="init-hp-group" style="min-width:80px">
          <span style="font-size:0.7rem">${p.hp_current}/${p.hp_max}</span>
          <div class="init-hp-bar"><div class="init-hp-bar-fill ok" style="width:${Math.round(p.hp_current/p.hp_max*100)}%"></div></div>
        </div>
        <div style="flex:1"></div>
        <button class="init-btn" onclick="addPlayerToInitiative(${p.char_id})" title="Add to initiative" style="flex-shrink:0;color:var(--accent)">➕</button>
        <button class="init-btn" data-char-action="previewCharSheet" data-char-id="${p.char_id}" data-char-name="${dmEsc(p.name)}" title="View sheet" style="flex-shrink:0">📋</button>
        <button class="init-btn" data-char-action="loadCharacterSummons" data-char-id="${p.char_id}" data-char-name="${dmEsc(p.name)}" title="Load summons into combat" style="flex-shrink:0;color:var(--warn)">🐾</button>
        <button class="init-btn" data-char-action="openSummonModalDM" data-char-id="${p.char_id}" data-char-name="${dmEsc(p.name)}" data-char-level="${p.level || 1}" title="Quick add summon mid-combat" style="flex-shrink:0;color:var(--accent)">⚡</button>
      </div>
    </div>`;
  }).join('');
}

// ── Player drag from panel into initiative track ──
let _playerDragCharId = null;

function playerSetInit(charId, val) {
  const p = _campaignPlayers.find(p => p.char_id === charId);
  if (p) p.initiative = parseInt(val) || 0;
}

function playerDragStart(e, charId) {
  _playerDragCharId = charId;
  e.dataTransfer.effectAllowed = 'move';
  e.target.closest('.initiative-card')?.classList.add('dragging');
}

function playerDragEnd(e) {
  document.querySelectorAll('.initiative-card.dragging').forEach(c => c.classList.remove('dragging'));
  _playerDragCharId = null;
}

// Mobile-friendly button fallback — same logic as the drop handler
function addPlayerToInitiative(charId) {
  const cp = _campaignPlayers.find(p => p.char_id === charId);
  if (!cp) return;
  const init = cp.initiative || 0;
  if (!init && !confirm(`${cp.name} has no initiative set. Add with initiative 0?`)) return;
  _combatParticipants.push({
    en_id: -cp.char_id, char_id: cp.char_id, name: cp.name,
    race: cp.race, class_name: cp.class_name, level: cp.level,
    is_enemy: 0, is_player: true, role: '', ac: cp.ac,
    hp_current: cp.hp_current, hp_max: cp.hp_max,
    defeated: cp.defeated || 0, initiative: init, roll: init, dex_mod: cp.dex_mod || 0
  });
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  renderPlayersPanel();
  saveCombatState();
}

// Make initiative track a drop target for player cards
const initTrackEl = document.getElementById('initiativeTrack');
initTrackEl.addEventListener('dragover', e => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
});
initTrackEl.addEventListener('drop', async e => {
  e.preventDefault();

  // ── Benched NPC dragged back into initiative ──
  if (_benchedDragEnId) {
    const bp = _benchedNpcs.find(p => p.en_id === _benchedDragEnId);
    if (!bp) { _benchedDragEnId = null; return; }
    _benchedNpcs = _benchedNpcs.filter(p => p.en_id !== _benchedDragEnId);
    _combatParticipants.push(bp);
    _combatParticipants.sort((a, b) => {
      if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
      return (b.initiative||0) - (a.initiative||0);
    });
    _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
    renderInitiativeTrack();
    renderBenchedNpcs();
    saveCombatState();
    _benchedDragEnId = null;
    return;
  }

  // ── Player dragged from panel into initiative ──
  if (!_playerDragCharId) return;
  const cp = _campaignPlayers.find(p => p.char_id === _playerDragCharId);
  if (!cp) return;

  const init = cp.initiative || 0;
  if (!init && !confirm(`${cp.name} has no initiative set. Add with initiative 0?`)) return;

  // Add to track participants
  _combatParticipants.push({
    en_id: -cp.char_id,  // negative to avoid collision
    char_id: cp.char_id,
    name: cp.name,
    race: cp.race,
    class_name: cp.class_name,
    level: cp.level,
    is_enemy: 0,
    is_player: true,
    role: '',
    ac: cp.ac,
    hp_current: cp.hp_current,
    hp_max: cp.hp_max,
    defeated: cp.defeated || 0,
    initiative: init,
    roll: init,
    dex_mod: cp.dex_mod || 0
  });

  // Re-sort
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });

  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  renderPlayersPanel();
  saveCombatState();
  _playerDragCharId = null;
});

// ── Roll initiative ──
async function rollInitiative() {
  if (!_combatEncId) return alert('Select an encounter first');

  // Preserve benched en_ids so they stay benched after reroll
  const benchedIds = new Set(_benchedNpcs.map(p => p.en_id));

  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}/roll-initiative`, {method:'POST'});
    const d = await r.json();
    if (!d.ok) return alert(d.error || 'Failed to roll');
    // Keep player characters that were already dragged into the track
    const inTrack = _combatParticipants.filter(p => p.is_player);
    // Preserve benched en_ids so they stay benched after reroll
    _combatParticipants = [];
    for (const npc of d.participants) {
      _combatParticipants.push({...npc, is_player: false, char_id: null});
    }
    // Re-add players that were already in the track
    for (const p of inTrack) {
      _combatParticipants.push(p);
    }
  } catch(e) { alert('Failed to roll NPC initiative'); return; }

  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });

  // Restore benched state: move benched NPCs out of combat participants
  if (benchedIds.size > 0) {
    _benchedNpcs = _combatParticipants.filter(p => benchedIds.has(p.en_id));
    _combatParticipants = _combatParticipants.filter(p => !benchedIds.has(p.en_id));
  }

  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  _combatRound = 1;
  _combatTurnIdx = 0;
  renderInitiativeTrack();
  saveCombatState();
  document.getElementById('btnRollInit').style.display = 'none';
  document.getElementById('btnRerollInit').style.display = '';
  document.getElementById('combatStatus').textContent =
    `🎲 ${_combatParticipants.length} in initiative`;
}

// ── Edit initiative value directly on a card ──
function editInitiative(input) {
  const enId = parseInt(input.dataset.enId);
  const newInit = parseInt(input.value) || 0;
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.initiative = newInit;
  // Re-sort and re-render
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  saveCombatState();
  // Persist to backend
  if (_combatEncId) {
    fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({participants: [{id: enId, initiative: newInit}]})
    }).catch(() => {});
  }
}

// ── Refresh participants from DB (used after HP changes, adding NPCs, etc.) ──
async function refreshCombatParticipants() {
  if (!_combatEncId) return;
  // Preserve player characters already in the track
  const players = _combatParticipants.filter(p => p.is_player);

  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}`);
    const d = await r.json();
    const npcs = (d.encounter.participants || []).map(p => ({
      en_id: p.id, npc_id: p.npc_id, name: p.npc_name,
      race: p.race || '', class_name: p.class_name || '',
      level: p.level || 1, is_enemy: p.is_enemy, role: p.role || '',
      ac: p.ac || p.npc_ac || 10, hp_current: p.hp_current,
      hp_max: p.hp_max || p.npc_hp_max || 10,
      defeated: p.defeated || 0, initiative: p.initiative || 0,
      is_player: false, char_id: p.char_id || null,
      npc_has_portrait: !!p.npc_has_portrait,
      creature_data: p.creature_data || null
    }));
    _combatParticipants = [...players, ...npcs];
  } catch(e) {}

  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  if (_combatOrder.length === 0) {
    _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  }
  renderInitiativeTrack();
}

// ── Render initiative track ──
function renderInitiativeTrack() {
  const track = document.getElementById('initiativeTrack');
  const all = [..._combatParticipants];  // include defeated for band grouping
  const alive = all.filter(p => !p.defeated);

  // Clamp turn index
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = alive.length - 1;
  if (_combatTurnIdx < 0) _combatTurnIdx = 0;

  document.getElementById('combatRoundLabel').textContent = `Round ${_combatRound}`;
  document.getElementById('combatTurnLabel').textContent =
    alive.length > 0 ? `Turn ${_combatTurnIdx + 1}/${alive.length}` : 'No active combatants';

  if (all.length === 0) {
    track.innerHTML = '<div style="color:var(--text-muted);padding:1rem;text-align:center">No participants. Roll initiative or add NPCs.</div>';
    return;
  }

  // Build in initiative_order sequence (if set), then by initiative descending
  const ordered = [];
  const used = new Set();
  for (const enId of _combatOrder) {
    const p = all.find(p => p.en_id === enId);
    if (p && !used.has(p.en_id)) { ordered.push(p); used.add(p.en_id); }
  }
  for (const p of all) {
    if (!used.has(p.en_id)) ordered.push(p);
  }

  // Five initiative bands
  const bands = [
    { label: '20+', min: 20, max: 99, members: [] },
    { label: '19–15', min: 15, max: 19, members: [] },
    { label: '14–10', min: 10, max: 14, members: [] },
    { label: '9–5', min: 5, max: 9, members: [] },
    { label: '4–0', min: -99, max: 4, members: [] },
  ];
  for (const p of ordered) {
    const init = p.initiative || 0;
    for (const band of bands) {
      if (init >= band.min && init <= band.max) {
        band.members.push(p);
        break;
      }
    }
  }

  const currentEnId = alive.length > 0 && _combatTurnIdx < alive.length
    ? alive[_combatTurnIdx].en_id : null;

  function cardHTML(p, i) {
    const isDefeated = p.defeated || p.hp_current <= 0;
    const isCurrent = p.en_id === currentEnId;
    const hpPct = p.hp_max > 0 ? Math.max(0, Math.round(p.hp_current / p.hp_max * 100)) : 0;
    const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'warn' : hpPct < 50 ? 'warn' : 'ok';
    const badge = p.is_player ? '<span class="badge" style="background:var(--accent2);color:var(--text);font-size:0.72rem">PC</span>'
      : p.is_summon ? '<span class="badge" style="background:rgba(255,165,0,0.15);color:#ffa500;font-size:0.72rem">🐾</span>'
      : p.is_enemy ? '<span class="badge badge-accent" style="font-size:0.72rem">ENEMY</span>'
      : '<span class="badge badge-muted" style="font-size:0.72rem">ALLY</span>';
    const role = p.role ? ` · ${p.role}` : '';
    const cls = p.class_name ? `L${p.level} ${p.class_name}${role}` : `L${p.level}${role}`;
    const rollInfo = p.roll != null ? ` <span style="font-size:0.68rem;opacity:0.6">(${p.roll}${p.dex_mod >= 0 ? '+' : ''}${p.dex_mod || 0})</span>` : '';

    return `<div class="initiative-card${isCurrent ? ' current-turn' : ''}${isDefeated ? ' defeated-card' : ''}"
      draggable="true"
      data-en-id="${p.en_id}"
      ondragstart="initDragStart(event, ${p.en_id})"
      ondragend="initDragEnd(event)"
      ondragover="initDragOver(event)"
      ondrop="initDrop(event, ${p.en_id})">
      <div class="init-turn-marker">${isCurrent ? '▶' : ''}</div>
      <div class="init-roll" title="d20${p.dex_mod >= 0 ? '+' + p.dex_mod : p.dex_mod} (roll: ${p.roll || '—'}${p.dex_mod >= 0 ? '+' : ''}${p.dex_mod || 0})">
        <input type="number" class="init-roll-input" value="${p.initiative}"
          data-en-id="${p.en_id}"
          onchange="editInitiative(this)" onfocus="this.select()"
          style="width:2.5rem;padding:0.1rem 0.2rem;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);font-size:0.75rem;text-align:center;font-family:monospace">
      </div>
      <div class="init-info" style="display:flex;align-items:center;gap:0.4rem">
        ${p.npc_id === -1
          ? charPortraitTile(null, p.name || '?', {size: 26, src: '/api/ref-image/creature/' + encodeURIComponent(p.name || '?') + '?size=52'})
          : (p.is_player || p.char_id
            ? charPortraitTile(p.char_id || p.en_id, p.name, {size: 26, hasPortrait: p.has_portrait})
            : charPortraitTile(p.npc_id > 0 ? p.npc_id : null, p.name, {size: 26, kind: 'npc', hasPortrait: p.npc_has_portrait}))}
        <div class="init-name-wrap" style="min-width:0;flex:1 1 auto">
        <div class="init-name">${dmEsc(p.name || '')} ${badge} <button class="init-btn" ${p.is_player || p.char_id
          ? `data-char-action="previewCharSheet" data-char-id="${Number(p.char_id || p.en_id)}" data-char-name="${dmEsc(p.name || '')}"`
          : `data-en-id="${Number(p.en_id)}"`} title="View details" style="font-size:0.65rem;padding:0 0.2rem">📋</button></div>
        <div class="init-meta">${cls} · AC ${p.ac}</div>
        </div>
      </div>
      <button class="init-btn" onclick="event.stopPropagation();combatOpenCondPicker(${p.en_id})" title="Add condition" style="flex-shrink:0;font-size:0.68rem;line-height:1.2;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;min-width:16px;padding:0 0.25rem">+</button>
      <button class="init-btn" onclick="toggleDefeatedCombat(${p.en_id})" title="Toggle defeated" style="flex-shrink:0">${isDefeated ? '⬆' : '💀'}</button>
      <div class="init-hp-group">
        <span style="font-size:0.85rem;font-weight:600;min-width:2.5em;text-align:right">${p.hp_current}</span>
        <span style="font-size:0.7rem;color:var(--text-muted)">/${p.hp_max}</span>
        <div class="init-hp-bar"><div class="init-hp-bar-fill ${hpClass}" style="width:${hpPct}%"></div></div>
      </div>
      <div class="init-cond-badges" style="display:flex;flex-wrap:wrap;gap:0.15rem;margin-top:0.15rem;align-items:center">
        ${(() => {
          const conds = p.conditions || [];
          if (_combatCondExpanded.has(p.en_id)) {
            return conds.map(c => {
              const style = CONDITION_COLORS[c.name] || '';
              const bg = style ? style.split(';')[0] : 'var(--accent)';
              const fg = style ? (style.split(';')[1]||'color:#fff') : 'color:#fff';
              return `<span class="cond-badge" style="background:${bg};${fg}" data-cond-en="${Number(p.en_id)}" data-cond-name="${dmEsc(c.name)}" data-cond-desc="${dmEsc(c.description||'')}">${dmEsc(c.name)}<span class="dismiss" data-cond-dismiss-en="${Number(p.en_id)}" data-cond-name="${dmEsc(c.name)}">✕</span></span>`;
            }).join('');
          }
          return conds.length ? `<span class="cond-badge" style="background:var(--bg);color:var(--text-muted);border:1px dashed var(--border);cursor:pointer" data-cond-expand-en="${Number(p.en_id)}">${conds.length} condition${conds.length!==1?'s':''}</span>` : '';
        })()}
      </div>
      <div class="init-actions">
        <input type="number" class="init-hp-input" id="dmg-input-${p.en_id}" value="" placeholder="dmg"
          style="width:44px"
          onkeydown="if(event.key==='Enter'){event.preventDefault();applyDmgInput(${p.en_id})}"
          onclick="event.stopPropagation()" title="Enter = damage, + = heal">
        <button class="init-btn heal-btn" onclick="applyDmgButton(${p.en_id}, 1)" title="Heal">+</button>
        <button class="init-btn dmg-btn" onclick="applyDmgButton(${p.en_id}, -1)" title="Damage">−</button>
        <button class="init-btn" onclick="combatResetHP(${p.en_id})" title="Reset to full HP">↺</button>
        <button class="init-btn" onclick="benchCombatant(${p.en_id})" title="Bench (remove from combat)" style="color:var(--text-muted)">⏸</button>
      </div>
    </div>`;
  }

  track.innerHTML = bands.map(band => `
    <div class="init-band">
      <div class="init-band-header" onclick="this.parentElement.classList.toggle('collapsed')">
        <span class="band-arrow">▼</span>
        <span class="band-range">${band.label}</span>
        <span class="band-count">${band.members.length}</span>
      </div>
      <div class="init-band-body">
        ${band.members.map((p, i) => cardHTML(p, i)).join('')}
      </div>
    </div>
  `).join('');
}

// ── Turn navigation ──
function nextTurn() {
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (alive.length === 0) return;
  _combatTurnIdx++;
  if (_combatTurnIdx >= alive.length) {
    _combatTurnIdx = 0;
    _combatRound++;
  }
  renderInitiativeTrack();
  saveCombatState();
}

function prevTurn() {
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (alive.length === 0) return;
  _combatTurnIdx--;
  if (_combatTurnIdx < 0) {
    if (_combatRound > 1) {
      _combatRound--;
      _combatTurnIdx = alive.length - 1;
    } else {
      _combatTurnIdx = 0;
    }
  }
  renderInitiativeTrack();
  saveCombatState();
}

// ── HP management ──
async function adjustHp(enId, newHp) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  const hp = Math.max(0, parseInt(newHp) || 0);
  p.hp_current = hp;
  if (hp <= 0) p.defeated = 1;

  await saveCombatHp(p, hp);
  renderInitiativeTrack();
  saveCombatState();
}

async function applyDmgInput(enId) {
  const input = document.getElementById('dmg-input-' + enId);
  if (!input) return;
  const raw = parseInt(input.value);
  if (isNaN(raw) || raw <= 0) { input.value = ''; return; }
  await _applyHpDelta(enId, -raw);
  input.value = '';
}

async function applyDmgButton(enId, sign) {
  const input = document.getElementById('dmg-input-' + enId);
  const raw = input ? (parseInt(input.value) || 1) : 1;
  const delta = sign === -1 ? -raw : raw;
  await _applyHpDelta(enId, delta);
  if (input) input.value = '';
}

async function _applyHpDelta(enId, delta) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p || p.defeated) return;
  const hp = Math.max(0, p.hp_current + delta);
  p.hp_current = hp;
  if (hp <= 0) p.defeated = 1;

  await saveCombatHp(p, hp);
  renderInitiativeTrack();
  saveCombatState();
}

async function combatResetHP(enId) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.hp_current = p.hp_max;
  p.defeated = 0;

  await saveCombatHp(p, p.hp_max);
  // Rebuild order (might have been defeated)
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  saveCombatState();
}

async function saveCombatHp(p, hp) {
  if (p.is_player && p.char_id) {
    // To the character row, through the DM route that authorises on the CAMPAIGN: the player's
    // sheet belongs to the player, so an owner-scoped write would be refused for every player at
    // a real table. (It used to POST /api/update, which is not a route — every damage the DM
    // applied to a player was a silent 404 that this catch swallowed.)
    try {
      await postDmHp('/api/dm/character-hp', {character_id: p.char_id, hp_current: hp});
    } catch(e) {}
  } else if (p.is_summon && p.owner_char_id && p.summon_idx !== undefined) {
    // Save summon HP back to character sheet. Same authority question as a player row: a summon
    // on a character the DM does not own needs the DM route, not the owner's.
    try {
      await postDmHp('/api/dm/summon-hp',
                     {character_id: p.owner_char_id, summon_idx: p.summon_idx, hp_current: hp});
    } catch(e) {}
  } else if (p.en_id > 0 && _combatEncId) {
    // Save to encounter NPC table
    try {
      await fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({single:{id:p.en_id, hp_current:hp, defeated: p.defeated?1:0}})
      });
    } catch(e) {}
  }
}

/** POST and INSIST on an answer. A write that fails silently is how the DM spent months healing
 *  and wounding players in the tracker with nothing happening; a failure now says so. */
async function postDmHp(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  if (!r.ok) {
    let detail = 'HTTP ' + r.status;
    try { const d = await r.json(); if (d && (d.error || d.detail)) detail = d.error || d.detail; } catch(e) {}
    reportCombatError('HP not saved: ' + detail);
    return null;
  }
  return r.json().catch(() => ({}));
}

/** Say it where the DM is looking, and keep it in the console for whoever reads logs next. */
function reportCombatError(message) {
  try { console.warn('[combat] ' + message); } catch(e) {}
  const bar = document.querySelector('.encounter-toolbar') || document.getElementById('initiativeTrack');
  if (!bar) return;
  let el = document.getElementById('combatSyncError');
  if (!el) {
    el = document.createElement('span');
    el.id = 'combatSyncError';
    el.style.cssText = 'font-size:0.7rem;color:var(--danger,#e5534b);align-self:center';
    bar.appendChild(el);
  }
  el.textContent = '⚠ ' + message;
  clearTimeout(el._clear);
  el._clear = setTimeout(function () { el.textContent = ''; }, 6000);
}

// ── Defeated toggle ──
async function toggleDefeatedCombat(enId) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.defeated = p.defeated ? 0 : 1;

  if (p.is_player && p.char_id) {
    // This branch used to do nothing at all — "just track locally for players" — which left the
    // tracker benching a row while the player's own sheet still read 51/51: one control
    // contradicting itself. A benched player is unconscious, so the sheet goes to 0 HP through the
    // ordinary HP route: same authority, it publishes (their sheet hears it live), and it records
    // the same one-tap undo. Un-benching does not heal — see defeatPlayerWriteThrough.
    await defeatPlayerWriteThrough(enId, !!p.defeated);
  } else if (p.en_id > 0 && _combatEncId) {
    try {
      await fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({single:{id:p.en_id, defeated: p.defeated}})
      });
    } catch(e) {}
  }

  // Rebuild order excluding defeated
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = Math.max(0, alive.length - 1);
  renderInitiativeTrack();
  saveCombatState();
}

