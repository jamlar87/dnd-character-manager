/* dm/npcs.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── NPCs ──
function showCreateNpc() {
  showNpcEditor(null);
}

function editNpc(id) {
  showNpcEditor(id);
}

// Show NPC info — editable for DB NPCs, read-only for manual NPCs
function showNpcInfo(id, name) {
  if (id > 0) { showNpcEditor(id); return; }
  // Manual NPC: show read-only stat card from cache
  const c = (window._creatureCache || []).find(c => c.id === id && c._kind === 'npc');
  if (!c) return;
  openModal('npcModal');
  let html = refArtImg('npc', c.name || name, 260) + `<h2 style="margin:0 0 0.5rem 0">📖 ${c.name || name}</h2>
    <p style="color:var(--text-muted);font-size:0.8rem;margin-bottom:1rem">Reference NPC (read-only)</p>
    <div class="ps-grid">
      <div class="ps-stat"><div class="ps-lbl">Race</div><div class="ps-val">${c.race || '?'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">Class</div><div class="ps-val">${c.class_name || '—'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">Level</div><div class="ps-val">${c.level || '?'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">HP</div><div class="ps-val">${c.hp_current}/${c.hp_max}</div></div>
      <div class="ps-stat"><div class="ps-lbl">AC</div><div class="ps-val">${c.ac}</div></div>
      <div class="ps-stat"><div class="ps-lbl">XP</div><div class="ps-val">${c.xp_reward || 0}</div></div>
    </div>
    <div style="margin-top:1rem;text-align:right">
      <button class="btn btn-primary btn-sm" onclick="openNpcBuilder(${id}, ${JSON.stringify(c).replace(/"/g, '&quot;')})" title="Create a fully playable character from this NPC" style="margin-right:0.5rem">🛠️ Fully Build</button>
      <button class="btn btn-outline btn-sm" onclick="closeModal('npcModal')">Close</button>
    </div>`;
  document.getElementById('npcEditor').innerHTML = html;
}

// ── NPC → Character Builder ─────────────────────────────────────────
// Opens the NPC info modal with a "Fully Build" interface that lets the
// user fill in gaps and create a full character from an NPC stat block.
async function openNpcBuilder(id, npcData) {
  openModal('npcModal');
  document.getElementById('npcEditor').innerHTML = '<div style="text-align:center;padding:2rem">⏳ Analyzing NPC…</div>';

  // Convert encounter-creature cache format to npc.json format
  const npc = npcData._raw || npcData;
  if (!npc.name && npcData.name) npc.name = npcData.name;
  if (!npc.race && npcData.race) npc.race = npcData.race;
  if (!npc.class_name && npcData.class_name) npc.class_name = npcData.class_name;
  if (!npc.level && npcData.level) npc.level = npcData.level;
  if (!npc.ability_scores) {
    npc.ability_scores = {};
    for (const ab of ['strength','dexterity','constitution','intelligence','wisdom','charisma']) {
      if (npcData[ab] !== undefined) npc.ability_scores[ab] = npcData[ab];
    }
  }
  if (!npc.hit_points && npcData.hp_max) npc.hit_points = npcData.hp_max;

  try {
    const resp = await fetch('/api/build-from-npc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({npc}),
    });
    const result = await resp.json();
    if (!result.ok) {
      document.getElementById('npcEditor').innerHTML = `<p style="color:var(--danger)">Error: ${result.error || 'Unknown'}</p>`;
      return;
    }
    renderNpcBuilder(result);
  } catch (e) {
    document.getElementById('npcEditor').innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
}

function renderNpcBuilder(result) {
  const ad = result.auto_detected;
  const gaps = result.gaps || [];
  const cd = result.character_data;

  let html = `<h2 style="margin:0 0 0.5rem 0">🛠️ Fully Build: ${cd.name}</h2>
    <p style="color:var(--text-muted);font-size:0.8rem;margin-bottom:1rem">Auto-detected fields from NPC stat block — fill in any gaps below.</p>`;

  // Auto-detected summary
  html += `<div class="ps-grid" style="margin-bottom:1rem">`;
  const autoFields = [
    {label: 'Race', value: ad.race || cd.race, icon: '🧬'},
    {label: 'Class', value: ad.class || cd.class_name, icon: '⚔️'},
    {label: 'Subclass', value: ad.subclass || '—', icon: '🔮'},
    {label: 'Level', value: ad.level, icon: '⬆️'},
    {label: 'Ability Scores', value: ad.has_ability_scores ? '✅ Detected' : '⚠️ Missing', icon: '📊'},
    {label: 'Skills', value: ad.has_skills ? '✅ Detected' : '❌ None', icon: '🎯'},
    {label: 'Features', value: ad.has_features ? '✅ Detected' : '❌ None', icon: '⭐'},
    {label: 'Equipment', value: ad.has_equipment ? '✅ Detected' : '❌ None', icon: '🎒'},
    {label: 'Spellcasting', value: ad.has_spellcasting ? '✅ Detected' : '❌ None', icon: '✨'},
  ];
  for (const f of autoFields) {
    html += `<div class="ps-stat"><div class="ps-lbl">${f.icon} ${f.label}</div><div class="ps-val" style="font-size:0.75rem">${f.value || '?'}</div></div>`;
  }
  html += `</div>`;

  // Gaps section
  if (gaps.length > 0) {
    html += `<h3 style="margin:0.5rem 0">⚠️ Requires Your Input</h3>
      <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:0.75rem;margin-bottom:1rem">`;
    for (let i = 0; i < gaps.length; i++) {
      const g = gaps[i];
      html += `<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
        <label style="flex:0 0 120px;font-size:0.8rem;font-weight:600">${g.label}</label>`;
      if (g.options && g.options.length > 0) {
        html += `<select id="bg-gap-${i}" style="flex:1;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">— Select ${g.label} —</option>`;
        for (const opt of g.options) {
          html += `<option value="${opt.replace(/"/g, '&quot;')}">${opt}</option>`;
        }
        html += `</select>`;
      } else if (g.field === 'alignment') {
        const alignments = ['Lawful Good','Neutral Good','Chaotic Good','Lawful Neutral','True Neutral','Chaotic Neutral','Lawful Evil','Neutral Evil','Chaotic Evil'];
        html += `<select id="bg-gap-${i}" style="flex:1;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">— Select Alignment —</option>`;
        for (const a of alignments) {
          html += `<option value="${a}">${a}</option>`;
        }
        html += `</select>`;
      } else {
        html += `<input id="bg-gap-${i}" type="text" value="${(g.original || '').replace(/"/g, '&quot;')}" placeholder="${g.note || ''}" style="flex:1;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">`;
      }
      html += `<span style="font-size:0.7rem;color:var(--text-muted)">${g.note || ''}</span>`;
      html += `</div>`;
    }
    html += `</div>`;
  } else {
    html += `<p style="color:var(--success);font-size:0.85rem">✅ All fields detected — ready to build!</p>`;
  }

  // Action buttons
  html += `<div style="display:flex;gap:0.5rem;margin-top:1rem;justify-content:flex-end">
    <button class="btn btn-primary" onclick="submitNpcBuild(${JSON.stringify(cd).replace(/"/g, '&quot;')}, ${JSON.stringify(gaps.map((g,i) => ({field: g.field, index: i}))).replace(/"/g, '&quot;')})">
      🚀 Create Character
    </button>
    <button class="btn btn-outline" onclick="closeModal('npcModal')">Cancel</button>
  </div>`;

  document.getElementById('npcEditor').innerHTML = html;
}

async function submitNpcBuild(charData, gapDefs) {
  // Fill in gaps from the form
  for (const gd of gapDefs) {
    const el = document.getElementById(`bg-gap-${gd.index}`);
    if (el) {
      charData[gd.field] = el.value;
    }
  }

  // Build the POST payload matching /api/character/create shape
  const body = {
    name: charData.name,
    race: charData.race || 'Human',
    class_name: charData.class_name || 'Fighter',
    subclass: charData.subclass || '',
    level: charData.level || 1,
    strength: (charData.ability_scores && charData.ability_scores.strength) || 10,
    dexterity: (charData.ability_scores && charData.ability_scores.dexterity) || 10,
    constitution: (charData.ability_scores && charData.ability_scores.constitution) || 10,
    intelligence: (charData.ability_scores && charData.ability_scores.intelligence) || 10,
    wisdom: (charData.ability_scores && charData.ability_scores.wisdom) || 10,
    charisma: (charData.ability_scores && charData.ability_scores.charisma) || 10,
    alignment: charData.alignment || 'True Neutral',
    hp_max: charData.hit_points || 10,
    features: JSON.stringify(charData.features || []),
    equipment: JSON.stringify(charData.equipment || []),
    source: charData.source || '',
    description: charData.description || '',
  };

  try {
    const resp = await fetch('/api/character/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const result = await resp.json();
    if (result.id) {
      document.getElementById('npcEditor').innerHTML = `<div style="text-align:center;padding:1.5rem">
        <h3>✅ Character Created!</h3>
        <p><strong>${result.name}</strong> (ID: ${result.id})</p>
        <p style="font-size:0.8rem;color:var(--text-muted)">You can now view and edit this character from the main character list.</p>
        <div style="margin-top:1rem">
          <button class="btn btn-primary" onclick="window.open('/sheet/${result.id}','_blank')">📄 Open Sheet</button>
          <button class="btn btn-outline" onclick="closeModal('npcModal')">Close</button>
        </div>
      </div>`;
    } else {
      document.getElementById('npcEditor').innerHTML = `<p style="color:var(--danger)">Error: ${result.error || 'Failed to create character'}</p>`;
    }
  } catch (e) {
    document.getElementById('npcEditor').innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
}

async function showNpcEditor(id) {
  openModal('npcModal');
  document.getElementById('npcEditor').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';

  let npc = {name: '', race: 'Human', class_name: 'Fighter', subclass: '', level: 1,
    strength: 10, dexterity: 10, constitution: 10, intelligence: 10, wisdom: 10, charisma: 10,
    hp_max: 10, hp_current: 10, ac: 10, speed: 30, alignment: 'True Neutral', role: 'NPC',
    faction: '', notes: '', is_enemy: false, xp_reward: 0};

  if (id) {
    try {
      const r = await fetch(`/api/dm/npc/${id}`);
      npc = await r.json();
    } catch(e) {}
  }

  const races = ['Dwarf','Elf','Halfling','Human','Dragonborn','Gnome','Half-Elf','Half-Orc','Tiefling'];
  const classes = ['Barbarian','Bard','Cleric','Druid','Fighter','Monk','Paladin','Ranger','Rogue','Sorcerer','Warlock','Wizard'];
  const alignments = ['Lawful Good','Neutral Good','Chaotic Good','Lawful Neutral','True Neutral','Chaotic Neutral','Lawful Evil','Neutral Evil','Chaotic Evil'];
  const roles = ['NPC','Guard','Merchant','Artisan','Noble','Cultist','Bandit','Assassin','Spy','Prisoner','Guardian','Villain','Lieutenant','Boss','Minion'];

  let html = `<h2 style="margin:0 0 1rem 0">${id ? '✏️ Edit' : '➕ Create'} NPC</h2>
    <form id="npcForm" onsubmit="return saveNpc(event, ${id || 'null'})">
      <div style="display:flex;gap:0.9rem;align-items:center;margin-bottom:0.9rem;flex-wrap:wrap">
        <div id="npcPortraitPreview">${charPortraitTile(id || null, npc.name || '?', {size: 64, kind: 'npc', hasPortrait: npc.has_portrait})}</div>
        <div style="display:flex;flex-direction:column;gap:0.35rem">
          <label class="btn btn-outline btn-sm" style="cursor:pointer;margin:0">
            🖼️ ${npc.has_portrait ? 'Replace portrait' : 'Add portrait'}
            <input type="file" id="npcPortraitFile" accept="image/*" style="display:none" onchange="npcPortraitPicked(this)">
          </label>
          <button type="button" class="btn btn-outline btn-sm" id="npcPortraitGenBtn" onclick="npcPortraitGenerate()">🎨 Generate</button>
          <button type="button" class="btn btn-outline btn-sm" onclick="npcPortraitClear()">✕ Remove</button>
          <span style="font-size:0.7rem;color:var(--text-muted)">Resized to 1024px on save</span>
        </div>
        <input type="hidden" name="portrait_url" id="npcPortraitValue" value="${npc.portrait_url || ''}">
      </div>
      <div class="form-row">
        <div class="form-group"><label>Name</label><input name="name" value="${npc.name || ''}" required></div>
        <div style="display:flex;gap:1rem;align-items:center;padding-top:1.5rem">
          <label><input type="checkbox" name="is_enemy" ${npc.is_enemy ? 'checked' : ''}> Enemy/Villain</label>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Race</label><select name="race">${races.map(r => `<option value="${r}" ${npc.race === r ? 'selected' : ''}>${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Class</label><select name="class_name">${classes.map(c => `<option value="${c}" ${npc.class_name === c ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Subclass</label><input name="subclass" value="${npc.subclass || ''}"></div>
        <div class="form-group"><label>Level</label><input name="level" type="number" min="1" max="20" value="${npc.level}"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Role</label><select name="role">${roles.map(r => `<option value="${r}" ${npc.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Alignment</label><select name="alignment">${alignments.map(a => `<option value="${a}" ${npc.alignment === a ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Faction</label><input name="faction" value="${npc.faction || ''}"></div>
        <div class="form-group"><label>XP Reward</label><input name="xp_reward" type="number" min="0" value="${npc.xp_reward || 0}"></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.5rem;margin:0.75rem 0">
        ${['strength','dexterity','constitution','intelligence','wisdom','charisma'].map(s => `
          <div class="form-group"><label style="font-size:0.7rem;text-align:center">${s.slice(0,3).toUpperCase()}</label>
          <input name="${s}" type="number" min="1" max="30" value="${npc[s] || 10}" style="text-align:center"></div>
        `).join('')}
      </div>
      <div class="form-row">
        <div class="form-group"><label>HP Max</label><input name="hp_max" type="number" value="${npc.hp_max}" min="1"></div>
        <div class="form-group"><label>HP Current</label><input name="hp_current" type="number" value="${npc.hp_current}" min="0"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>AC</label><input name="ac" type="number" value="${npc.ac}" min="1"></div>
        <div class="form-group"><label>Speed</label><input name="speed" type="number" value="${npc.speed}" min="0"></div>
      </div>
      <div class="form-group"><label>Notes</label><textarea name="notes" rows="3" style="width:100%;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">${npc.notes || ''}</textarea></div>
      <div style="display:flex;gap:0.5rem;margin-top:1rem">
        <button type="submit" class="btn btn-primary">Save NPC</button>
        <button type="button" class="btn btn-outline" onclick="closeModal('npcModal')">Cancel</button>
      </div>
    </form>`;
  document.getElementById('npcEditor').innerHTML = html;
}

async function saveNpc(event, id) {
  event.preventDefault();
  const form = document.getElementById('npcForm');
  const data = Object.fromEntries(new FormData(form));
  data.is_enemy = form.querySelector('[name="is_enemy"]').checked ? true : false;
  data.level = parseInt(data.level) || 1;
  // portrait_url arrives as '' when the NPC has a stored data-URL portrait, so
  // only send it when the DM actually picked or removed an image — otherwise a
  // plain rename would wipe the portrait.
  if (form.dataset.portraitTouched !== '1') delete data.portrait_url;

  const url = id ? `/api/dm/npc/${id}/update` : '/api/dm/npc/create';
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await r.json();
  if (result.ok) { closeModal('npcModal'); location.reload(); }
  else { alert(result.error || ('Could not save the NPC (HTTP ' + r.status + ')')); }
  return false;
}

/* Portrait picker in the NPC editor: downscale in the browser, preview, and
   mark the field as touched so saveNpc sends it (see saveNpc). */
