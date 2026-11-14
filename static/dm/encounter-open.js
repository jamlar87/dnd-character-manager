/* dm/encounter-open.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
function toggleShareEncounter(encId, currentlyShared) {
  const newState = !currentlyShared;
  api(`/api/dm/encounter/${encId}/share`, { body: {shared: newState} })
    .then(d => { if (d.ok) location.reload(); })
    .catch(e => alert('Could not update sharing: ' + e.message));
}

function copyEncounter(encId) {
  fetch(`/api/dm/encounter/${encId}/copy`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: '{}'
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
    else alert(d.detail || 'Failed to copy');
  }).catch(() => alert('Failed to copy encounter'));
}

async function openEncounter(id) {
  openModal('encounterModal');
  document.getElementById('encounterDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';
  try {
    const r = await fetch(`/api/dm/encounter/${id}`);
    const d = await r.json();
    const enc = d.encounter;

    // Fetch available NPCs and monsters for adding
    const [nr, mr] = await Promise.all([
      fetch('/api/dm/npcs').then(r => r.json()),
      fetch('/api/dm/monsters').then(r => r.json())
    ]);
    const allNpcs = (nr.npcs || []).map(n => ({...n, _kind: 'npc'}));
    const allMonsters = (mr.monsters || []).map(m => ({
      id: `m_${m.index || m.name}`, name: m.name,
      race: m.type || '', class_name: '', level: (m.challenge_rating || '?'),
      hp_current: m.hit_points || 10, hp_max: m.hit_points || 10,
      ac: (m.armor_class && m.armor_class[0] ? m.armor_class[0].value : 10),
      is_enemy: 1, role: (m.size || '') + ' ' + (m.type || ''),
      xp_reward: m.xp || 0, _kind: 'monster',
      _raw: m  // preserve full monster data
    }));
    const allCreatures = [...allMonsters, ...allNpcs];  // monsters first so info buttons are visible
    window._creatureCache = allCreatures;  // store for onclick lookups

    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem">
      <h2 style="margin:0">${enc.name}</h2>
      <button class="btn btn-outline btn-sm" onclick="editEncounterMeta(${id})">✏️</button>
    </div>`;
    if (enc.description) html += `<p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.5rem">${enc.description}</p>`;
    if (enc.notes) html += `<details style="margin-bottom:0.5rem"><summary style="cursor:pointer;font-size:0.85rem;color:var(--accent);user-select:none">📋 Tactics & Notes</summary><p style="font-size:0.85rem;color:var(--text-muted);white-space:pre-wrap;margin:0.3rem 0 0 0.5rem;border-left:2px solid var(--accent);padding-left:0.5rem">${enc.notes}</p></details>`;

    // Widget bar: counters and timers the DM ticks mid-fight (loaded from the encounter row).
    html += `<div id="widgetBar" class="widget-bar">Loading widgets…</div>`;

    // Encounter builder: searchable palette + tracking
    html += `<div class="encounter-builder">
      <div>
        <h4 style="margin-bottom:0.5rem">📋 Available Creatures</h4>
        <div style="margin-bottom:0.5rem;display:flex;gap:0.4rem;flex-wrap:wrap">
          <input type="text" id="creatureSearch" placeholder="Search creatures..."
            style="flex:1;min-width:150px;padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem"
            oninput="filterCreaturePalette()">
          <select id="creatureKindFilter" onchange="filterCreaturePalette()"
            style="padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
            <option value="all">All</option>
            <option value="npc">NPCs</option>
            <option value="monster">Monsters</option>
          </select>
          <span id="paletteSrcFilter"></span>
          <span style="font-size:0.75rem;color:var(--text-muted);align-self:center" id="creatureCount">${allCreatures.length}</span>
        </div>
        <div class="monster-palette" id="creaturePalette" style="max-height:400px;overflow-y:auto">`;
    allCreatures.forEach((c, ci) => {
      const kindBadge = c._kind === 'monster'
        ? '<span class="badge badge-accent" style="font-size:0.68rem">MON</span>'
        : c.id < 0 ? '<span class="badge badge-muted" style="font-size:0.68rem">📖</span>' : '';
      const sourceBadge = c._kind === 'monster' && c._raw && c._raw.source
        ? `<span class="src-badge" data-src="${dmEsc(c._raw.source)}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;cursor:pointer" title="Click to open ${c._raw.source}">📚</span> `
        : '';
      const tagBadge = c._kind === 'monster' && c._raw && c._raw.tags && c._raw.tags.length
        ? c._raw.tags.map(t => `<span class="badge badge-muted" style="font-size:0.65rem;opacity:0.8">${t}</span>`).join(' ')
        : '';
      const hpDisplay = c._kind === 'monster'
        ? `HP ${c.hp_current}`
        : `HP ${c.hp_current}/${c.hp_max}`;
      const crDisplay = c._kind === 'monster' && c.level ? `CR ${c.level} · ` : '';
      const detailDisplay = c._kind === 'monster'
        ? `${crDisplay}AC ${c.ac} · ${c.race || '?'}`
        : `${c.race}${c.class_name ? ' L' + c.level + ' ' + c.class_name : ''} · AC ${c.ac}`;
      html += `<div class="creature-row" data-kind="${c._kind}" data-name="${c.name.toLowerCase()}" data-source="${(c._raw && c._raw.source) || ''}"
        style="display:flex;align-items:center;gap:0.3rem;padding:0.35rem 0.5rem;background:var(--bg);border-radius:4px;margin-bottom:0.25rem">
        ${c._kind === 'npc' ? charPortraitTile(c.id, c.name, {size: 24, kind: 'npc', hasPortrait: c.has_portrait}) : ''}
        ${c._kind !== 'npc' ? charPortraitTile(null, c.name, {size: 24, src: '/api/ref-image/creature/' + encodeURIComponent(c.name) + '?size=48'}) : ''}
        ${c._kind === 'monster' && c._raw && c._raw.index ? `<button class="btn btn-outline btn-sm" data-monster-action="showMonster" data-monster-index="${dmEsc(c._raw.index)}" title="Monster details" style="font-size:0.65rem;padding:0.15rem 0.35rem;flex-shrink:0">ℹ️</button>` : (c._kind === 'npc' ? `<button class="btn btn-outline btn-sm" data-npc-action="showNpcInfo" data-npc-id="${Number(c.id)}" data-npc-name="${dmEsc(c.name || '')}" title="NPC details" style="font-size:0.65rem;padding:0.15rem 0.35rem;flex-shrink:0">ℹ️</button>` : '')}
        <span style="font-size:0.8rem;flex:1 1 auto;min-width:0;overflow-wrap:break-word;word-break:break-word">${kindBadge}${sourceBadge}${tagBadge}<strong>${c.name}</strong> <span style="color:var(--text-muted)">${detailDisplay} · ${hpDisplay}</span></span>
        <button class="btn btn-primary btn-sm" style="flex-shrink:0" data-add-creature-enc="${Number(id)}" data-add-creature-idx="${Number(ci)}">+ Add</button>
      </div>`;
    });
    html += `</div></div>
      <div>
        <h4 style="margin-bottom:0.5rem">⚡ Combat Tracker</h4>
        <div class="encounter-tracking">`;

    if (enc.participants && enc.participants.length) {
      const alive = enc.participants.filter(p => !p.defeated).length;
      html += `<div class="encounter-toolbar">
        <span style="font-size:0.8rem;color:var(--text-muted)">${alive}/${enc.participants.length} active · ${d.xp_total || 0} XP</span>
        <button class="btn btn-outline btn-sm" onclick="updateInitiatives(${id})">💾 Save All</button>
      </div>`;
      enc.participants.sort((a, b) => {
        // Sort: alive first (by initiative desc), then defeated
        if ((a.defeated || 0) !== (b.defeated || 0)) return (a.defeated ? 1 : -1);
        return (b.initiative || 0) - (a.initiative || 0);
      });
      enc.participants.forEach((p, idx) => {
        const hpPct = p.hp_max > 0 ? Math.round(p.hp_current / p.hp_max * 100) : 0;
        const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'danger' : hpPct < 50 ? 'warn' : 'ok';
        const isDefeated = p.defeated || hpPct <= 0;
        // Parse NPC spell slot data
        let npcSlots = {};
        try { npcSlots = typeof p.npc_spell_slots === 'string' ? JSON.parse(p.npc_spell_slots || '{}') : (p.npc_spell_slots || {}); } catch(e) {}
        // Parse encounter-level spell_slots_used for this participant
        let usedSlots = {};
        try { usedSlots = typeof p.spell_slots_used === 'string' ? JSON.parse(p.spell_slots_used || '{}') : (p.spell_slots_used || {}); } catch(e) {}
        // Detect if NPC has spells (has class_name that's a caster, or has spell_slot_data)
        const hasSpells = p.class_name && ['Bard','Cleric','Druid','Paladin','Ranger','Sorcerer','Warlock','Wizard','Artificer'].includes(p.class_name);
        const isCaster = hasSpells && Object.keys(npcSlots).length > 0;

        html += `<div class="participant-row${isDefeated ? ' defeated' : ''}" id="participant-row-${p.id}" data-name="${dmEsc(p.npc_name || p.name || '')}" data-participant-index="${Number(idx)}">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
              ${p.npc_id === -1
                ? charPortraitTile(null, p.npc_name || p.name || '?', {size: 24, src: '/api/ref-image/creature/' + encodeURIComponent(p.npc_name || p.name || '?') + '?size=48'})
                : (p.is_player
                  ? charPortraitTile(p.char_id, p.npc_name || p.name || '?', {size: 24, hasPortrait: p.has_portrait})
                  : charPortraitTile(p.npc_id > 0 ? p.npc_id : null, p.npc_name || p.name || '?', {size: 24, kind: 'npc', hasPortrait: p.npc_has_portrait}))}
              <span class="badge ${p.is_enemy ? 'badge-accent' : 'badge-muted'}" style="font-size:0.65rem">${p.is_enemy ? 'ENEMY' : 'ALLY'}</span>
              <strong class="p-name" style="font-size:0.85rem">${p.npc_name || '?'}</strong>
              <span style="font-size:0.75rem;color:var(--text-muted)">L${p.level} ${p.role || ''}</span>
              <span style="font-size:0.75rem;color:var(--text-muted)">AC ${p.ac || p.npc_ac || '?'}</span>
              ${isCaster ? '<span style="font-size:0.7rem;color:var(--accent)">🔮 Caster</span>' : ''}
            </div>
            <div style="display:flex;align-items:center;gap:0.4rem;margin-top:0.15rem;flex-wrap:wrap">
              <input type="number" class="init-input" value="${p.initiative || 0}" data-enid="${p.id}" placeholder="Init" onclick="event.stopPropagation()" onchange="markDirty()">
              <span style="font-size:0.75rem">HP</span>
              <input type="number" class="hp-input" value="${p.hp_current}" data-enid="${p.id}" placeholder="HP" onclick="event.stopPropagation()" onchange="markDirty()" style="width:50px">
              <span style="font-size:0.75rem">/${p.hp_max}</span>
              <div class="hp-bar-mini" style="width:80px"><div class="hp-bar-mini-fill ${hpClass}" style="width:${Math.max(0, hpPct)}%"></div></div>
              <button class="defeat-btn ${isDefeated ? 'dead' : 'alive'}" onclick="event.stopPropagation();toggleDefeated(${id}, ${p.id}, ${idx})">${isDefeated ? '✓ Alive' : '💀 Defeat'}</button>
              <button class="btn btn-outline btn-sm p-locate" onclick="event.stopPropagation();focusTrackerRow(${idx})" title="Jump to this combatant (↑/↓ to move, Enter to open)">🎯</button>
              <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();removeNpcFromEncounter(${id}, ${p.id})" title="Remove">✕</button>
              ${p._monster_index ? `<button class="btn btn-outline btn-sm" data-monster-action="showMonster" data-monster-index="${dmEsc(p._monster_index)}" title="Monster details" style="font-size:0.7rem;padding:0.15rem 0.4rem">ℹ️</button>` : (p.npc_id && p.npc_id > 0 ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showNpcEditor(${p.npc_id})" title="NPC details" style="font-size:0.7rem;padding:0.15rem 0.4rem">ℹ️</button>` : '')}
            </div>
          </div>
        </div>
        <div class="p-stats" id="p-stats-${idx}">
          <div class="ps-grid">
            <div class="ps-stat"><div class="ps-lbl">Initiative</div><div class="ps-val">${p.initiative || '-'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">AC</div><div class="ps-val">${p.ac || p.npc_ac || '?'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">HP</div><div class="ps-val">${p.hp_current}/${p.hp_max}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Race</div><div class="ps-val">${p.race || '?'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Class</div><div class="ps-val">${p.class_name || '—'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Level</div><div class="ps-val">${p.level || '?'}</div></div>
          </div>`;


        // Spell slot trackers (for caster NPCs)
        if (isCaster) {
          html += `<div style="margin-top:0.3rem"><strong style="font-size:0.75rem">🔮 Spell Slots</strong>`;
          // Determine max spell level from slots data
          const maxLevel = Math.max(...Object.keys(npcSlots).map(Number).filter(k => k > 0 && k < 10), 0);
          for (let lvl = 1; lvl <= maxLevel; lvl++) {
            const max = npcSlots[lvl] || 0;
            const used = usedSlots[lvl] || 0;
            if (max > 0) {
              html += `<div class="spell-slot-row">
                <span style="font-size:0.7rem;width:24px">L${lvl}</span>
                <div class="spell-slot-track">`;
              for (let s = 0; s < max; s++) {
                html += `<div class="spell-slot-dot ${s < used ? 'used' : ''}" onclick="event.stopPropagation();toggleSlot(${id}, ${p.id}, ${idx}, ${lvl}, ${s}, ${max})" title="${s < used ? 'Used — click to restore' : 'Available — click to expend'}"></div>`;
              }
              html += `</div>
                <span style="font-size:0.65rem;color:var(--text-muted)">${used}/${max}</span>
              </div>`;
            }
          }
          html += `</div>`;
        }

        html += `</div>`;
      });
    } else {
      html += `<div class="empty-state" style="padding:1rem"><p>No participants. Add NPCs from the palette.</p></div>`;
    }

    html += `</div></div></div>
      <div style="text-align:right;margin-top:0.75rem">
        <button class="btn btn-danger btn-sm" onclick="deleteEncounter(${id});closeModal('encounterModal')">✕ Delete Encounter</button>
      </div>`;
    document.getElementById('encounterDetail').innerHTML = html;
    // Widgets live on the encounter row; the bar owns its own timer loop.
    if (window.WidgetBar) window.WidgetBar.load('encounter', id);
    // Encounter-builder creature palette gets the same manual filter
    if (window.SourceFilter) {
      const mount = document.getElementById('paletteSrcFilter');
      if (mount) SourceFilter.init(mount, {key: 'dm-palette', onChange: filterCreaturePalette});
      filterCreaturePalette();
    }
  } catch(e) {
    document.getElementById('encounterDetail').innerHTML = '<p style="color:var(--danger)">Failed to load encounter.</p>';
  }
}

function editEncounterMeta(id) {
  const name = prompt('Encounter name:');
  if (!name) return;
  const location = prompt('Location:', '') || '';
  const difficulty = prompt('Difficulty (easy/medium/hard/deadly):', 'medium') || 'medium';
  fetch(`/api/dm/encounter/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, location, difficulty, status: 'active'})
  }).then(r => r.json()).then(() => openEncounter(id));
}

function addNpcToEncounter(encId, npcId) {
  fetch(`/api/dm/encounter/${encId}/add-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(() => openEncounter(encId));
}

// ── Add creature (monster or manual NPC) to encounter ──
function addCreatureToEncounter(encId, creatureIndex) {
  const c = window._creatureCache && window._creatureCache[creatureIndex];
  if (!c) return;
  if (c._kind === 'npc' && c.id > 0) {
    // DB NPC — use add-npc
    return addNpcToEncounter(encId, c.id);
  }
  // Monster or manual NPC — use add-creature
  fetch(`/api/dm/encounter/${encId}/add-creature`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: c.name, race: c.race, class_name: c.class_name,
      level: typeof c.level === 'number' ? c.level : 1,
      hp: c.hp_current, hp_max: c.hp_max, ac: c.ac,
      is_enemy: c.is_enemy || 0, role: c.role || '',
      xp_reward: c.xp_reward || 0,
      _monster_index: c.id || c._raw?.index || '',
      // Full stat block fields (from manual NPCs, SRD monsters)
      ability_scores: c.ability_scores || null,
      spellcasting: c.spellcasting || null,
      features: c.features || c.special_abilities || [],
      actions: c.actions || [],
      skills: c.skills || {},
      saving_throws: c.saving_throws || {},
      speed: c.speed || '',
      alignment: c.alignment || '',
      description: c.description || '',
      equipment: c.equipment || [],
      senses: c.senses || '',
      languages: c.languages || [],
      damage_resistances: c.damage_resistances || [],
      damage_immunities: c.damage_immunities || [],
      condition_immunities: c.condition_immunities || [],
      challenge_rating: c.challenge_rating || null,
    })
  }).then(r => r.json()).then(() => openEncounter(encId));
}

// ── Filter creature palette ──
function filterCreaturePalette() {
  const q = (document.getElementById('creatureSearch')?.value || '').toLowerCase();
  const kind = document.getElementById('creatureKindFilter')?.value || 'all';
  let count = 0;
  document.querySelectorAll('.creature-row').forEach(row => {
    const name = row.dataset.name || '';
    const rowKind = row.dataset.kind || '';
    const match = (!q || name.includes(q)) && (kind === 'all' || rowKind === kind)
      && (!_coreOnlyDM || rowKind === 'npc' || isCoreSourceDM(row.dataset.source || ''))
      && _srcOk(row.dataset.source || '', 'dm-palette');
    row.style.display = match ? '' : 'none';
    if (match) count++;
  });
  const countEl = document.getElementById('creatureCount');
  if (countEl) countEl.textContent = count;
}

function removeNpcFromEncounter(encId, enId) {
  fetch(`/api/dm/encounter/${encId}/remove-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({en_id: enId})
  }).then(r => r.json()).then(() => openEncounter(encId));
}

async function updateInitiatives(encId) {
  const btn = document.querySelector('.encounter-toolbar .btn-outline');
  if (btn) { btn.textContent = '⏳ Saving...'; btn.disabled = true; }

  // Marking a player defeated here (the bulk save) has to do the same thing the row toggle does,
  // or the two ways of benching someone disagree about their HP.
  document.querySelectorAll('.participant-row.defeated').forEach(function (row) {
    const box = row.querySelector('input[data-enid]');
    if (box) defeatPlayerWriteThrough(parseInt(box.dataset.enid), true);
  });
  const participants = [];
  document.querySelectorAll('.participant-row').forEach(row => {
    const initInput = row.querySelector('.init-input');
    const hpInput = row.querySelector('.hp-input');
    if (initInput) {
      const entry = {
        id: parseInt(initInput.dataset.enid),
        initiative: parseInt(initInput.value) || 0,
        defeated: row.classList.contains('defeated') ? true : false,
      };
      if (hpInput && hpInput.value.trim() !== '') {
        const hp = parseInt(hpInput.value);
        if (!isNaN(hp)) entry.hp_current = hp;
      }
      participants.push(entry);
    }
  });
  api(`/api/dm/encounter/${encId}/update-initiative`, { body: {participants} }).then(data => {
    if (btn) {
      btn.textContent = '✅ Saved!';
      btn.style.background = 'var(--success)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--success)';
      setTimeout(() => {
        btn.textContent = '💾 Save All'; btn.disabled = false;
        btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = '';
      }, 2000);
    }
  }).catch(() => {
    if (btn) {
      btn.textContent = '❌ Failed';
      btn.style.background = 'var(--danger)'; btn.style.color = '#fff';
      setTimeout(() => {
        btn.textContent = '💾 Save All'; btn.disabled = false;
        btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = '';
      }, 2500);
    }
  });
}

// ── Participant helpers ──
// ── Combat tracker: keyboard + click-to-locate ────────────────────────────────
// Atlas VTT's tracker lets you click an entry to jump to that token, and drive it from the
// keyboard mid-fight. There is no map yet, so "locate" means the row itself: highlight it,
// scroll it into view, and let Enter open it. Each row carries a stable anchor
// (`id="participant-row-<en_id>"` + `data-name`) so the map layer can centre a token from here
// without re-rendering the tracker.
let _trackerFocus = -1;

function trackerRows() {
  const modal = document.getElementById('encounterModal');
  // Only live while the encounter modal is open: otherwise every arrow key on the page
  // would be stolen from the search inputs and the creature palette.
  if (!modal || getComputedStyle(modal).display === 'none') return [];
  const detail = document.getElementById('encounterDetail');
  if (!detail) return [];
  return Array.from(detail.querySelectorAll('.participant-row'));
}

function focusTrackerRow(idx, opts) {
  const rows = trackerRows();
  if (!rows.length) return null;
  const scroll = !opts || opts.scroll !== false;
  if (idx < 0) idx = rows.length - 1;
  if (idx >= rows.length) idx = 0;
  rows.forEach((row, n) => row.classList.toggle('init-active', n === idx));
  _trackerFocus = idx;
  if (scroll && rows[idx].scrollIntoView) rows[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return rows[idx];
}

function trackerKeydown(ev) {
  if (!trackerRows().length) return;
  const tag = (ev.target && ev.target.tagName) || '';
  // Typing in an initiative or HP box is not navigation.
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  const k = ev.key;
  if (k === 'ArrowDown' || k === 'j' || k === 'J') {
    ev.preventDefault();
    focusTrackerRow(_trackerFocus + 1);
  } else if (k === 'ArrowUp' || k === 'k' || k === 'K') {
    ev.preventDefault();
    focusTrackerRow(_trackerFocus - 1);
  } else if (k === 'Enter' && _trackerFocus >= 0) {
    ev.preventDefault();
    toggleParticipantStats(_trackerFocus);
  }
}

document.addEventListener('keydown', trackerKeydown);

function toggleParticipantStats(idx) {
  const el = document.getElementById('p-stats-' + idx);
  if (el) el.classList.toggle('open');
}

function toggleDefeated(encId, enId, idx) {
  const row = document.querySelector(`.participant-row input[data-enid="${enId}"]`)?.closest('.participant-row');
  const isDead = row ? row.classList.contains('defeated') : false;
  if (row) {
    row.classList.toggle('defeated');
    const btn = row.querySelector('.defeat-btn');
    if (btn) {
      btn.textContent = isDead ? '💀 Defeat' : '✓ Alive';
      btn.className = 'defeat-btn ' + (isDead ? 'alive' : 'dead');
    }
  }
  // A player character's sheet has to agree with this row (see defeatPlayerWriteThrough)
  defeatPlayerWriteThrough(enId, !isDead).then(function (wrote) {
    if (wrote) renderInitiativeTrack();
  });

  // Save immediately
  fetch(`/api/dm/encounter/${encId}/update-initiative`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({single: {id: enId, defeated: !isDead}})
  }).then(r => r.json()).then(() => {});
}

function toggleSlot(encId, enId, idx, level, slotIdx, max) {
  // Get the stat block
  const statBlock = document.getElementById('p-stats-' + idx);
  if (!statBlock) return;
  // Find the dots for this level
  const levelRow = statBlock.querySelectorAll('.spell-slot-row')[level - 1];
  if (!levelRow) return;
  const dots = levelRow.querySelectorAll('.spell-slot-dot');
  if (slotIdx < dots.length) {
    const dot = dots[slotIdx];
    const wasUsed = dot.classList.contains('used');
    // Toggle this dot
    if (wasUsed) {
      dot.classList.remove('used');
    } else {
      dot.classList.add('used');
    }
    // Recompute used count for this level
    let used = 0;
    dots.forEach((d, i) => {
      if (i < dots.length && d.classList.contains('used')) used++;
    });
    // Update the text
    const textSpan = levelRow.querySelector('span:last-child');
    if (textSpan) textSpan.textContent = used + '/' + max;
  }
  // Save
  const newUsed = {};
  statBlock.querySelectorAll('.spell-slot-row').forEach((row, lvl) => {
    const lvlNum = lvl + 1;
    const used = row.querySelectorAll('.spell-slot-dot.used').length;
    if (used > 0) newUsed[lvlNum] = used;
  });
  fetch(`/api/dm/encounter/${encId}/update-initiative`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({single: {id: enId, spell_slots_used: newUsed}})
  }).then(r => r.json()).then(() => {});
}

function markDirty() {
  // Visual indicator that unsaved changes exist
  const btn = document.querySelector('.encounter-toolbar .btn-outline');
  if (btn && !btn.textContent.includes('*')) {
    btn.textContent = '💾 Save All *';
    btn.style.borderColor = 'var(--warning)';
    btn.style.color = 'var(--warning)';
  }
}

function deleteEncounter(id) {
  if (!confirm('Delete this encounter?')) return;
  fetch(`/api/dm/encounter/${id}/delete`, {method: 'POST'})
    .then(r => r.json()).then(() => location.reload());
}

function filterEncounters() {
  const status = document.getElementById('encounterFilter').value;
  document.querySelectorAll('.encounter-card').forEach(card => {
    card.style.display = (!status || card.dataset.status === status) ? '' : 'none';
  });
}

