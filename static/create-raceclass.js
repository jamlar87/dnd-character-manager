/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
function buildRaces() {
  const grid = document.getElementById('race-grid');
  let entries = Object.entries(RACES);
  if (_coreOnlyCreate) {
    entries = entries.filter(([name, r]) => isCoreSourceC(r.source));
  }
  grid.innerHTML = entries.map(([name, r]) => `
    <div class="option-card" data-name="${name}" onclick="selectRace('${name}')">
      <div class="name">${name}${r.source ? ` ${_srcBadge(r.source, r._source_slug)}` : ''}</div>
      <div class="detail">${Object.entries(r.asi).map(([k,v])=>'+'+v+' '+k.slice(0,3).toUpperCase()).join(', ') || 'No ASI'}</div>
    </div>`).join('');
}

function selectRace(name) {
  state.race = name; state.subrace = null; state.asi_picks = [];
  state.mpmm_mode = ""; state.mpmm_p2 = ""; state.mpmm_p1 = ""; state.mpmm_t3 = [];
  document.querySelectorAll('#race-grid .option-card').forEach(c => c.classList.toggle('selected', c.dataset.name === name));
  
  const r = RACES[name];
  const subsec = document.getElementById('subrace-section');
  const subgrid = document.getElementById('subrace-grid');
  if (r.subraces && r.subraces.length) {
    subsec.style.display = 'block';
    const srSrcs = r._subrace_sources || {};
    let filteredSubs = r.subraces;
    if (_coreOnlyCreate) filteredSubs = r.subraces.filter(s => isCoreSourceC(srSrcs[s] || r.source || ''));
    subgrid.innerHTML = filteredSubs.map(s => `
      <div class="option-card" data-name="${s}" onclick="selectSubrace('${s}')">
        <div class="name">${s}${(srSrcs[s]||r.source) ? ` ${_srcBadge(srSrcs[s]||r.source)}` : ''}</div>
        <div class="detail">${SUBASIS[s] ? Object.entries(SUBASIS[s]).map(([k,v])=>'+'+v+' '+k.slice(0,3).toUpperCase()).join(', ') : 'No extra ASI'}</div>
      </div>`).join('');
  } else { subsec.style.display = 'none'; }

  document.getElementById('race-info').style.display = 'block';
  document.getElementById('race-info').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
      <span><strong>${name}</strong>${state.subrace ? ' ('+state.subrace+')' : ''}</span>
      ${r.source ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef(this.dataset.src)" data-src="${r.source.replace(/"/g, '&quot;')}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;white-space:nowrap;cursor:pointer" title="Click to open ${r.source}">📚 ${r.source}</span>` : ''}
    </div>
    <div style="font-size:0.85rem;color:var(--text-muted)">Speed ${r.speed}ft · Darkvision ${r.darkvision}ft</div>
    <span style="font-size:0.85rem;color:var(--text-muted)">${r.traits.join(', ') || 'No special traits'}</span>
    ${state.subrace && r.subrace_descs && r.subrace_descs[state.subrace] ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5;color:var(--accent)"><strong>${state.subrace}:</strong> ${r.subrace_descs[state.subrace]}</p>` : ''}
    ${r.desc ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5">${r.desc}</p>` : ''}`;
  
  // Dragonborn ancestry picker
  const ancestryDiv = document.getElementById('ancestry-section');
  if (name === 'Dragonborn' || name === 'Dragonblood') {
    ancestryDiv.style.display = 'block';
    buildAncestryDropdown();
  } else {
    ancestryDiv.style.display = 'none';
    state.dragonborn_ancestry = '';
  }

  updateAsiPreview();
}

function selectSubrace(s) {
  if (state.subrace !== s) {   // the MPMM entry can be the subrace, so its picks are per-subrace
    state.mpmm_mode = ""; state.mpmm_p2 = ""; state.mpmm_p1 = ""; state.mpmm_t3 = [];
  }
  state.subrace = s;
  document.querySelectorAll('#subrace-grid .option-card').forEach(c => c.classList.toggle('selected', c.dataset.name === s));
  // Refresh race info without resetting subrace
  const r = RACES[state.race];
  const detailSrc = (r._subrace_sources && r._subrace_sources[s]) || r.source || '';
  document.getElementById('race-info').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
      <span><strong>${state.race}</strong>${state.subrace ? ' ('+state.subrace+')' : ''}</span>
      ${detailSrc ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef(this.dataset.src)" data-src="${detailSrc.replace(/"/g, '&quot;')}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;white-space:nowrap;cursor:pointer" title="Click to open ${detailSrc}">📚 ${detailSrc}</span>` : ''}
    </div>
    <div style="font-size:0.85rem;color:var(--text-muted)">Speed ${r.speed}ft · Darkvision ${r.darkvision}ft</div>
    <span style="font-size:0.85rem;color:var(--text-muted)">${r.traits.join(', ') || 'No special traits'}</span>
    ${r.subrace_descs && r.subrace_descs[s] ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5;color:var(--accent)"><strong>${s}:</strong> ${r.subrace_descs[s]}</p>` : ''}
    ${r.desc ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5">${r.desc}</p>` : ''}`;
  updateAsiPreview();
}

function buildAncestryDropdown() {
  const div = document.getElementById('ancestry-section');
  div.innerHTML = `
    <label style="font-size:0.8rem;color:var(--text-muted);display:block;margin-bottom:0.2rem">Draconic Ancestry (determines resistance & breath weapon)</label>
    <select id="ancestry-pick" onchange="state.dragonborn_ancestry=this.value"
            style="font-size:0.8rem;padding:0.25rem 0.5rem;background:var(--bg);color:var(--text);border:1px solid var(--border-color);border-radius:4px">
      <option value="">— Choose ancestry —</option>
      ${Object.entries(DRACONIC_ANCESTRIES).map(([color, data]) => 
        `<option value="${color}" ${state.dragonborn_ancestry === color ? 'selected' : ''}>${color} Dragon — ${data.resist} resist, ${data.shape} (${data.save} save)</option>`
      ).join('')}
    </select>`;
}

// ── Step 1: Class ──
function buildClasses() {
  const grid = document.getElementById('class-grid');
  let entries = Object.entries(CLASSES);
  if (_coreOnlyCreate) {
    entries = entries.filter(([name, c]) => isCoreSourceC(c.source));
  }
  grid.innerHTML = entries.map(([name, c]) => `
    <div class="option-card" data-name="${name}" onclick="selectClass('${name}')">
      <div class="name">${name}${c.source ? ` ${_srcBadge(c.source)}` : ''}</div>
      <div class="detail">d${c.hd} HD · ${c.skill_count} skills · Saves: ${c.saves.join(', ')}</div>
    </div>`).join('');
}

function selectClass(name) {
  const changed = state.class_name !== name;
  state.class_name = name; state.subclass = null; state.subclass_bonus = [];
  if (changed) { state.spells = []; state._spellsDone = false; state._quota = null; }
  prefetchSpellQuota();
  document.querySelectorAll('#class-grid .option-card').forEach(c => c.classList.toggle('selected', c.dataset.name === name));
  const c = CLASSES[name];
  const scgrid = document.getElementById('subclass-grid');
  const scsec = document.getElementById('subclass-section');
  if (c.subclasses && c.subclasses.length) {
    scsec.style.display = 'block';
    const scSrcs = c._subclass_sources || {};
    let filteredScs = c.subclasses;
    if (_coreOnlyCreate) filteredScs = c.subclasses.filter(s => isCoreSourceC(scSrcs[s] || c.source || ''));
    scgrid.innerHTML = filteredScs.map(s => `
      <div class="option-card" data-name="${s}" onclick="selectSubclass('${s}')">
        <div class="name">${s}${scSrcs[s] ? ` ${_srcBadge(scSrcs[s])}` : ''}</div>
      </div>`).join('');
  } else { scsec.style.display = 'none'; }

  document.getElementById('class-info').style.display = 'block';
  document.getElementById('class-info').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
      <span><strong>${name}</strong>${state.subclass ? ' — '+state.subclass : ''}</span>
      ${c.source ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef(this.dataset.src)" data-src="${c.source.replace(/"/g, '&quot;')}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;white-space:nowrap;cursor:pointer" title="Click to open ${c.source}">📚 ${c.source}</span>` : ''}
    </div>
    <div style="font-size:0.85rem;color:var(--text-muted)">Hit Die: d${c.hd} · Saving Throws: ${c.saves.join(', ')}</div>
    <span style="font-size:0.85rem;color:var(--text-muted)">${c.skill_count} skill proficiencies from: ${Array.isArray(c.skills) ? c.skills.join(', ') : 'any'}</span>
    ${c.desc ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5">${c.desc}</p>` : ''}`;
}

function selectSubclass(s) {
  state.subclass = s;
  document.querySelectorAll('#subclass-grid .option-card').forEach(c => c.classList.toggle('selected', c.dataset.name === s));
  // Refresh class info without resetting subclass
  const c = CLASSES[state.class_name];
  const detailSrc = (c._subclass_sources && c._subclass_sources[s]) || c.source || '';
  document.getElementById('class-info').innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">
      <span><strong>${state.class_name}</strong>${state.subclass ? ' — '+state.subclass : ''}</span>
      ${detailSrc ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef(this.dataset.src)" data-src="${detailSrc.replace(/"/g, '&quot;')}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;white-space:nowrap;cursor:pointer" title="Click to open ${detailSrc}">📚 ${detailSrc}</span>` : ''}
    </div>
    <div style="font-size:0.85rem;color:var(--text-muted)">Hit Die: d${c.hd} · Saving Throws: ${c.saves.join(', ')}</div>
    <span style="font-size:0.85rem;color:var(--text-muted)">${c.skill_count} skill proficiencies from: ${Array.isArray(c.skills) ? c.skills.join(', ') : 'any'}</span>
    ${c.subclass_descs && c.subclass_descs[s] ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5;color:var(--accent)"><strong>${s}:</strong> ${c.subclass_descs[s]}</p>` : ''}
    ${c.desc ? `<p style="font-size:0.85rem;margin-top:0.5rem;line-height:1.5">${c.desc}</p>` : ''}`;
}

// ── Step 2: Abilities ──
