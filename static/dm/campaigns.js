/* dm/campaigns.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Campaigns ──
function showCreateCampaign() {
  const name = prompt('Campaign name:');
  if (!name) return;
  fetch('/api/dm/campaign/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description: '', party_level: 1, party_size: 4})
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
  });
}

async function openCampaign(id) {
  openModal('campaignModal');
  document.getElementById('campaignDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';

  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const camp = (d.campaigns || []).find(c => c.id === id);
    if (!camp) throw 'Not found';

    const quests = typeof camp.quests === 'string' ? (JSON.parse(camp.quests) || []) : (camp.quests || []);
    const locations = typeof camp.locations === 'string' ? (JSON.parse(camp.locations) || []) : (camp.locations || []);
    const chars = typeof camp.characters === 'string' ? (JSON.parse(camp.characters) || []) : (camp.characters || []);

    // Compute live party stats from linked characters
    const liveSize = chars.length || camp.party_size;
    const liveLevel = chars.length ? Math.round(chars.reduce((s, c) => s + (c.level || 1), 0) / chars.length) : camp.party_level;

    // Fetch user's characters for the party picker
    const cr = await fetch('/api/dm/user-characters');
    const cd = await cr.json();
    const allChars = cd.characters || [];

    let html = `<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem">
      <div>
        <h2 style="margin:0">📜 ${camp.name}</h2>
        <p style="color:var(--text-muted);font-size:0.85rem">Party: L${liveLevel} · ${plural(liveSize, 'player')} · ${plural(chars.length, 'character')}</p>
      </div>
      <div style="display:flex;gap:0.3rem">
        <button class="btn btn-outline btn-sm" onclick="editCampaignMeta(${camp.id})">Edit</button>
        <button class="btn btn-outline btn-sm" onclick="addCampQuest(${camp.id})">+ Quest</button>
        <button class="btn btn-outline btn-sm" onclick="addCampLocation(${camp.id})">+ Location</button>
      </div>
    </div>`;
    if (camp.description) html += `<p style="margin-bottom:1rem">${camp.description}</p>`;

    // ── Party Characters Section ──
    html += `<div style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <h4 style="margin:0">👥 Party Characters</h4>
        <div style="display:flex;gap:0.3rem">
          <select id="charPicker" style="padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
            <option value="">— Add character —</option>`;
    allChars.forEach(ch => {
      const inCamp = chars.some(c => c.id === ch.id);
      if (!inCamp) {
        html += `<option value="${ch.id}">${dmEsc(ch.name)} (L${ch.level} ${dmEsc(ch.race)} ${dmEsc(ch.class_name)})</option>`;
      }
    });
    html += `</select>
          <button class="btn btn-primary btn-sm" onclick="addCharToCampaign(${camp.id})">+ Add</button>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:0.3rem">`;
    if (chars.length) {
      chars.forEach(ch => {
        const hpPct = ch.hp_max > 0 ? Math.round(ch.hp_current / ch.hp_max * 100) : 0;
        const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'danger' : hpPct < 50 ? 'warn' : 'ok';
        const mod = s => ch.modifiers && ch.modifiers[s] !== undefined ? (ch.modifiers[s] >= 0 ? '+' + ch.modifiers[s] : ch.modifiers[s]) : '?';
        const slotStr = ch.spell_slots && Object.entries(ch.spell_slots).filter(([k,v]) => v > 0).length
          ? ' · 🔮 ' + Object.entries(ch.spell_slots).filter(([k,v]) => v > 0).map(([k,v]) => `${k}:${v}`).join(' ')
          : '';
        html += `<div style="display:flex;flex-direction:column;padding:0.5rem 0.7rem;background:var(--bg);border:1px solid var(--border);border-radius:6px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.3rem">
            <span style="display:flex;align-items:center;gap:0.45rem;min-width:0;flex-wrap:wrap">
              ${charPortraitTile(ch.id, ch.name, {size: 32, hasPortrait: ch.has_portrait})}
              <strong style="cursor:pointer" data-char-action="previewCharSheet" data-char-id="${ch.id}" data-char-name="${dmEsc(ch.name)}" title="View character sheet">${dmEsc(ch.name)}</strong>
              <span style="color:var(--text-muted);font-size:0.85rem">L${ch.level} ${dmEsc(ch.race)}${ch.subrace ? ' (' + dmEsc(ch.subrace) + ')' : ''} ${dmEsc(ch.class_name)}${ch.subclass ? ' — ' + dmEsc(ch.subclass) : ''}</span>
              <span class="badge badge-muted" style="font-size:0.65rem">${dmEsc(ch.status || 'active')}</span>
            </span>
            <button class="btn btn-outline btn-sm" data-char-action="previewCharSheet" data-char-id="${ch.id}" data-char-name="${dmEsc(ch.name)}" title="View sheet">📋</button>
            <button class="btn btn-danger btn-sm" onclick="removeCharFromCampaign(${camp.id}, ${ch.id})">✕</button>
          </div>
          <div style="display:flex;gap:0.75rem;margin-top:0.3rem;flex-wrap:wrap;align-items:center;font-size:0.8rem">
            <span><strong>HP</strong> ${ch.hp_current}/${ch.hp_max}${ch.temp_hp > 0 ? ' (+' + ch.temp_hp + ' temp)' : ''}</span>
            <span><strong>AC</strong> ${ch.ac}</span>
            <span><strong>Speed</strong> ${ch.speed || 30}</span>
            <span><strong>PB</strong> +${ch.proficiency_bonus || 2}</span>
            <span><strong>Initiative</strong> ${mod('dexterity')}</span>
            ${ch.passive_perception ? `<span><strong>PP</strong> ${ch.passive_perception}</span>` : ''}
            ${ch.inspiration ? `<span class="badge badge-accent" style="font-size:0.65rem">✨ Inspiration</span>` : ''}
            ${ch.exhaustion > 0 ? `<span style="color:var(--warn);font-size:0.75rem">Exhaustion: ${ch.exhaustion}</span>` : ''}
          </div>
          <div style="display:flex;gap:0.3rem;margin-top:0.25rem;font-size:0.75rem">
            <span class="ability-tag">STR ${ch.strength} (${mod('strength')})</span>
            <span class="ability-tag">DEX ${ch.dexterity} (${mod('dexterity')})</span>
            <span class="ability-tag">CON ${ch.constitution} (${mod('constitution')})</span>
            <span class="ability-tag">INT ${ch.intelligence} (${mod('intelligence')})</span>
            <span class="ability-tag">WIS ${ch.wisdom} (${mod('wisdom')})</span>
            <span class="ability-tag">CHA ${ch.charisma} (${mod('charisma')})</span>
          </div>
          <div class="hp-bar-mini" style="width:100%;margin-top:0.25rem"><div class="hp-bar-mini-fill ${hpClass}" style="width:${Math.max(0, hpPct)}%"></div></div>
          ${slotStr ? `<div style="font-size:0.7rem;color:var(--accent);margin-top:0.15rem">🔮 Spell slots: ${slotStr}</div>` : ''}
        </div>`;
      });
    } else {
      html += `<p style="color:var(--text-muted);font-size:0.85rem">No characters in this campaign yet. Add from your character list above.</p>`;
    }
    html += `</div></div>`;

    // ── Campaign NPCs Section ──
    const npcs = typeof camp.npcs === 'string' ? (JSON.parse(camp.npcs) || []) : (camp.npcs || []);
    html += `<div style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <h4 style="margin:0">🧑 NPCs (${npcs.length})</h4>
        <button class="btn btn-primary btn-sm" onclick="showNpcPicker(${camp.id})">+ Add NPC</button>
      </div>
      <div id="npcPicker-${camp.id}" style="display:none;margin-bottom:0.5rem;max-height:300px;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:0.5rem">
        <div style="text-align:center;color:var(--text-muted);padding:1rem">Loading NPCs...</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:0.3rem">`;
    if (npcs.length) {
      npcs.forEach(n => {
        const isEnemy = n.is_enemy ? 'enemy' : 'npc';
        html += `<div style="display:flex;flex-direction:column;padding:0.5rem 0.7rem;background:var(--bg);border:1px solid var(--border);border-radius:6px" data-npc-id="${n.id}">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.3rem">
            <span>
              <strong>${n.name}</strong>
              <span class="npc-badge ${isEnemy}" style="font-size:0.65rem">${n.is_enemy ? 'Enemy' : 'NPC'}</span>
              <span style="color:var(--text-muted);font-size:0.85rem">${n.race}${n.class_name ? ' · L' + n.level + ' ' + n.class_name : ''}${n.subclass ? ' (' + n.subclass + ')' : ''}</span>
              ${n.role ? '<span class="badge badge-muted" style="font-size:0.65rem">' + n.role + '</span>' : ''}
              ${n.alignment ? '<span style="font-size:0.75rem;color:var(--text-muted)">' + n.alignment + '</span>' : ''}
            </span>
            <button class="btn btn-danger btn-sm" onclick="removeNpcFromCampaign(${camp.id}, ${n.id})">✕</button>
          </div>
          <div style="display:flex;gap:0.75rem;margin-top:0.3rem;font-size:0.8rem;flex-wrap:wrap">
            <span><strong>HP</strong> ${n.hp_current || 0}/${n.hp_max || 0}</span>
            <span><strong>AC</strong> ${n.ac || 10}</span>
            ${n.faction ? '<span><strong>Faction</strong> ' + n.faction + '</span>' : ''}
          </div>
          <div style="margin-top:0.3rem">
            <textarea id="npcNotes-${camp.id}-${n.id}" rows="2" style="width:100%;padding:0.3rem 0.5rem;background:var(--card-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.75rem;resize:vertical" placeholder="DM notes for this NPC…">${n.notes || ''}</textarea>
            <button class="btn btn-outline btn-sm" style="margin-top:0.2rem;font-size:0.7rem" onclick="saveNpcNotes(${camp.id}, ${n.id}, 'npcNotes-${camp.id}-${n.id}')">💾 Save Notes</button>
          </div>
        </div>`;
      });
    } else {
      html += `<p style="color:var(--text-muted);font-size:0.85rem">No NPCs linked yet. Use the NPCs tab to add them.</p>`;
    }
    html += `</div></div>`;

    if (camp.session_notes) {
      html += `<h4>Session Notes</h4>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:0.75rem;margin-bottom:1rem;font-size:0.85rem;white-space:pre-wrap">${camp.session_notes}</div>`;
    }

    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">`;

    // Quests
    html += `<div><h4 style="margin-bottom:0.5rem">📋 Quests (${quests.length})</h4>`;
    if (quests.length) {
      quests.forEach((q, i) => {
        html += `<div class="camp-quest">
          <span>${q.name || 'Quest ' + (i+1)} ${q.status ? '<span class="badge badge-' + (q.status === 'completed' ? 'accent' : 'muted') + '">' + q.status + '</span>' : ''}</span>
          <button class="btn btn-danger btn-sm" onclick="removeCampQuest(${camp.id}, ${i})">✕</button>
        </div>`;
      });
    } else { html += '<p style="color:var(--text-muted);font-size:0.85rem">No quests yet</p>'; }
    html += `</div>`;

    // Locations
    html += `<div><h4 style="margin-bottom:0.5rem">📍 Locations (${locations.length})</h4>`;
    if (locations.length) {
      locations.forEach((l, i) => {
        html += `<div class="camp-quest">
          <span>${l.name || 'Location ' + (i+1)}${l.type ? ' <span class="badge badge-muted">' + l.type + '</span>' : ''}</span>
          <button class="btn btn-danger btn-sm" onclick="removeCampLocation(${camp.id}, ${i})">✕</button>
        </div>`;
      });
    } else { html += '<p style="color:var(--text-muted);font-size:0.85rem">No locations yet</p>'; }
    html += `</div></div>`;

    // Notes editor
    html += `<h4>Campaign Notes</h4>
      <textarea id="campNotes" rows="4" style="width:100%;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);margin-bottom:0.5rem">${camp.notes || ''}</textarea>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <button class="btn btn-outline btn-sm" onclick="saveCampNotes(${camp.id})">Save Notes</button>
        <button class="btn btn-danger btn-sm" onclick="deleteCampaign(${camp.id});closeModal('campaignModal')">Delete Campaign</button>
      </div>`;

    document.getElementById('campaignDetail').innerHTML = html;
  } catch(e) {
    document.getElementById('campaignDetail').innerHTML = '<p style="color:var(--danger)">Failed to load campaign.</p>';
  }
}

function addCharToCampaign(campId) {
  const sel = document.getElementById('charPicker');
  const charId = parseInt(sel.value);
  if (!charId) return;
  fetch(`/api/dm/campaign/${campId}/add-character`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({character_id: charId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function removeCharFromCampaign(campId, charId) {
  if (!confirm('Remove this character from the campaign?')) return;
  fetch(`/api/dm/campaign/${campId}/remove-character`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({character_id: charId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

// ── NPC ↔ Campaign functions ──
async function showNpcPicker(campId) {
  const picker = document.getElementById('npcPicker-' + campId);
  if (!picker) return;
  // Toggle
  if (picker.style.display === 'block') { picker.style.display = 'none'; return; }
  picker.style.display = 'block';
  picker.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:1rem">Loading NPCs...</div>';
  
  try {
    const r = await fetch('/api/dm/npcs');
    const d = await r.json();
    const allNpcs = d.npcs || [];
    // Filter out already-linked NPCs
    const linked = document.querySelectorAll('[data-npc-id]');
    const linkedIds = new Set();
    linked.forEach(el => linkedIds.add(parseInt(el.dataset.npcId)));
    const available = allNpcs.filter(n => !linkedIds.has(n.id));
    
    if (!available.length) {
      picker.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:1rem">All NPCs already linked</div>';
      return;
    }
    
    let html = '<div style="display:flex;flex-direction:column;gap:0.2rem">';
    available.forEach(n => {
      const isEnemy = n.is_enemy ? 'enemy' : 'npc';
      html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:0.3rem 0.5rem;background:var(--bg);border-radius:4px;font-size:0.8rem">
        <span>
          <strong>${n.name}</strong>
          <span class="npc-badge ${isEnemy}" style="font-size:0.72rem">${n.is_enemy ? 'Enemy' : 'NPC'}</span>
          <span style="color:var(--text-muted)">${n.race}${n.class_name ? ' L' + n.level + ' ' + n.class_name : ''}${n.role ? ' · ' + n.role : ''}</span>
        </span>
        <button class="btn btn-outline btn-sm" onclick="addNpcToCampaign(${n.id}, ${campId})">➕ Add</button>
      </div>`;
    });
    html += '</div>';
    picker.innerHTML = html;
  } catch(e) {
    picker.innerHTML = '<div style="color:var(--danger);padding:1rem">Failed to load NPCs</div>';
  }
}

function addNpcToCampaign(npcId, campId) {
  fetch(`/api/dm/campaign/${campId}/add-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(d => {
    if (d.ok) { openCampaign(campId); }
    else alert(d.error || 'Failed');
  });
}

function removeNpcFromCampaign(campId, npcId) {
  if (!confirm('Remove this NPC from the campaign?')) return;
  fetch(`/api/dm/campaign/${campId}/remove-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function saveNpcNotes(campId, npcId, elementId) {
  const notes = document.getElementById(elementId).value;
  fetch(`/api/dm/campaign/${campId}/update-npc-notes`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId, notes})
  }).then(r => r.json()).then(d => {
    if (d.ok) { /* silent save */ }
  });
}

