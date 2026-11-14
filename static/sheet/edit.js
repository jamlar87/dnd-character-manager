/* sheet/edit.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Edit Mode Toggle ──
function toggleEditMode() {
  const isCurrentlyEdit = document.body.classList.contains('edit-mode');
  if (!isCurrentlyEdit && !confirm('Enter edit mode? You can modify stats, HP, proficiencies, and more. Popup descriptions are disabled while editing.')) {
    return;
  }
  const isEdit = document.body.classList.toggle('edit-mode');
  const btn = document.getElementById('edit-toggle');
  btn.textContent = isEdit ? '🔓 Done' : '🔒 Edit';
  // Persist preference for this character
  localStorage.setItem('edit-mode-' + CHAR_ID, isEdit ? '1' : '0');
}

// Restore edit mode preference (skip in DM preview)
if (!document.body.classList.contains('dm-preview') && localStorage.getItem('edit-mode-' + CHAR_ID) === '1') {
  document.body.classList.add('edit-mode');

async function toggleCharacterShare() {
  const btn = document.getElementById('share-toggle-btn');
  const currentlyShared = btn.textContent.includes('Public');
  const newState = !currentlyShared;
  const r = await fetch(`/api/character/${CHAR_ID}/toggle-share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shared: newState })
  });
  if (r.ok) {
    btn.textContent = newState ? '🌐 Public' : '🔒 Share';
    btn.style.background = newState ? 'var(--accent)' : 'var(--accent2)';
    btn.style.color = newState ? '#fff' : 'var(--accent)';
  } else {
    alert('Failed to update sharing');
  }
}
  const toggleBtn = document.getElementById('edit-toggle');
  if (toggleBtn) toggleBtn.textContent = '🔓 Done';
}

// ── Editable fields ──
function makeEditable(el, field, isNum, isList) {
  el.classList.add('editable');
  el.addEventListener('click', () => {
    if (!document.body.classList.contains('edit-mode')) return; // locked
    if (el.querySelector('input')) return;
    const current = el.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'editable-input' + (isNum ? ' number' : '');
    input.value = isNum ? current.replace(/[^0-9-]/g,'') : current;
    input.style.width = (Math.max(current.length, 3) + 2) + 'ch';
    el.textContent = '';
    el.appendChild(input);
    input.focus();
    input.select();

    const save = async () => {
      let val = input.value.trim();
      if (isNum) val = parseInt(val) || 0;
      if (isList) {
        const arr = val.split(',').map(s => s.trim()).filter(Boolean);
        el.textContent = arr.join(', ') || 'None';
        try { await sheetApi('/update', { [field]: JSON.stringify(arr) }); } catch(e) { console.error(e); }
      } else {
        el.textContent = isNum ? val : val || '—';
        try { await sheetApi('/update', { [field]: isNum ? val : val }); } catch(e) { console.error(e); }
      }
    };
    input.addEventListener('blur', save);
    input.addEventListener('keydown', (e) => { if (e.key==='Enter') input.blur(); });
  });
}

function initEditable() {
  document.querySelectorAll('.editable[data-num]').forEach(el => {
    makeEditable(el, el.dataset.field, true, el.dataset.list === '1');
  });
  document.querySelectorAll('.editable[data-text]').forEach(el => {
    makeEditable(el, el.dataset.field, false, el.dataset.list === '1');
  });
  // Text fields without explicit data-text/data-num (identity, etc.)
  document.querySelectorAll('.editable[data-field]:not([data-num]):not([data-text])').forEach(el => {
    makeEditable(el, el.dataset.field, false, el.dataset.list === '1');
  });
}

// ── Proficiency Picker (click row → checkbox dropdown) ──
function showProfPicker(type, title, masterList, field) {
  if (!document.body.classList.contains('edit-mode')) return;
  
  // Remove any existing pickers
  document.querySelectorAll('.prof-picker-overlay').forEach(o => o.remove());
  
  // Parse current proficiencies from the display span
  const displayEl = document.querySelector(`.prof-display[data-field="${field}"]`);
  const currentText = displayEl ? displayEl.textContent.trim() : '';
  const current = (!currentText || currentText === '—' || currentText === 'None') ? [] : currentText.split(',').map(s => s.trim()).filter(Boolean);
  const curSet = new Set(current.map(s => s.toLowerCase()));
  
  const overlay = document.createElement('div');
  overlay.className = 'prof-picker-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9998;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:1rem;max-width:380px;width:92%;max-height:70vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.4)';
  
  // Category-specific help
  const helpBlurbs = {
    armor: "Without proficiency: disadvantage on STR/DEX ability checks, saving throws, and attack rolls — and you can't cast spells while wearing it.",
    weapon: `Add your proficiency bonus (+${SHEETCFG.charProfBonus}) to attack rolls with proficient weapons. You can still use non-proficient weapons, but without the bonus.`,
    tool: "Add your proficiency bonus to ability checks made with these tools. Tool proficiency often stacks with relevant skill proficiency for advantage.",
    language: "You can speak, read, and write these languages fluently. Secret languages (Druidic, Thieves' cant) require special class features to learn.",
  };
  
  // Search/filter input
  let html = `<div style="flex-shrink:0"><h4 style="margin:0 0 0.25rem 0;font-size:0.95rem">${title}</h4>
    <p style="margin:0 0 0.5rem 0;font-size:0.7rem;color:var(--text-muted);line-height:1.4">${helpBlurbs[type] || ''}</p></div>`;
  if (masterList.length > 12) {
    html += `<input type="text" id="prof-filter" placeholder="Filter..." style="width:100%;padding:0.35rem 0.5rem;margin-bottom:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:0.8rem;outline:none;flex-shrink:0" oninput="filterProfPicker(this.value)">`;
  }
  html += `<div id="prof-options" style="display:flex;flex-direction:column;gap:0.15rem;flex:1;overflow-y:auto;min-height:0">`;
  
  const sorted = [...masterList].sort((a,b) => {
    const aHas = curSet.has(a.toLowerCase());
    const bHas = curSet.has(b.toLowerCase());
    if (aHas !== bHas) return bHas ? 1 : -1;  // checked items at bottom
    return a.localeCompare(b);
  });
  
  sorted.forEach(name => {
    const checked = curSet.has(name.toLowerCase());
    html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.4rem;border-radius:4px;cursor:pointer;font-size:0.82rem${checked ? ';background:rgba(255,255,255,0.04)' : ''}" 
      onmouseenter="this.style.background='rgba(255,255,255,0.06)'" onmouseleave="this.style.background='${checked ? 'rgba(255,255,255,0.04)' : 'none'}'">
      <input type="checkbox" ${checked ? 'checked' : ''} value="${name.replace(/"/g, '&quot;')}" onchange="toggleProfItem(this, '${field}', '${type}')" style="accent-color:var(--accent)">
      <span>${name}</span>
    </label>`;
  });
  html += `</div>`;
  
  html += `<div style="display:flex;gap:0.5rem;margin-top:0.75rem;justify-content:flex-end;flex-shrink:0">
    <button onclick="clearAllProfs('${field}', '${type}')" style="padding:0.3rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;font-size:0.75rem">Clear All</button>
    <button onclick="selectAllProfs('${field}', '${type}')" style="padding:0.3rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;font-size:0.75rem">Select All</button>
    <button onclick="document.querySelector('.prof-picker-overlay').remove()" style="padding:0.3rem 0.8rem;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-weight:600;font-size:0.8rem">Done</button>
  </div>`;
  
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function filterProfPicker(q) {
  const opts = document.getElementById('prof-options');
  if (!opts) return;
  const labels = opts.querySelectorAll('label');
  const lq = q.toLowerCase();
  labels.forEach(l => {
    l.style.display = l.textContent.toLowerCase().includes(lq) ? '' : 'none';
  });
}

function getCurrentProfs(field) {
  const displayEl = document.querySelector(`.prof-display[data-field="${field}"]`);
  if (!displayEl) return [];
  const t = displayEl.textContent.trim();
  return t === '—' || t === 'None' || t === '' ? [] : t.split(',').map(s => s.trim()).filter(Boolean);
}

// Section labels for each field (shown above badge group)
const PROF_SECTION_LABELS = {
  armor_proficiencies: 'Armor',
  weapon_proficiencies: 'Weapons',
  tool_proficiencies: 'Tools',
  languages: 'Languages'
};

function updateProfBadges(field) {
  const profs = getCurrentProfs(field);
  let container = document.getElementById('prof-badges-' + field);
  const section = document.getElementById('prof-section');
  if (!section) return;

  if (profs.length === 0) {
    // Remove badge container + its header if exists
    if (container) {
      const header = container.previousElementSibling;
      if (header && header.style.fontSize === '0.6rem') header.remove();
      container.remove();
    }
    // Update "No proficiencies" visibility
    updateNoProfsMessage();
    return;
  }

  // Build badge HTML
  const badgesHtml = profs.map(p => `<span class="prof-badge">${p}</span>`).join('');

  if (container) {
    container.innerHTML = badgesHtml;
  } else {
    // Create header + container
    const label = PROF_SECTION_LABELS[field] || field;
    const header = document.createElement('div');
    header.style.cssText = 'font-size:0.72rem;color:var(--text-muted);margin-bottom:0.15rem;margin-top:0.3rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em';
    header.textContent = label;
    container = document.createElement('span');
    container.id = 'prof-badges-' + field;
    container.innerHTML = badgesHtml;

    // Insert before edit pickers or at end of prof-section
    const editBlock = section.querySelector('.edit-only-block');
    if (editBlock) {
      editBlock.before(header, container);
    } else {
      section.appendChild(header);
      section.appendChild(container);
    }
  }
  updateNoProfsMessage();
}

function updateNoProfsMessage() {
  const noMsg = document.querySelector('#prof-section > span[style*="color:var(--text-muted)"]');
  const anyBadges = document.querySelectorAll('#prof-section [id^="prof-badges-"]').length > 0;
  if (anyBadges && noMsg) {
    noMsg.style.display = 'none';
  } else if (!anyBadges && noMsg) {
    noMsg.style.display = '';
  } else if (!anyBadges && !noMsg) {
    const section = document.getElementById('prof-section');
    if (section) {
      const msg = document.createElement('span');
      msg.style.cssText = 'color:var(--text-muted);font-size:0.8rem';
      msg.textContent = 'No proficiencies';
      section.insertBefore(msg, section.querySelector('.edit-only-block') || section.firstChild);
    }
  }
}

async function toggleProfItem(cb, field, type) {
  let profs = getCurrentProfs(field);
  const name = cb.value;
  const idx = profs.findIndex(p => p.toLowerCase() === name.toLowerCase());
  if (cb.checked && idx === -1) {
    profs.push(name);
  } else if (!cb.checked && idx !== -1) {
    profs.splice(idx, 1);
  }
  // Update display
  const displayEl = document.querySelector(`.prof-display[data-field="${field}"]`);
  if (displayEl) displayEl.textContent = profs.join(', ') || '';
  // Update badges
  updateProfBadges(field);
  // Save
  await sheetApi('/update', { [field]: JSON.stringify(profs) });
  // Refresh proficiency-based UI
  if (field === 'weapon_proficiencies') tagWeapons();
  if (field === 'armor_proficiencies') { tagArmor(); updateAcWarning(); }
}

async function clearAllProfs(field, type) {
  const displayEl = document.querySelector(`.prof-display[data-field="${field}"]`);
  if (displayEl) displayEl.textContent = '';
  updateProfBadges(field);
  await sheetApi('/update', { [field]: JSON.stringify([]) });
  if (field === 'weapon_proficiencies') tagWeapons();
  if (field === 'armor_proficiencies') { tagArmor(); updateAcWarning(); }
  const overlay = document.querySelector('.prof-picker-overlay');
  if (overlay) overlay.remove();
}

async function selectAllProfs(field, type) {
  let master;
  if (type === 'armor') master = ALL_ARMOR_PROFS;
  else if (type === 'weapon') master = ALL_WEAPON_PROFS;
  else if (type === 'tool') master = ALL_TOOL_PROFS;
  else master = ALL_LANGUAGES;
  const displayEl = document.querySelector(`.prof-display[data-field="${field}"]`);
  if (displayEl) displayEl.textContent = master.join(', ');
  updateProfBadges(field);
  await sheetApi('/update', { [field]: JSON.stringify(master) });
  if (field === 'weapon_proficiencies') tagWeapons();
  if (field === 'armor_proficiencies') { tagArmor(); updateAcWarning(); }
  const overlay = document.querySelector('.prof-picker-overlay');
  if (overlay) overlay.remove();
}

// ── Defense Picker (click row → checkbox overlay) ──
function showDefensePicker(title, masterList, field, icon, helpText) {
  if (!document.body.classList.contains('edit-mode')) return;
  
  // Remove any existing pickers
  document.querySelectorAll('.defense-picker-overlay').forEach(o => o.remove());
  
  // Parse current values from the display span
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  const currentText = displayEl ? displayEl.textContent.trim() : '';
  const current = (!currentText || currentText === '—' || currentText === 'None') ? [] : currentText.split(',').map(s => s.trim()).filter(Boolean);
  const curSet = new Set(current.map(s => s.toLowerCase()));
  
  const overlay = document.createElement('div');
  overlay.className = 'defense-picker-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9998;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:1rem;max-width:380px;width:92%;max-height:70vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.4)';
  
  let html = `<div style="flex-shrink:0"><h4 style="margin:0 0 0.25rem 0;font-size:0.95rem">${title}</h4>
    <p style="margin:0 0 0.5rem 0;font-size:0.7rem;color:var(--text-muted);line-height:1.4">${helpText || ''}</p></div>`;
  if (masterList.length > 12) {
    html += `<input type="text" id="def-filter" placeholder="Filter..." style="width:100%;padding:0.35rem 0.5rem;margin-bottom:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:0.8rem;outline:none;flex-shrink:0" oninput="filterDefensePicker(this.value)">`;
  }
  html += `<div id="def-options" style="display:flex;flex-direction:column;gap:0.15rem;flex:1;overflow-y:auto;min-height:0">`;
  
  const sorted = [...masterList].sort((a,b) => {
    const aHas = curSet.has(a.toLowerCase());
    const bHas = curSet.has(b.toLowerCase());
    if (aHas !== bHas) return bHas ? 1 : -1;
    return a.localeCompare(b);
  });
  
  sorted.forEach(name => {
    const checked = curSet.has(name.toLowerCase());
    html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.4rem;border-radius:4px;cursor:pointer;font-size:0.82rem${checked ? ';background:rgba(255,255,255,0.04)' : ''}" 
      onmouseenter="this.style.background='rgba(255,255,255,0.06)'" onmouseleave="this.style.background='${checked ? 'rgba(255,255,255,0.04)' : 'none'}'">
      <input type="checkbox" ${checked ? 'checked' : ''} value="${name.replace(/"/g, '&quot;')}" onchange="toggleDefenseItem(this, '${field}', '${icon}')" style="accent-color:var(--accent)">
      <span>${name}</span>
    </label>`;
  });
  html += `</div>`;
  
  html += `<div style="display:flex;gap:0.5rem;margin-top:0.75rem;justify-content:flex-end;flex-shrink:0">
    <button onclick="clearAllDefenses('${field}')" style="padding:0.3rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;font-size:0.75rem">Clear All</button>
    <button onclick="selectAllDefenses('${field}')" style="padding:0.3rem 0.6rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;font-size:0.75rem">Select All</button>
    <button onclick="document.querySelector('.defense-picker-overlay').remove()" style="padding:0.3rem 0.8rem;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-weight:600;font-size:0.8rem">Done</button>
  </div>`;
  
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function filterDefensePicker(q) {
  const opts = document.getElementById('def-options');
  if (!opts) return;
  const labels = opts.querySelectorAll('label');
  const lq = q.toLowerCase();
  labels.forEach(l => {
    l.style.display = l.textContent.toLowerCase().includes(lq) ? '' : 'none';
  });
}

function getCurrentDefenses(field) {
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  if (!displayEl) return [];
  const t = displayEl.textContent.trim();
  return (!t || t === '—' || t === 'None') ? [] : t.split(',').map(s => s.trim()).filter(Boolean);
}

async function toggleDefenseItem(cb, field, icon) {
  // Get current from display (live state)
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  if (!displayEl) return;
  const curText = displayEl.textContent.trim();
  let items = (!curText || curText === '—' || curText === 'None') ? [] : curText.split(',').map(s => s.trim()).filter(Boolean);
  
  const name = cb.value;
  const idx = items.findIndex(p => p.toLowerCase() === name.toLowerCase());
  if (cb.checked && idx === -1) {
    items.push(name);
  } else if (!cb.checked && idx !== -1) {
    items.splice(idx, 1);
  }
  // Update display
  displayEl.textContent = items.join(', ') || '—';
  // Save to server
  await sheetApi('/update', { [field]: JSON.stringify(items) });
  // Refresh the defense badge area (reload page or update inline)
  refreshDefenseBadges();
}

async function clearAllDefenses(field) {
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  if (displayEl) displayEl.textContent = '—';
  await sheetApi('/update', { [field]: JSON.stringify([]) });
  refreshDefenseBadges();
  document.querySelector('.defense-picker-overlay')?.remove();
}

async function selectAllDefenses(field) {
  // Determine which master list to use based on field name
  let master;
  if (field.includes('condition')) master = ALL_CONDITIONS;
  else master = ALL_DAMAGE_TYPES;
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  if (displayEl) displayEl.textContent = master.join(', ');
  await sheetApi('/update', { [field]: JSON.stringify(master) });
  refreshDefenseBadges();
  document.querySelector('.defense-picker-overlay')?.remove();
}

function refreshDefenseBadges() {
  // Update the defense badge section inline without page reload
  updateDefenseBadges('damage_resistances');
  updateDefenseBadges('damage_immunities');
  updateDefenseBadges('damage_vulnerabilities');
  updateDefenseBadges('condition_immunities');
}

// Map field → icon + class for badge rendering
const DEFENSE_BADGE_STYLE = {
  damage_resistances: { icon: '🛡️', cls: 'defense-resist' },
  damage_immunities: { icon: '✨', cls: 'defense-immune' },
  damage_vulnerabilities: { icon: '⚠️', cls: 'defense-vuln' },
  condition_immunities: { icon: '✨', cls: 'defense-immune' }
};

const DEFENSE_SECTION_LABELS = {
  damage_resistances: 'Resistances',
  damage_immunities: 'Immunities',
  damage_vulnerabilities: 'Vulnerabilities',
  condition_immunities: 'Condition Imm.'
};

function getDefenseBadgeContainer(field) {
  // Defense section structure: header div → badges → ...
  const section = document.getElementById('defenses-section');
  if (!section) return null;
  const labels = section.querySelectorAll('div[style*="font-size:0.72rem"]');
  for (const label of labels) {
    if (label.textContent.trim() === DEFENSE_SECTION_LABELS[field]) {
      // Return the container after the header — collect all defense-badge siblings until next header or non-badge
      const container = document.createElement('span');
      container.id = 'def-badges-' + field;
      let el = label.nextElementSibling;
      // Find existing badges or create container
      while (el && el.classList && (el.classList.contains('defense-badge') || el.classList.contains('defense-display'))) {
        if (!el.classList.contains('defense-display')) {
          container.appendChild(el.cloneNode(true));
        }
        el = el.nextElementSibling;
      }
      return { label, container, nextSibling: el };
    }
  }
  return null;
}

function updateDefenseBadges(field) {
  const displayEl = document.querySelector(`.defense-display[data-field="${field}"]`);
  if (!displayEl) return;
  const curText = displayEl.textContent.trim();
  const items = (!curText || curText === '—' || curText === 'None') ? [] : curText.split(',').map(s => s.trim()).filter(Boolean);
  const style = DEFENSE_BADGE_STYLE[field] || { icon: '🛡️', cls: 'defense-resist' };
  const section = document.getElementById('defenses-section');
  if (!section) return;

  // Remove old badge container + header if exists
  const oldContainer = document.getElementById('def-badges-' + field);
  if (oldContainer) {
    const header = oldContainer.previousElementSibling;
    if (header && header.style && header.style.fontSize === '0.6rem') header.remove();
    oldContainer.remove();
  }

  if (items.length === 0) {
    updateNoDefensesMessage();
    return;
  }

  // Build badge HTML
  const badgesHtml = items.map(v => `<span class="defense-badge ${style.cls}">${style.icon} ${v}</span>`).join('');

  // Create header + container
  const label = DEFENSE_SECTION_LABELS[field] || field;
  const header = document.createElement('div');
  header.style.cssText = 'font-size:0.72rem;color:var(--text-muted);margin-bottom:0.15rem;margin-top:0.3rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em';
  header.textContent = label;

  const container = document.createElement('span');
  container.id = 'def-badges-' + field;
  container.innerHTML = badgesHtml;

  // Insert before edit pickers or at end of defenses-section
  const editBlock = section.querySelector('.edit-only-block');
  if (editBlock) {
    editBlock.before(header, container);
  } else {
    section.appendChild(header);
    section.appendChild(container);
  }
  updateNoDefensesMessage();
}

function updateNoDefensesMessage() {
  const section = document.getElementById('defenses-section');
  if (!section) return;
  const noMsg = section.querySelector('span[style*="color:var(--text-muted)"]');
  const anyBadges = section.querySelectorAll('[id^="def-badges-"]').length > 0;
  if (anyBadges && noMsg) {
    noMsg.style.display = 'none';
  } else if (!anyBadges && noMsg) {
    noMsg.style.display = '';
  } else if (!anyBadges && !noMsg) {
    const msg = document.createElement('span');
    msg.style.cssText = 'color:var(--text-muted);font-size:0.8rem';
    msg.textContent = 'No special defenses';
    const editBlock = section.querySelector('.edit-only-block');
    if (editBlock) editBlock.before(msg);
    else section.appendChild(msg);
  }
}

// ── Proficiency Toggles ──
async function toggleSaveProf(stat) {
  if (!document.body.classList.contains('edit-mode')) return;
  const active = [];
  // Each save row needs a data-stat attribute — rebuild list from all active dots
  document.querySelectorAll('.save-dot.active').forEach(d => {
    if (d.dataset.stat !== stat) active.push(d.dataset.stat);
  });
  // If the clicked stat wasn't active, add it
  const dot = document.querySelector(`.save-dot[data-stat="${stat}"]`);
  if (!dot.classList.contains('active')) active.push(stat);
  
  await sheetApi('/update', { save_proficiencies: JSON.stringify(active) });
  location.reload();
}

async function toggleSkillProf(skillName) {
  if (!document.body.classList.contains('edit-mode')) return;
  const active = [];
  document.querySelectorAll('.skill-dot.active').forEach(d => {
    if (d.dataset.skill !== skillName) active.push(d.dataset.skill);
  });
  const dot = document.querySelector(`.skill-dot[data-skill="${skillName}"]`);
  if (!dot.classList.contains('active')) active.push(skillName);
  
  await sheetApi('/update', { skills: JSON.stringify(active) });
  location.reload();
}