async function npcPortraitGenerate() {
  // Same keyless provider as the character wizard, prompt built server-side from
  // the NPC's own name/notes/race/role (services.portraits.npc_prompt). Nothing
  // is written here: the image lands in the form's hidden field and the NPC is
  // saved — and normalised — by the normal save path.
  const btn = document.getElementById('npcPortraitGenBtn');
  const value = document.getElementById('npcPortraitValue');
  const preview = document.getElementById('npcPortraitPreview');
  const form = document.getElementById('npcForm');
  if (!btn || !form || !value) return;
  const field = name => {
    const el = form.querySelector('[name="' + name + '"]');
    return el ? String(el.value || '').trim() : '';
  };
  const name = field('name');
  if (!name) { alert('Name the NPC first — the portrait prompt uses it.'); return; }
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '🎨 Generating…';
  try {
    const r = await fetch('/api/ai/portrait', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({npc: {name: name, notes: field('notes') || field('description'),
                                 race: field('race'), role: field('role')}})
    });
    const data = await r.json();
    if (!data.image_url) {
      btn.textContent = '⚠️ Failed'; btn.disabled = false;
      alert('Portrait not generated: ' + (data.error || 'the image service did not return a picture'));
      return;
    }
    value.value = data.image_url;
    if (preview) preview.innerHTML = charPortraitTile(null, name, {size: 64, kind: 'npc', hasPortrait: true, src: data.image_url});
    btn.textContent = label; btn.disabled = false;
  } catch (e) {
    btn.textContent = label; btn.disabled = false;
    alert('Portrait failed: ' + (e.message || e));
  }
}