function editCampaignMeta(id) {
  const name = prompt('Campaign name:');
  if (!name) return;
  const pl = prompt('Party level:', '1') || '1';
  const ps = prompt('Party size:', '4') || '4';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, party_level: parseInt(pl), party_size: parseInt(ps)})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function addCampQuest(id) {
  const qName = prompt('Quest name:');
  if (!qName) return;
  const status = prompt('Status (active/completed):', 'active') || 'active';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({addQuest: JSON.stringify({name: qName, status})})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function removeCampQuest(campId, idx) {
  if (!confirm('Remove quest?')) return;
  fetch(`/api/dm/campaign/${campId}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({removeQuest: idx})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function addCampLocation(id) {
  const lName = prompt('Location name:');
  if (!lName) return;
  const type = prompt('Type (city/dungeon/forest/etc):', '') || '';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({addLocation: JSON.stringify({name: lName, type})})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function removeCampLocation(campId, idx) {
  if (!confirm('Remove location?')) return;
  fetch(`/api/dm/campaign/${campId}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({removeLocation: idx})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function saveCampNotes(id, elementId) {
  const elId = elementId || 'campNotes';
  const notes = document.getElementById(elId).value;
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({notes})
  }).then(r => r.json()).then(() => { /* saved */ });
}

function deleteCampaign(id) {
  if (!confirm('Delete this campaign?')) return;
  fetch(`/api/dm/campaign/${id}/delete`, {method: 'POST'}).then(r => r.json()).then(() => location.reload());
}

