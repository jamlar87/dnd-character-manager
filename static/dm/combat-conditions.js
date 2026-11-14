/* dm/combat-conditions.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Combat Conditions ────────────────────────────────────────────────────
const _combatCondExpanded = new Set();
const CONDITION_COLORS = window.DND_CONDITIONS.colors;
const STANDARD_CONDITIONS = window.DND_CONDITIONS.list;
const EXHAUSTION_LEVELS = window.DND_CONDITIONS.exhaustionLevels;

function combatAddCondition(enId, name, desc) {
  if (!name) return;
  const p = _combatParticipants.find(x => x.en_id === enId);
  if (!p) return;
  if (!p.conditions) p.conditions = [];
  if (p.conditions.some(c => c.name.toLowerCase() === name.toLowerCase())) return;
  p.conditions.push({name:name, description:desc||'', source:''});
  // If it's a PC, also save to character DB
  if (p.char_id && p.is_player) {
    api('/api/character/'+p.char_id+'/conditions', {
      body: {name:name, description:desc||'', source:''}
    }).catch(()=>{});
  }
  renderInitiativeTrack();
  saveCombatState();
}

async function combatDismissCond(enId, name) {
  const p = _combatParticipants.find(x => x.en_id === enId);
  if (!p) return;
  p.conditions = (p.conditions||[]).filter(c => c.name.toLowerCase() !== name.toLowerCase());
  if (p.char_id && p.is_player) {
    api('/api/character/'+p.char_id+'/conditions', {
      method:'DELETE',
      body: {name:name}
    }).catch(()=>{});
  }
  renderInitiativeTrack();
  saveCombatState();
}

function combatToggleCondExpand(enId) {
  if (_combatCondExpanded.has(enId)) {
    _combatCondExpanded.delete(enId);
  } else {
    _combatCondExpanded.add(enId);
  }
  renderInitiativeTrack();
}

function combatToggleCond(event, enId, name, desc, anchorEl) {
  document.querySelectorAll('.cond-detail-popout').forEach(el => el.remove());
  const badge = anchorEl || event.currentTarget;
  const popout = document.createElement('div');
  popout.className = 'cond-popout cond-detail-popout';
  const style = CONDITION_COLORS[name] || 'var(--accent)';
  // Name and description are DM-typed free text ("custom condition" box): build nodes, not markup.
  const popTitle = document.createElement('strong');
  popTitle.style.color = style.split(';')[0];
  popTitle.textContent = name;
  const popDesc = document.createElement('div');
  popDesc.style.cssText = 'font-size:0.65rem;color:var(--text-muted);margin:0.3rem 0;line-height:1.3';
  popDesc.textContent = desc || '';
  const popRemove = document.createElement('button');
  popRemove.style.cssText = 'display:block;margin-top:0.4rem;width:100%;padding:0.2rem;border:1px solid var(--danger);border-radius:4px;background:transparent;color:var(--danger);cursor:pointer;font-size:0.72rem';
  popRemove.textContent = 'Remove';
  popRemove.addEventListener('click', function () {
    combatDismissCond(enId, name);
    popout.remove();
  });
  popout.append(popTitle, popDesc, popRemove);
  popout.style.cssText = 'position:absolute;z-index:1001;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.6rem;min-width:180px;max-width:250px;box-shadow:0 4px 12px rgba(0,0,0,0.3);font-size:0.7rem';
  badge.parentElement.style.position = 'relative';
  badge.parentElement.appendChild(popout);
  const rect = badge.getBoundingClientRect();
  const parentRect = badge.parentElement.getBoundingClientRect();
  popout.style.left = '0';
  popout.style.top = (rect.bottom - parentRect.top + 4) + 'px';
}

function combatOpenCondPicker(enId) {
  document.querySelectorAll('.cond-detail-popout').forEach(el => el.remove());
  let html = '<div class="cond-popout" id="cond-picker-combat" style="position:fixed;top:15%;left:50%;transform:translateX(-50%);z-index:2000;width:380px;max-height:70vh;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.8rem;box-shadow:0 4px 12px rgba(0,0,0,0.4)">';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><strong style="font-size:0.75rem">Add Condition</strong><span onclick="document.getElementById(\'cond-picker-combat\').remove()" style="cursor:pointer;font-size:0.75rem">✕</span></div>';
  for (const c of STANDARD_CONDITIONS) {
    const style = CONDITION_COLORS[c.name] || 'var(--accent)';
    if (c.name === 'Exhaustion') {
      html += '<div onclick="combatOpenExhaustionPicker('+enId+')" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem"><span class="cond-badge" style="flex-shrink:0;background:'+style.split(';')[0]+';'+style.split(';')[1]+'">'+c.name+'</span><span style="color:var(--text-muted);font-size:0.72rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">Pick level 1–6…</span></div>';
    } else {
    html += '<div onclick="combatAddCondition('+enId+',\''+c.name+'\',\''+c.desc.replace(/'/g,"\\'")+'\');document.getElementById(\'cond-picker-combat\').remove()" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem"><span class="cond-badge" style="flex-shrink:0;background:'+style.split(';')[0]+';'+style.split(';')[1]+'">'+c.name+'</span><span style="color:var(--text-muted);font-size:0.72rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">'+c.desc+'</span></div>';
    }
  }
  html += '<div style="margin-top:0.4rem;border-top:1px solid var(--border);padding-top:0.4rem"><input id="custom-cond-name-combat" placeholder="Custom condition…" style="width:100%;padding:0.25rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.65rem;margin-bottom:0.2rem"><button onclick="combatAddCondition('+enId+',document.getElementById(\'custom-cond-name-combat\').value,\'\');document.getElementById(\'cond-picker-combat\').remove()" style="width:100%;padding:0.2rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:0.65rem">Add Custom</button></div>';
  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);
}

function combatOpenExhaustionPicker(enId) {
  const el = document.getElementById('cond-picker-combat');
  if (!el) return;
  el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><a href="#" onclick="event.preventDefault();document.getElementById(\'cond-picker-combat\').remove();combatOpenCondPicker('+enId+')" style="font-size:0.7rem;color:var(--text-muted)">← Back</a><strong style="font-size:0.75rem">Exhaustion Level</strong><span onclick="document.getElementById(\'cond-picker-combat\').remove()" style="cursor:pointer;font-size:0.75rem">✕</span></div>';
  for (const e of EXHAUSTION_LEVELS) {
    const label = 'Exhaustion '+e.level;
    const color = CONDITION_COLORS['Exhaustion'] || '#827717;color:#fff';
    el.innerHTML += '<div onclick="combatAddCondition('+enId+',\''+label+'\', \'Level '+e.level+': '+e.desc.replace(/'/g,"\\'")+'\');document.getElementById(\'cond-picker-combat\').remove()" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;align-items:center;gap:0.4rem"><span class="cond-badge" style="flex-shrink:0;background:'+color.split(';')[0]+';'+color.split(';')[1]+'">'+label+'</span><span style="color:var(--text-muted);font-size:0.72rem;flex:1">'+e.desc+'</span></div>';
  }
}

// ── Live HP Polling ──────────────────────────────────────────────────────

// The tracker's poll: 2s while nothing better exists, 30s once the campaign stream is carrying
// sheet events, straight back to 2s if that stream drops. It only ever returns the DM's OWN
// characters (/api/sync-combat-hp filters on user_id), so the events are what make it live for
// a table whose players own their own sheets.
const COMBAT_POLL_FAST_MS = 2000;
const COMBAT_POLL_SAFE_MS = 30000;
let _combatPollTimer = null;

async function syncPlayerAndSummonHP() {
  // A backgrounded DM tab has no one looking at it: skip the fetch entirely and let the
  // 2s timer resume on the next visible tick (see the visibilitychange wire at the bottom).
  if (document.hidden) return;
  // Collect unique character IDs from players and summon owners
  const charIds = new Set();
  for (const p of _campaignPlayers) {
    if (p.char_id) charIds.add(p.char_id);
  }
  for (const p of _combatParticipants) {
    if (p.is_summon && p.owner_char_id) charIds.add(p.owner_char_id);
    if (p.is_player && p.char_id) charIds.add(p.char_id);
  }
  if (charIds.size === 0) return;

  try {
    const data = await api('/api/sync-combat-hp', {
      body: {char_ids: Array.from(charIds)}
    });
    const chars = data.characters || {};

    // Update campaign players HP. The players panel holds no inputs — only stats — so it is safe
    // to re-render the moment a value lands, and it has to be: this loop used to update the data
    // without ever flagging a repaint, so the DM's panel kept showing the old number until some
    // unrelated render happened along. (The initiative track DOES hold hp inputs, so it still
    // waits for a participant change rather than yanking the field out from under a typing DM.)
    let changedPlayers = false;
    for (const p of _campaignPlayers) {
      const c = chars[String(p.char_id)];
      if (c && c.hp_current !== undefined) {
        if (p.hp_current !== c.hp_current || p.hp_max !== c.hp_max) changedPlayers = true;
        p.hp_current = c.hp_current;
        p.hp_max = c.hp_max;
      }
    }

    // Update combat participants HP (players + summons)
    let changed = false;
    for (const p of _combatParticipants) {
      if (p.is_player && p.char_id) {
        const c = chars[String(p.char_id)];
        if (c && c.hp_current !== undefined && c.hp_current !== p.hp_current) {
          p.hp_current = c.hp_current;
          p.hp_max = c.hp_max;
          changed = true;
        }
        // Sync conditions from sheet
        if (c && c.conditions) {
          const srvConds = JSON.stringify(c.conditions);
          const localConds = JSON.stringify(p.conditions || []);
          if (srvConds !== localConds) {
            p.conditions = c.conditions;
            changed = true;
          }
        }
      } else if (p.is_summon && p.owner_char_id && p.summon_idx !== undefined) {
        const c = chars[String(p.owner_char_id)];
        if (c && c.summons && c.summons[p.summon_idx]) {
          const s = c.summons[p.summon_idx];
          if (s.hp_current !== undefined && s.hp_current !== p.hp_current) {
            p.hp_current = s.hp_current;
            p.hp_max = s.hp_max;
            changed = true;
          }
        }
      }
    }

    // Re-render if anything changed
    // `changed` implies `changedPlayers` on the HP path, so this used to rebuild the players
    // panel twice per tick — 243 lines of innerHTML for the same data.
    if (changed) {
      // renderInitiativeTrack() replaces every combatant card's innerHTML, so rebuilding it
      // while the DM is mid-keystroke takes the focused input away. Wait for the next tick.
      const focused = document.activeElement;
      const typingInTrack = focused && focused.closest && focused.closest('#initiative-track');
      if (!typingInTrack) renderInitiativeTrack();
      renderPlayersPanel();
    } else if (changedPlayers) {
      renderPlayersPanel();
    }
  } catch(e) { /* silent */ }
}