async function npcPortraitPicked(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const preview = document.getElementById('npcPortraitPreview');
  try {
    const dataUrl = await downscaleImageFile(file, 1024);
    document.getElementById('npcPortraitValue').value = dataUrl;
    document.getElementById('npcForm').dataset.portraitTouched = '1';
    if (preview) preview.innerHTML = `<img src="${dataUrl}" alt="portrait preview" class="char-portrait" style="--cp-size:64px;--cp-size-mobile:56px">`;
  } catch (err) {
    alert(err.message || 'Could not read that image');
  }
  input.value = '';
}

function npcPortraitClear() {
  document.getElementById('npcPortraitValue').value = '';
  document.getElementById('npcForm').dataset.portraitTouched = '1';
  const preview = document.getElementById('npcPortraitPreview');
  if (preview) {
    const name = document.querySelector('#npcForm [name="name"]');
    preview.innerHTML = charPortraitTile(null, (name && name.value) || '?', {size: 64});
  }
}

function deleteNpc(id) {
  if (!confirm('Delete this NPC?')) return;
  fetch(`/api/dm/npc/${id}/delete`, {method: 'POST'}).then(r => r.json()).then(() => location.reload());
}

function filterNpcs() {
  const q = document.getElementById('npcSearch').value.toLowerCase();
  document.querySelectorAll('.npc-row').forEach(row => {
    const match = row.dataset.name.includes(q) && _srcOk(row.dataset.source, 'dm-npcs');
    row.style.display = match ? '' : 'none';
  });
}

