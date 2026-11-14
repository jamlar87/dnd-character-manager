/* dm/builder.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── NPC → Full Character Builder (Fully Build button) ───────────────────
let _cbState = null;

function buildNpcToCharacter(npcId, npcName) {
  openModal('charBuilderModal');
  document.getElementById('cbTitle').textContent = `🛠️ Building ${npcName}...`;
  document.getElementById('cbBody').innerHTML = '<p style="text-align:center;padding:2rem;color:var(--text-muted)">⏳ Checking NPC data...</p>';
  document.getElementById('cbNav').style.display = 'none';

  fetch(`/api/dm/npc/${npcId}/build-to-character`, {method: 'POST'})
    .then(r => r.json())
    .then(result => {
      if (result.ok && result.character_id) {
        cbShowSuccess(result.character_id, result.name);
        return;
      }
      if (result.gaps && result.gaps.length > 0) {
        cbStartWizard(result.npc_name || npcName, result.gaps, result.npc_data);
        return;
      }
      document.getElementById('cbTitle').textContent = '❌ Build Failed';
      document.getElementById('cbBody').innerHTML = `<p style="color:var(--danger)">${result.error || 'Unknown error'}</p>
        <button class="btn btn-outline" onclick="closeModal('charBuilderModal')">Close</button>`;
    })
    .catch(err => {
      document.getElementById('cbTitle').textContent = '❌ Error';
      document.getElementById('cbBody').innerHTML = `<p style="color:var(--danger)">${err}</p>
        <button class="btn btn-outline" onclick="closeModal('charBuilderModal')">Close</button>`;
    });
}

function cbStartWizard(npcName, gaps, npcData) {
  document.getElementById('cbTitle').textContent = `🛠️ Build ${npcName}`;
  document.getElementById('cbNav').style.display = 'flex';

  const steps = [];
  let cur = null;
  for (const g of gaps) {
    if (g.type === 'race_picker' || g.type === 'class_picker') {
      if (cur) steps.push(cur);
      cur = {label: g.type === 'race_picker' ? 'Race' : 'Class', gaps: [g]};
    } else if (g.type === 'subrace_picker') {
      if (cur && cur.gaps[0].type === 'race_picker') { cur.gaps.push(g); }
      else { if (cur) steps.push(cur); cur = {label: 'Subrace', gaps: [g]}; }
    } else if (g.type === 'subclass_picker') {
      if (cur && cur.gaps[0].type === 'class_picker') { cur.gaps.push(g); }
      else { if (cur) steps.push(cur); cur = {label: 'Subclass', gaps: [g]}; }
    } else {
      if (cur) steps.push(cur);
      cur = {label: 'Abilities', gaps: [g]};
    }
  }
  if (cur) steps.push(cur);

  _cbState = { steps, curStep: 0, gaps, npcData, gapValues: {}, npcName };
  cbRenderStep(0);
}

function cbRenderStep(idx) {
  const st = _cbState;
  if (!st || idx < 0 || idx >= st.steps.length) return;
  st.curStep = idx;
  const step = st.steps[idx];
  document.getElementById('cbBack').style.display = idx > 0 ? '' : 'none';

  const isLast = idx === st.steps.length - 1;
  const nextBtn = document.getElementById('cbNext');
  nextBtn.textContent = isLast ? '🛠️ Build Character' : 'Next →';
  nextBtn.onclick = isLast ? cbBuild : cbNextStep;

  let html = `<div class="cb-desc">Step ${idx + 1} of ${st.steps.length}: <strong>${step.label}</strong></div>`;
  for (const g of step.gaps) {
    html += cbRenderGap(g, st.npcData, st.gapValues[g.field]);
  }
  document.getElementById('cbBody').innerHTML = html;
}

function cbRenderGap(g, npcData, currentValue) {
  const val = currentValue !== undefined ? currentValue : (g.current || '');
  let html = `<div class="cb-gap-card"><div class="cb-gap-label">${g.label}</div>`;
  if (g.note) html += `<div class="cb-gap-note">${g.note}</div>`;

  switch (g.type) {
    case 'race_picker':
    case 'class_picker': {
      const isRace = g.type === 'race_picker';
      const phbOptions = isRace
        ? ['Dwarf','Elf','Halfling','Human','Dragonborn','Gnome','Half-Elf','Half-Orc','Tiefling']
        : ['Barbarian','Bard','Cleric','Druid','Fighter','Monk','Paladin','Ranger','Rogue','Sorcerer','Warlock','Wizard'];
      let cur = val || (isRace ? npcData.race : npcData.class_name) || '';
      if (!phbOptions.includes(cur)) cur = '';
      html += `<select class="cb-picker" onchange="cbSetGap('${g.field}', this.value)">
        <option value="">— Pick —</option>
        ${phbOptions.map(o => `<option value="${o}" ${o === cur ? 'selected' : ''}>${o}</option>`).join('')}
      </select>`;
      break;
    }
    case 'subrace_picker': {
      const subs = g.options || [];
      html += `<select class="cb-picker" onchange="cbSetGap('subrace', this.value)">
        <option value="">— None —</option>
        ${subs.map(s => `<option value="${s}">${s}</option>`).join('')}
      </select>`;
      break;
    }
    case 'subclass_picker': {
      const scOpts = g.options || [];
      html += `<select class="cb-picker" onchange="cbSetGap('subclass', this.value)">
        <option value="">— Skip (no subclass) —</option>
        ${scOpts.map(s => `<option value="${s}">${s}</option>`).join('')}
      </select>`;
      break;
    }
    case 'ability_input': {
      html += `<div class="cb-stat-box"><div class="cb-stat-lbl">${g.label}</div>
        <input type="number" min="3" max="20" value="${val || 10}"
          onchange="cbSetGap('${g.field}', this.value)">
        <div class="cb-gap-note">Standard Array: 15,14,13,12,10,8</div>
      </div>`;
      break;
    }
    case 'asi_picks': {
      const abilities = ['strength','dexterity','constitution','intelligence','wisdom','charisma'];
      html += `<div style="display:flex;flex-wrap:wrap;gap:0.3rem">
        ${abilities.map(a => `<label style="font-size:0.8rem;cursor:pointer;background:var(--bg);padding:0.2rem 0.5rem;border-radius:4px;border:1px solid var(--border)">
          <input type="checkbox" value="${a}" onchange="cbUpdateAsiPicks()"> ${a.slice(0,3).toUpperCase()}
        </label>`).join('')}
      </div>`;
      break;
    }
    default:
      html += `<input class="cb-picker" type="text" value="${val}" onchange="cbSetGap('${g.field}', this.value)">`;
  }
  html += '</div>';
  return html;
}

function cbSetGap(field, value) {
  if (!_cbState) return;
  _cbState.gapValues[field] = value;
}

function cbUpdateAsiPicks() {
  const checked = [...document.querySelectorAll('input[type="checkbox"][value]:checked')]
    .filter(c => ['strength','dexterity','constitution','intelligence','wisdom','charisma'].includes(c.value))
    .map(c => c.value);
  if (_cbState) _cbState.gapValues['asi_picks'] = checked;
}

function cbNextStep() {
  if (!_cbState) return;
  if (_cbState.curStep < _cbState.steps.length - 1) {
    cbRenderStep(_cbState.curStep + 1);
  }
}

function cbPrevStep() {
  if (!_cbState) return;
  if (_cbState.curStep > 0) {
    cbRenderStep(_cbState.curStep - 1);
  }
}

function cbBuild() {
  if (!_cbState) return;
  document.getElementById('cbNav').style.display = 'none';
  document.getElementById('cbBody').innerHTML = '<p style="text-align:center;padding:2rem;color:var(--text-muted)">⏳ Building character...</p>';
  document.getElementById('cbTitle').textContent = '🛠️ Building...';

  fetch('/api/dm/npc/build-from-gaps', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({char_data: _cbState.npcData, gap_values: _cbState.gapValues})
  })
    .then(r => r.json())
    .then(result => {
      if (result.ok && result.character_id) {
        cbShowSuccess(result.character_id, result.name);
      } else {
        document.getElementById('cbTitle').textContent = '❌ Build Failed';
        document.getElementById('cbBody').innerHTML = `<p style="color:var(--danger)">${result.error || 'Unknown error'}</p>
          <button class="btn btn-outline" onclick="closeModal('charBuilderModal')">Close</button>`;
      }
    })
    .catch(err => {
      document.getElementById('cbTitle').textContent = '❌ Error';
      document.getElementById('cbBody').innerHTML = `<p style="color:var(--danger)">${err}</p>
        <button class="btn btn-outline" onclick="closeModal('charBuilderModal')">Close</button>`;
    });
}

function cbShowSuccess(charId, name) {
  document.getElementById('cbTitle').textContent = '✅ Character Built!';
  document.getElementById('cbBody').innerHTML = `
    <div class="cb-result">
      <div class="cb-success-icon">✅</div>
      <h3 style="margin:0.5rem 0">${dmEsc(name)}</h3>
      <p style="color:var(--text-muted)">Successfully built as a full character!</p>
      <div style="display:flex;gap:0.5rem;justify-content:center;margin-top:1rem">
        <button class="btn btn-primary" data-char-action="previewCharSheet" data-char-id="${Number(charId)}" data-char-name="${dmEsc(name)}">📋 View Sheet</button>
        <button class="btn btn-outline" onclick="closeModal('charBuilderModal')">Close</button>
      </div>
    </div>`;
}