function startCombatPolling(interval) {
  stopCombatPolling();
  _combatPollTimer = setInterval(syncPlayerAndSummonHP, interval || COMBAT_POLL_FAST_MS);
}

// ...and a backgrounded tab catches up the moment it is looked at again, rather than showing a
// value up to one interval stale (2s fast / 30s safe).
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) syncPlayerAndSummonHP();
});

function stopCombatPolling() {
  if (_combatPollTimer) {
    clearInterval(_combatPollTimer);
    _combatPollTimer = null;
  }
}


// ── Page load: auto-restore last combat encounter + campaign ──
document.addEventListener('DOMContentLoaded', async function() {
  // Monster cards are built here (see renderMonsterCards) — do it before the
  // source filter mounts so its onChange sees a populated grid.
  const renderedMonsters = renderMonsterCards();
  const renderedNpcs = renderManualNpcRows();
  const renderedTraps = renderTrapCards();
  const renderedSpells = renderSpellCards();
  renderManualGroups();

  // ── Manual (book) filter on every search bar ──
  if (window.SourceFilter) {
    const mounts = [
      ['monsterSrcFilter', 'dm-monsters', filterMonsters],
      ['spellSrcFilter', 'dm-spells', filterSpells],
      ['npcSrcFilter', 'dm-npcs', filterNpcs],
      ['trapSrcFilter', 'dm-traps', filterTraps],
      ['itemPickerSrcFilter', 'dm-items', function () {
        const inp = document.getElementById('itemPickerSearch');
        searchItemPicker(inp ? inp.value : '');
      }],
      ['combatSrcFilter', 'dm-combat', filterCombatCreatures],
    ];
    mounts.forEach(function (m) {
      const el = document.getElementById(m[0]);
      if (el) SourceFilter.init(el, {key: m[1], onChange: m[2]});
    });
  }
  // Count/apply the restored source filter now that the grid exists.
  if (renderedMonsters) filterMonsters();
  if (renderedNpcs) filterNpcs();
  if (renderedTraps) filterTraps();
  if (renderedSpells) filterSpells();

  // Populate combat + items dropdowns regardless of active tab (each has its own guard)
  initCombatPanel();
  loadItemsPanel();

  let lastEncId = null;
  try { lastEncId = localStorage.getItem('combatLastEncounterId'); } catch(e) {}
  if (lastEncId) {
    const sel = document.getElementById('combatEncounterSelect');
    if (sel) {
      // Check if the option still exists
      const opt = sel.querySelector(`option[value="${lastEncId}"]`);
      if (opt) {
        sel.value = lastEncId;
        await loadCombatEncounter();
      }
    }
  }
});