// ── AI NPC Builder ──
function handleNpcCrChange(sel) {
  const levelInput = document.querySelector('#aiNpcForm input[name=level]');
  const hint = document.getElementById('crLevelHint');
  if (!sel.value) {
    levelInput.disabled = false;
    levelInput.style.opacity = '1';
    hint.style.display = 'none';
    return;
  }
  // CR → level: L ≈ CR*2 + 1
  let cr;
  if (sel.value.includes('/')) {
    const parts = sel.value.split('/');
    cr = parseFloat(parts[0]) / parseFloat(parts[1]);
  } else {
    cr = parseFloat(sel.value);
  }
  const level = Math.min(20, Math.max(1, Math.round(cr * 2) + 1));
  levelInput.value = level;
  levelInput.disabled = true;
  levelInput.style.opacity = '0.6';
  hint.textContent = `→ L${level}`;
  hint.style.display = 'inline';
}

function showAiNpcWizard() {
  openModal('npcModal');
  const races = ['Human','Elf','Dwarf','Halfling','Dragonborn','Gnome','Half-Elf','Half-Orc','Tiefling'];
  const classes = ['Barbarian','Bard','Cleric','Druid','Fighter','Monk','Paladin','Ranger','Rogue','Sorcerer','Warlock','Wizard'];

  let html = `<h2 style="margin:0 0 1rem 0">🤖 AI Build NPC</h2>
    <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:1rem">AI generates name, personality, backstory + full PHB-grounded build.</p>
    <form id="aiNpcForm" onsubmit="return generateAiNpc(event)">
      <div class="form-row">
        <div class="form-group"><label>Race</label><select name="race">${races.map(r => `<option value="${r}">${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Class</label><select name="class_name">${classes.map(c => `<option value="${c}">${c}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Subclass (optional)</label><input name="subclass" placeholder="e.g. Champion, Lore"></div>
        <div class="form-group"><label>Level</label><input name="level" type="number" min="1" max="20" value="5"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Target CR (optional — auto-sets level)</label>
          <select name="target_cr" onchange="handleNpcCrChange(this)">
            <option value="">— Use manual level —</option>
            <option value="0">0</option>
            <option value="1/8">1/8</option>
            <option value="1/4">1/4</option>
            <option value="1/2">1/2</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
            <option value="5">5</option>
            <option value="6">6</option>
            <option value="7">7</option>
            <option value="8">8</option>
            <option value="9">9</option>
            <option value="10">10</option>
            <option value="11">11</option>
            <option value="12">12</option>
            <option value="13">13</option>
            <option value="14">14</option>
            <option value="15">15</option>
            <option value="16">16</option>
            <option value="17">17</option>
            <option value="18">18</option>
            <option value="19">19</option>
            <option value="20">20</option>
          </select>
          <span id="crLevelHint" style="font-size:0.7rem;color:var(--accent);display:none"></span>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Role</label>
          <select name="role"><option value="NPC">Friendly NPC</option><option value="Guard">Guard</option><option value="Bandit">Bandit</option><option value="Cultist">Cultist</option><option value="Merchant">Merchant</option><option value="Villain" selected>Villain</option></select></div>
        <div class="form-group"><label>Type</label>
          <select name="is_enemy"><option value="0">Friendly NPC</option><option value="1" selected>Enemy / Monster</option></select></div>
      </div>
      <div class="form-group"><label>Personality hint (optional)</label><input name="personality_hint" placeholder="e.g. sinister, greedy, wise"></div>
      <div style="display:flex;gap:0.5rem;margin-top:1rem">
        <button type="submit" class="btn btn-primary">🤖 Generate NPC</button>
        <button type="button" class="btn btn-outline" onclick="closeModal('npcModal')">Cancel</button>
      </div>
    </form>`;
  document.getElementById('npcEditor').innerHTML = html;
}

async function generateAiNpc(event) {
  event.preventDefault();
  const form = document.getElementById('aiNpcForm');
  const data = Object.fromEntries(new FormData(form));
  data.is_enemy = data.is_enemy === '1';
  data.level = parseInt(data.level) || 5;

  document.getElementById('npcEditor').innerHTML = '<div style="text-align:center;padding:2rem">🧙 Generating NPC...</div>';

  try {
    const r = await fetch('/api/dm/ai/build-npc', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    const result = await r.json();
    const build = result.build || {};

    // Save to DB
    const saveData = {
      name: result.name || data.race + ' ' + data.role,
      race: data.race,
      class_name: data.class_name,
      subclass: data.subclass || '',
      level: data.level,
      is_enemy: data.is_enemy,
      role: data.role,
      alignment: result.alignment || 'True Neutral',
      faction: result.faction || '',
      notes: (result.personality ? 'Personality: ' + result.personality + '\n' : '') + (result.backstory ? 'Backstory: ' + result.backstory : ''),
      // NOT build.hit_points: that wrote a meaningless XP payout onto every AI-built NPC
      // (a 20-HP NPC became worth 20 XP). The DM sets XP, or the encounter's own budget does.
      xp_reward: 0,
      strength: build.ability_scores?.strength || 10,
      dexterity: build.ability_scores?.dexterity || 10,
      constitution: build.ability_scores?.constitution || 10,
      intelligence: build.ability_scores?.intelligence || 10,
      wisdom: build.ability_scores?.wisdom || 10,
      charisma: build.ability_scores?.charisma || 10,
      hp_max: build.hit_points || 10,
      hp_current: build.hit_points || 10,
      ac: build.armor_class || 10,
      speed: build.speed || 30,
      proficiency_bonus: build.proficiency_bonus || 2,
      hit_dice: build.hit_dice || '1d8',
      skills: build.skills || [],
      features: build.features || [],
      inventory: build.equipment || [],
    };

    const sr = await fetch('/api/dm/npc/create', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(saveData)
    });
    const saveResult = await sr.json();
    if (saveResult.ok) { closeModal('npcModal'); location.reload(); }
  } catch(e) {
    document.getElementById('npcEditor').innerHTML = '<p style="color:var(--danger)">Failed to generate NPC. Try again.</p>';
  }
}

