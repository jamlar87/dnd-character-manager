/* sheet/features.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Feature Editing ──
const featureData = SHEETCFG.charFeatureData;

// ── ASI / Feat re-picker (edit mode) ──
let _asiEditLevel = null;

function startAsiEdit(level) {
  _asiEditLevel = level;
  const entry = CHAR_ASI_HISTORY.find(e => e.level === level);
  const abilities = ['Strength','Dexterity','Constitution','Intelligence','Wisdom','Charisma'];
  
  document.getElementById('asi-edit-level').textContent = `Level ${level} — Ability Score Improvement`;
  
  let isFeat = entry && entry.type === 'feat';
  let curPlus2 = '', curPlus1 = '', curFeat = '';
  
  if (entry) {
    if (isFeat) {
      curFeat = entry.feat || '';
    } else {
      for (const [ab, inc] of Object.entries(entry)) {
        if (ab === 'type' || ab === 'level') continue;
        if (inc >= 2) curPlus2 = ab;
        else if (inc >= 1) curPlus1 = ab;
      }
    }
  }
  
  let html = `<div style="margin-bottom:1rem">
    <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;padding:0.5rem;border:1px solid ${!isFeat ? 'var(--accent)' : 'var(--border)'};border-radius:6px;margin-bottom:0.5rem" onclick="document.getElementById('asi-edit-feat').style.display='none';document.getElementById('asi-edit-abilities').style.display='';this.style.borderColor='var(--accent)'">
      <input type="radio" name="asi-type" ${!isFeat ? 'checked' : ''} onchange="document.getElementById('asi-edit-feat').style.display='none';document.getElementById('asi-edit-abilities').style.display=''"> 
      <strong>Ability Score Increase</strong> (+2 / +1)
    </label>
    <div id="asi-edit-abilities" style="${isFeat ? 'display:none' : ''};padding:0.5rem;background:var(--bg);border-radius:6px">
      <div class="form-group"><label>+2 to:</label>
        <select id="asi-edit-plus2" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">— Choose —</option>
          ${abilities.map(a => `<option value="${a}" ${curPlus2 === a ? 'selected' : ''}>${a}</option>`).join('')}
        </select>
      </div>
      <div class="form-group"><label>+1 to:</label>
        <select id="asi-edit-plus1" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">— Choose —</option>
          ${abilities.map(a => `<option value="${a}" ${curPlus1 === a ? 'selected' : ''}>${a}</option>`).join('')}
        </select>
      </div>
    </div>
    
    <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;padding:0.5rem;border:1px solid ${isFeat ? 'var(--accent)' : 'var(--border)'};border-radius:6px" onclick="document.getElementById('asi-edit-abilities').style.display='none';document.getElementById('asi-edit-feat').style.display='';this.style.borderColor='var(--accent)'">
      <input type="radio" name="asi-type" ${isFeat ? 'checked' : ''} onchange="document.getElementById('asi-edit-abilities').style.display='none';document.getElementById('asi-edit-feat').style.display=''"> 
      <strong>Take a Feat</strong>
    </label>
    <div id="asi-edit-feat" style="${isFeat ? '' : 'display:none'};padding:0.5rem;background:var(--bg);border-radius:6px">
      <div class="form-group"><label>Search feats:</label>
        <input id="asi-edit-feat-search" type="text" placeholder="Type to filter feats..." style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);margin-bottom:0.5rem"
          oninput="filterAsiFeats()">
      </div>
      <div id="asi-edit-feat-list" style="max-height:260px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">
        ${KNOWN_FEATS.map(f => {
          const d = FEAT_DETAILS[f] || {};
          const desc = d.desc || '';
          const prereq = d.prereq || '';
          const src = d.source || '';
          const sel = curFeat === f ? 'selected' : '';
          return `<div class="asi-feat-option ${sel}" data-feat="${f.replace(/"/g,'&quot;')}" onclick="selectAsiFeat(this,'${f.replace(/'/g,"\\'")}')"
            style="padding:0.5rem 0.6rem;cursor:pointer;border-bottom:1px solid var(--border);transition:background 0.1s;${sel ? 'background:var(--accent2);border-left:3px solid var(--accent);' : ''}"
            onmouseenter="if(!this.classList.contains('selected'))this.style.background='rgba(255,255,255,0.03)'"
            onmouseleave="if(!this.classList.contains('selected'))this.style.background=''">
            <div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.5rem">
              <strong style="font-size:0.85rem">${f}</strong>
              <span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">${src}</span>
            </div>
            ${prereq ? `<div style="font-size:0.7rem;color:var(--accent);margin-top:0.15rem">Prerequisite: ${prereq}</div>` : ''}
            ${desc ? `<div style="font-size:0.72rem;color:var(--text-muted);margin-top:0.2rem;line-height:1.35">${desc.length > 200 ? desc.slice(0,200)+'…' : desc}</div>` : ''}
          </div>`;
        }).join('')}
      </div>
      <input type="hidden" id="asi-edit-feat-select" value="${curFeat}">
      <p style="font-size:0.7rem;color:var(--text-muted);margin-top:0.5rem">Switching to a feat replaces your ASI for this level.</p>
    </div>
  </div>`;
  
  document.getElementById('asi-edit-body').innerHTML = html;
  document.getElementById('asi-edit-modal').style.display = 'flex';
}

function closeAsiEdit() {
  document.getElementById('asi-edit-modal').style.display = 'none';
  _asiEditLevel = null;
}

// ── Expertise Edit ──
let _expPicks = [];

function startExpertiseEdit() {
  _expPicks = [...CHAR_EXPERTISE];
  const count = EXPERTISE_COUNT;
  document.getElementById('exp-edit-count').textContent = count;
  
  let html = '';
  EXPERTISE_OPTIONS.forEach(opt => {
    const sel = _expPicks.includes(opt);
    html += `<div class="exp-opt-row" data-opt="${opt.replace(/"/g,'&quot;')}" onclick="toggleExpPick(this,'${opt.replace(/'/g,"\\'")}')"
      style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;border:1px solid ${sel ? 'var(--accent)' : 'var(--border)'};border-radius:6px;cursor:pointer;transition:border-color 0.15s;${sel ? 'background:var(--accent2)' : ''}">
      <span style="font-size:0.85rem;flex:1">${opt}</span>
      <span class="exp-check" style="font-size:0.9rem">${sel ? '✅' : '⬜'}</span>
    </div>`;
  });
  document.getElementById('exp-edit-body').innerHTML = html || '<p style="color:var(--text-muted);font-size:0.8rem">No expertise options available for this class.</p>';
  _updateExpCount();
  document.getElementById('exp-edit-modal').style.display = 'flex';
}

function toggleExpPick(el, opt) {
  const idx = _expPicks.indexOf(opt);
  if (idx >= 0) {
    _expPicks.splice(idx, 1);
    el.style.borderColor = 'var(--border)';
    el.style.background = '';
    el.querySelector('.exp-check').textContent = '⬜';
  } else {
    if (_expPicks.length >= EXPERTISE_COUNT) {
      // Remove first pick to make room
      const old = _expPicks.shift();
      const oldEl = document.querySelector(`.exp-opt-row[data-opt="${old.replace(/"/g,'&quot;')}"]`);
      if (oldEl) {
        oldEl.style.borderColor = 'var(--border)';
        oldEl.style.background = '';
        oldEl.querySelector('.exp-check').textContent = '⬜';
      }
    }
    _expPicks.push(opt);
    el.style.borderColor = 'var(--accent)';
    el.style.background = 'var(--accent2)';
    el.querySelector('.exp-check').textContent = '✅';
  }
  _updateExpCount();
}

function _updateExpCount() {
  document.getElementById('exp-edit-selected').textContent = `${_expPicks.length} / ${EXPERTISE_COUNT} selected`;
}

function closeExpertiseEdit() {
  document.getElementById('exp-edit-modal').style.display = 'none';
  _expPicks = [];
}

async function saveExpertiseEdit() {
  try {
    await sheetApi('/edit-expertise', { expertise_skills: _expPicks });
    location.reload();
  } catch(e) { alert('Failed: ' + e.message); }
}

// ── Feat browser helpers ──
function filterAsiFeats() {
  const q = (document.getElementById('asi-edit-feat-search')?.value || '').toLowerCase();
  document.querySelectorAll('.asi-feat-option').forEach(el => {
    const name = (el.getAttribute('data-feat') || '').toLowerCase();
    el.style.display = !q || name.includes(q) ? '' : 'none';
  });
}
function selectAsiFeat(el, name) {
  document.querySelectorAll('.asi-feat-option').forEach(o => {
    o.classList.remove('selected');
    o.style.background = '';
    o.style.borderLeft = '';
  });
  el.classList.add('selected');
  el.style.background = 'var(--accent2)';
  el.style.borderLeft = '3px solid var(--accent)';
  document.getElementById('asi-edit-feat-select').value = name;
}

async function saveAsiEdit() {
  const isFeat = document.querySelector('input[name="asi-type"]:checked')?.parentElement?.textContent.includes('Feat');
  let entry = {level: _asiEditLevel};
  
  if (isFeat) {
    const feat = document.getElementById('asi-edit-feat-select')?.value;
    if (!feat) { alert('Please select a feat.'); return; }
    entry.type = 'feat';
    entry.feat = feat;
  } else {
    const plus2 = document.getElementById('asi-edit-plus2')?.value;
    const plus1 = document.getElementById('asi-edit-plus1')?.value;
    if (!plus2 || !plus1) { alert('Please select both +2 and +1 abilities.'); return; }
    if (plus2 === plus1) { alert('+2 and +1 must be different abilities.'); return; }
    entry.type = 'asi';
    entry[plus2.toLowerCase()] = 2;
    entry[plus1.toLowerCase()] = 1;
  }
  
  try {
    await sheetApi('/edit-asi', {level: _asiEditLevel, entry});
    location.reload();
  } catch(e) { alert('Failed to save: ' + e.message); }
}

// ── Collapse Toggle for Feature Descriptions ──
function toggleFeatBody(card) {
  // In edit mode, ASI cards open the re-picker
  if (document.body.classList.contains('edit-mode')) {
    const idx = parseInt(card.getAttribute('data-feat-index'));
    const feat = featureData[idx];
    if (feat && feat.name && feat.name.includes('Ability Score Improvement')) {
      const lvlStr = feat.level ? String(feat.level).replace('L','') : '';
      const lvl = parseInt(lvlStr);
      if (lvl) { startAsiEdit(lvl); return; }
    }
    if (feat && feat.name === 'Expertise') {
      startExpertiseEdit(); return;
    }
  }
  const body = card.querySelector('.feat-body');
  const arrow = card.querySelector('.collapse-arrow');
  body.classList.toggle('open');
  if (arrow) arrow.classList.toggle('open');
}
function togglePassiveBody(chip) {
  // In edit mode, ASI cards open the re-picker
  if (document.body.classList.contains('edit-mode')) {
    const idx = parseInt(chip.getAttribute('data-feat-index'));
    const feat = featureData[idx];
    if (feat && feat.name && feat.name.includes('Ability Score Improvement')) {
      const lvlStr = feat.level ? String(feat.level).replace('L','') : '';
      const lvl = parseInt(lvlStr);
      if (lvl) { startAsiEdit(lvl); return; }
    }
    if (feat && feat.name === 'Expertise') {
      startExpertiseEdit(); return;
    }
  }
  const body = chip.nextElementSibling;
  const arrow = chip.querySelector('.collapse-arrow');
  if (body && body.classList.contains('passive-chip-body')) {
    body.classList.toggle('open');
    if (arrow) arrow.classList.toggle('open');
  }
}

function editFeature(idx) {
  if (!document.body.classList.contains('edit-mode')) return;
  const feat = featureData[idx];
  if (!feat) return;
  const card = document.querySelector(`.feat-card[data-feat-index="${idx}"]`);
  const body = card.querySelector('.feat-body');
  const current = feat.description || '';
  
  if (body.querySelector('textarea')) return; // already editing
  body.innerHTML = `<textarea id="feat-edit-${idx}" style="width:100%;min-height:80px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:0.5rem;border-radius:4px;font-family:var(--font);font-size:0.85rem">${current.replace(/`/g,'\\`').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</textarea>
    <div style="margin-top:0.3rem;display:flex;gap:0.3rem">
      <button class="btn btn-primary btn-sm" onclick="saveFeature(${idx})">Save</button>
      <button class="btn btn-outline btn-sm" onclick="cancelFeature(${idx})">Cancel</button>
    </div>`;
}

async function saveFeature(idx) {
  const ta = document.getElementById(`feat-edit-${idx}`);
  if (!ta) return;
  const val = ta.value.trim();
  featureData[idx].description = val;
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
  // If feature has sub_options, reload to re-render; otherwise update inline
  if (featureData[idx].sub_options && featureData[idx].sub_options.length) {
    location.reload();
  } else {
    const card = document.querySelector(`.feat-card[data-feat-index="${idx}"]`);
    card.querySelector('.feat-body').textContent = val || 'No description available.';
  }
}

function cancelFeature(idx) {
  const feat = featureData[idx];
  // If feature has sub_options, reload to re-render; otherwise restore inline
  if (feat.sub_options && feat.sub_options.length) {
    location.reload();
  } else {
    const card = document.querySelector(`.feat-card[data-feat-index="${idx}"]`);
    card.querySelector('.feat-body').textContent = feat.description || 'No description available.';
  }
}

// ── Magic Initiate feat ──────────────────────────────────────────────
async function toggleMagicInitiateUse(level) {
  await fetch('/api/character/' + CHAR_ID + '/magic-initiate/use', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'
  });
  location.reload();
}

function setupMagicInitiate(level) {
  // Build a modal to configure Magic Initiate
  const classes = ['Bard','Cleric','Druid','Sorcerer','Warlock','Wizard'];
  let html = `<div id="mi-modal" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)this.remove()">
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:1.5rem;max-width:500px;width:90%;max-height:80vh;overflow-y:auto" onclick="event.stopPropagation()">
      <h3 style="margin:0 0 1rem">Magic Initiate — Level ${level}</h3>
      <label style="font-weight:600;display:block;margin-bottom:0.3rem">Choose a class:</label>
      <select id="mi-class" style="width:100%;padding:0.4rem;margin-bottom:1rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">`;
  classes.forEach(c => { html += `<option value="${c}">${c}</option>`; });
  html += `</select>
      <div id="mi-spells" style="margin-bottom:1rem"><p style="color:var(--text-muted);font-size:0.8rem">Select a class first to load spells</p></div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end">
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('mi-modal').remove()">Cancel</button>
        <button class="btn btn-primary btn-sm" onclick="saveMagicInitiate(${level})">Save</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  // Load spells for default class
  document.getElementById('mi-class').onchange = () => loadMISpells();
  loadMISpells();
}

async function loadMISpells() {
  const cls = document.getElementById('mi-class').value;
  const container = document.getElementById('mi-spells');
  container.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem">Loading spells...</p>';
  try {
    const resp = await fetch('/api/spells/starting?class_name=' + cls + '&level=1');
    const spells = await resp.json();
    const cantrips = spells.filter(s => s.level === 0);
    const l1spells = spells.filter(s => s.level === 1);
    let html = '';
    if (cantrips.length) {
      html += '<p style="font-weight:600;margin-bottom:0.3rem;font-size:0.85rem">Cantrips <span style="font-weight:400;color:var(--text-muted);font-size:0.75rem">(pick 2)</span></p>';
      cantrips.forEach(s => {
        html += `<label style="display:block;font-size:0.8rem;margin:0.1rem 0;cursor:pointer"><input type="checkbox" class="mi-cantrip" value="${s.name}" onchange="limitMICantrips()"> ${s.name}</label>`;
      });
    }
    if (l1spells.length) {
      html += '<p style="font-weight:600;margin:0.5rem 0 0.3rem;font-size:0.85rem">1st-level spell <span style="font-weight:400;color:var(--text-muted);font-size:0.75rem">(pick 1)</span></p>';
      html += '<select id="mi-l1-spell" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)"><option value="">— Choose —</option>';
      l1spells.forEach(s => {
        html += `<option value="${s.name}">${s.name}</option>`;
      });
      html += '</select>';
    }
    container.innerHTML = html || '<p style="color:var(--warn);font-size:0.8rem">No spells found for this class</p>';
  } catch(e) {
    container.innerHTML = '<p style="color:var(--danger);font-size:0.8rem">Failed to load spells</p>';
  }
}

function limitMICantrips() {
  const boxes = document.querySelectorAll('.mi-cantrip:checked');
  if (boxes.length > 2) {
    boxes[boxes.length - 1].checked = false;
  }
}

async function saveMagicInitiate(level) {
  const cls = document.getElementById('mi-class').value;
  const cantrips = Array.from(document.querySelectorAll('.mi-cantrip:checked')).map(cb => cb.value);
  const spell = document.getElementById('mi-l1-spell')?.value || '';
  if (!cls) { alert('Choose a class'); return; }
  if (cantrips.length < 2) { alert('Pick 2 cantrips'); return; }
  if (!spell) { alert('Pick a 1st-level spell'); return; }
  
  const resp = await fetch('/api/character/' + CHAR_ID + '/magic-initiate', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({class: cls, cantrips, spell})
  });
  const data = await resp.json();
  if (data.ok) {
    document.getElementById('mi-modal').remove();
    location.reload();
  } else {
    alert(data.error || 'Failed to save');
  }
}

// ── Generic feat config setup (Elemental Adept, Ley Initiate, etc.) ──
function setupFeatConfig(featName, level) {
  const key = featName.toLowerCase().replace(/ /g, '_');
  const setup = FEAT_SETUP[key];
  if (!setup) return;

  // Martial Adept: multi-select maneuver picker
  if (setup.kind === 'maneuvers') {
    setupMartialAdeptModal(featName, level, setup);
    return;
  }

  let opts = '';
  setup.choices.forEach(c => { opts += `<option value="${c}">${c}</option>`; });
  const html = `<div id="fc-modal" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)this.remove()">
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:1.5rem;max-width:400px;width:90%" onclick="event.stopPropagation()">
      <h3 style="margin:0 0 1rem">${featName} — Level ${level}</h3>
      <label style="font-weight:600;display:block;margin-bottom:0.3rem">${setup.label}:</label>
      <select id="fc-choice" style="width:100%;padding:0.4rem;margin-bottom:1rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">${opts}</select>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end">
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('fc-modal').remove()">Cancel</button>
        <button class="btn btn-primary btn-sm" onclick="saveFeatConfig('${key}','${featName.replace(/'/g,"\\'")}','${setup.field}')">Save</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

function setupMartialAdeptModal(featName, level, setup) {
  const key = featName.toLowerCase().replace(/ /g, '_');
  window._ma_picks = [];
  window._ma_max = setup.picks;
  let manList = '';
  for (const [k, v] of Object.entries(MANEUVER_OPTIONS)) {
    manList += `<div class="ma-man-opt card" data-key="${k}" style="cursor:pointer;padding:0.4rem 0.6rem;margin-bottom:0.25rem;border:1px solid var(--border);border-radius:6px;font-size:0.85rem"
      onclick="toggleMaManeuver(this,'${k}')">
      <strong>${v.name}</strong><br><span style="font-size:0.75rem;color:var(--text-muted)">${v.desc}</span>
    </div>`;
  }
  const html = `<div id="fc-modal" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)this.remove()">
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:1.5rem;max-width:500px;width:90%;max-height:80vh;overflow-y:auto" onclick="event.stopPropagation()">
      <h3 style="margin:0 0 0.5rem">${featName} — Level ${level}</h3>
      <p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.75rem">${setup.label} (<span id="ma-count">0</span> / ${setup.picks} selected)</p>
      <div id="ma-maneuver-list">${manList}</div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem">
        <button class="btn btn-outline btn-sm" onclick="document.getElementById('fc-modal').remove()">Cancel</button>
        <button class="btn btn-primary btn-sm" id="ma-save-btn" disabled onclick="saveMaConfig('${key}','${featName.replace(/'/g,"\\'")}')">Save</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

function toggleMaManeuver(el, manKey) {
  const idx = window._ma_picks.indexOf(manKey);
  if (idx >= 0) {
    window._ma_picks.splice(idx, 1);
    el.style.border = '1px solid var(--border)';
    el.style.background = '';
  } else {
    if (window._ma_picks.length >= window._ma_max) return;
    window._ma_picks.push(manKey);
    el.style.border = '1px solid var(--accent)';
    el.style.background = 'rgba(255,215,0,0.08)';
  }
  const countEl = document.getElementById('ma-count');
  if (countEl) countEl.textContent = window._ma_picks.length;
  const saveBtn = document.getElementById('ma-save-btn');
  if (saveBtn) saveBtn.disabled = window._ma_picks.length === 0;
}

async function saveMaConfig(key, featName) {
  const config = {maneuvers: window._ma_picks};
  const resp = await fetch('/api/character/' + CHAR_ID + '/feat-config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({feat: key, config})
  });
  const data = await resp.json();
  if (data.ok) {
    document.getElementById('fc-modal').remove();
    location.reload();
  } else {
    alert(data.error || 'Failed to save');
  }
}

async function saveFeatConfig(key, featName, field) {
  const choice = document.getElementById('fc-choice').value;
  const config = {};
  config[field] = choice;
  const resp = await fetch('/api/character/' + CHAR_ID + '/feat-config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({feat: key, config})
  });
  const data = await resp.json();
  if (data.ok) {
    document.getElementById('fc-modal').remove();
    location.reload();
  } else {
    alert(data.error || 'Failed to save');
  }
}

// ── Limited-Use Features (Channel Divinity, Rage, etc.) — toggle dots ──
async function toggleFeatureUse(idx, dotIdx) {
  const feat = featureData[idx];
  if (!feat || !feat.uses_max) return;
  // dotIdx < feat.uses means the dot is currently filled (used) → click restores
  // dotIdx >= feat.uses means the dot is empty → click expends
  if (dotIdx < feat.uses) {
    // Restore this use: decrease uses
    feat.uses--;
  } else {
    // Expend this use: increase uses
    feat.uses++;
  }
  renderFeatureDots(idx);
  // Sync actions tab button
  if (feat.action_type) syncActionButton(feat.name, feat.uses, feat.uses_max);
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
}

function renderFeatureDots(idx) {
  const feat = featureData[idx];
  if (!feat) return;
  const box = document.getElementById(`feat-uses-box-${idx}`);
  if (box) {
    box.querySelectorAll('.feat-use-dot').forEach((dot, i) => {
      dot.classList.toggle('filled', i < feat.uses);
      dot.title = i < feat.uses ? 'Available — click to expend' : 'Used — click to restore';
    });
  }
  // Update pool HP display
  const hpEl = document.getElementById(`feat-pool-hp-${idx}`);
  if (hpEl) hpEl.textContent = feat.uses;
  // Update text counter
  const text = document.getElementById(`feat-uses-text-${idx}`);
  if (text) {
    if (feat.pool_kind) {
      const label = feat.pool_kind === 'hp' ? 'HP' : feat.pool_kind === 'points' ? 'pts' : 'dice';
      text.textContent = `max ${feat.uses_max} ${label}`;
    } else {
      text.textContent = `${feat.uses}/${feat.uses_max}`;
    }
  }
  // Toggle depleted state on the feature card
  const card = document.querySelector(`.feat-card[data-feat-index="${idx}"]`);
  if (card) card.classList.toggle('depleted', feat.uses === 0 && feat.uses_max > 0);
}

// ── Pool features (Lay on Hands, Ki, Sorcery Points, Superiority Dice, etc.) ──
async function spendPool(idx) {
  const feat = featureData[idx];
  if (!feat || !feat.pool_kind) return;
  const input = document.getElementById(`feat-pool-spend-${idx}`);
  const amount = parseInt(input.value) || 0;
  if (amount <= 0) return;
  if (amount > feat.uses) { input.value = feat.uses; return; }
  feat.uses -= amount;
  input.max = feat.uses;
  if (parseInt(input.value) > feat.uses) input.value = feat.uses || 1;
  renderFeatureDots(idx);
  if (feat.action_type) syncActionButton(feat.name, feat.uses, feat.uses_max);
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
}

async function restorePool(idx) {
  const feat = featureData[idx];
  if (!feat || !feat.pool_kind) return;
  feat.uses = feat.uses_max;
  const input = document.getElementById(`feat-pool-spend-${idx}`);
  if (input) input.max = feat.uses;
  renderFeatureDots(idx);
  if (feat.action_type) syncActionButton(feat.name, feat.uses, feat.uses_max);
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
}

function resetAllFeatureUses() {
  featureData.forEach((feat, i) => {
    if (feat.uses_max) {
      feat.uses = feat.uses_max;
      renderFeatureDots(i);
    }
  });
  // Sync actions tab buttons
  featureData.forEach(f => { if (f.action_type) syncActionButton(f.name, f.uses, f.uses_max); });
}

// ── Sync Actions tab button when Features tab toggles uses ──
function syncActionButton(name, uses, usesMax) {
  const btn = document.querySelector(`.feat-action-btn[data-feat-name="${name}"]`);
  if (!btn) return;
  const poolKind = btn.dataset.poolKind;
  if (poolKind) {
    // Pool feature: update value display and input max
    const poolVal = btn.querySelector('.feat-pool-val');
    if (poolVal) poolVal.textContent = uses;
    const poolMax = btn.querySelector('.feat-pool-max');
    const label = poolKind === 'hp' ? 'HP' : poolKind === 'points' ? 'pts' : 'dice';
    if (poolMax) poolMax.textContent = `max ${usesMax} ${label}`;
    const spendInput = btn.querySelector('input[type="number"]');
    if (spendInput) {
      spendInput.max = uses;
      if (parseInt(spendInput.value) > uses) spendInput.value = uses || 1;
    }
    // Depleted when pool is empty
    if (uses === 0 && usesMax > 0) {
      btn.classList.add('depleted');
      btn.style.opacity = '0.45';
    } else {
      btn.classList.remove('depleted');
      btn.style.opacity = '';
    }
    return;
  }
  // Update dots
  const dots = btn.querySelectorAll('.use-dot');
  dots.forEach((dot, i) => dot.classList.toggle('active', i < uses));
  // Update count text
  const countSpan = btn.querySelector('.feat-uses-dots span:last-child');
  if (countSpan) countSpan.textContent = `${uses}/${usesMax}`;
  // Toggle depleted state
  if (uses === 0 && usesMax > 0) {
    btn.classList.add('depleted');
    btn.style.opacity = '0.45';
    btn.style.cursor = 'default';
    btn.onclick = null;
  } else {
    btn.classList.remove('depleted');
    btn.style.opacity = '';
    btn.style.cursor = 'pointer';
    btn.onclick = function() { useFeature(name, btn); };
  }
}

// ── Pool features in Actions tab (spend/restore by name) ──
async function spendPoolAction(name, inputEl) {
  const feat = featureData.find(f => f.name === name);
  if (!feat || !feat.pool_kind) return;
  const amount = parseInt(inputEl.value) || 0;
  if (amount <= 0) return;
  if (amount > feat.uses) { inputEl.value = feat.uses; return; }
  feat.uses -= amount;
  inputEl.max = feat.uses;
  if (parseInt(inputEl.value) > feat.uses) inputEl.value = feat.uses || 1;
  // Update this action button in-place
  syncActionButton(name, feat.uses, feat.uses_max);
  // Update features tab
  const idx = featureData.indexOf(feat);
  if (idx >= 0) renderFeatureDots(idx);
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
}

async function restorePoolAction(name) {
  const feat = featureData.find(f => f.name === name);
  if (!feat || !feat.pool_kind) return;
  feat.uses = feat.uses_max;
  syncActionButton(name, feat.uses, feat.uses_max);
  const idx = featureData.indexOf(feat);
  if (idx >= 0) renderFeatureDots(idx);
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });
}

// Reset only short-rest features (for short rest action)
function resetShortRestFeatures() {
  featureData.forEach((feat, i) => {
    if (feat.uses_max && feat.recharge === 'short') {
      feat.uses = feat.uses_max;
      renderFeatureDots(i);
    }
  });
}

