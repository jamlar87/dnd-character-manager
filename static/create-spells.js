/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
const KNOWN_CASTERS = ['Bard','Sorcerer','Warlock','Ranger'];
const L1_SPELLS_KNOWN = {Bard:4, Sorcerer:2, Warlock:2, Ranger:0};  // Ranger starts at L2
const L1_CANTRIPS = {Bard:2, Sorcerer:4, Warlock:2, Ranger:0};

function isKnownCaster() {
  return KNOWN_CASTERS.includes(state.class_name);
}

let _createSpellList = [];

async function showSpellPicker() {
  const className = state.class_name;
  const needSpells = L1_SPELLS_KNOWN[className] || 0;
  const needCantrips = L1_CANTRIPS[className] || 0;
  const total = needSpells + needCantrips;
  
  if (total === 0) { showStep(5); return; }  // No spells to pick
  
  if (!state.spells) state.spells = [];
  
  document.getElementById('spell-picker-info').textContent = 
    `Your ${className} starts with ${total} spell${total>1?'s':''} (${needCantrips>0?needCantrips+' cantrip'+ (needCantrips>1?'s':'') + (needSpells>0?' + ':''):''}${needSpells>0?needSpells+' L1 spell'+ (needSpells>1?'s':''):''}).`;
  
  document.getElementById('spell-picker-overlay').style.display = 'flex';
  document.getElementById('sp-pick-search').value = '';
  
  try {
    const r = await fetch(`/api/spells/starting?class_name=${className}&level=1`);
    _createSpellList = await r.json();
    filterCreateSpells();
    renderCreateSelected();
  } catch(e) {
    document.getElementById('sp-pick-results').innerHTML = '<div style="padding:0.5rem;color:var(--danger);text-align:center">Failed to load spells</div>';
  }
}

function filterCreateSpells() {
  const q = (document.getElementById('sp-pick-search')?.value || '').toLowerCase().trim();
  const div = document.getElementById('sp-pick-results');
  if (!div) return;
  
  let spells = _createSpellList;
  if (window.SourceFilter) {
    const src = SourceFilter.slugs('create-spells');
    if (src.length) spells = spells.filter(s => SourceFilter.matches(s.book || s.source || '', src));
  }
  if (q) {
    spells = spells.filter(s => s.name.toLowerCase().includes(q) || (s.school||'').toLowerCase().includes(q));
  }
  
  if (!spells.length) {
    div.innerHTML = '<div style="padding:0.5rem;color:var(--text-muted);text-align:center">No spells match</div>';
    return;
  }
  
  const selected = state.spells || [];
  const className = state.class_name;
  const needSpells = L1_SPELLS_KNOWN[className] || 0;
  const needCantrips = L1_CANTRIPS[className] || 0;
  const total = needSpells + needCantrips;
  
  const shown = spells;
  div.innerHTML = shown.map((s, i) => {
    const isSel = selected.some(x => x.name === s.name);
    const uid = 'csp' + i;
    // Level badge
    const lvlBadge = s.level === 0
      ? '<span style="background:var(--accent2);color:var(--accent);font-size:0.65rem;padding:0.1rem 0.35rem;border-radius:3px;flex-shrink:0">Cantrip</span>'
      : `<span style="background:var(--accent);color:#fff;font-size:0.65rem;padding:0.1rem 0.35rem;border-radius:3px;flex-shrink:0">Lv${s.level}</span>`;
    // Dice badge
    let diceBadge = '';
    if (s.dice) {
      if (s.dice_healing) {
        diceBadge = `<span style="font-size:0.68rem;font-weight:600;background:rgba(34,197,94,0.15);color:var(--success);font-family:monospace;padding:0.1rem 0.25rem;border-radius:3px;flex-shrink:0">${s.dice}</span>`;
      } else if (s.dice_ac) {
        diceBadge = `<span style="font-size:0.68rem;font-weight:600;background:rgba(59,130,246,0.15);color:#3b82f6;font-family:monospace;padding:0.1rem 0.25rem;border-radius:3px;flex-shrink:0">${s.dice}</span>`;
      } else if (s.dice_buff) {
        diceBadge = `<span style="font-size:0.68rem;font-weight:600;background:rgba(139,92,246,0.15);color:#8b5cf6;font-family:monospace;padding:0.1rem 0.25rem;border-radius:3px;flex-shrink:0">${s.dice}</span>`;
      } else {
        diceBadge = `<span style="font-size:0.68rem;font-weight:600;background:var(--accent2);color:var(--accent);font-family:monospace;padding:0.1rem 0.25rem;border-radius:3px;flex-shrink:0">${s.dice}</span>`;
      }
    }
    let ritualBadge = s.ritual ? '<span style="font-size:0.68rem;color:var(--accent);opacity:0.7;flex-shrink:0" title="Ritual">\\u26a1</span>' : '';
    let concBadge = s.concentration ? '<span style="font-size:0.68rem;color:var(--warning);opacity:0.8;flex-shrink:0" title="Concentration">\\u23f3</span>' : '';
    const srcBadge = s.book ? `${_srcBadge(s.book)}` : (s.source ? `${_srcBadge(s.source)}` : '');
    const bg = isSel ? 'background:rgba(99,102,241,0.15);' : '';
    // Description body
    let descText = (s.description || '').replace(/'/g, "\\'").replace(/`/g, '\\`').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    let timeText = s.casting_time || '1 action';
    let rangeText = s.range || 'Self';
    let durText = s.duration || 'Instantaneous';
    return `<div style="border-bottom:1px solid var(--border);${bg}">
      <div onclick="const b=document.getElementById('${uid}');b.style.display=b.style.display==='none'?'block':'none'" 
        style="padding:0.35rem 0.5rem;cursor:pointer;font-size:0.82rem;display:flex;align-items:center;gap:0.3rem;flex-wrap:wrap">
        <span style="font-size:0.8rem;width:1.2em;text-align:center;flex-shrink:0" onclick="event.stopPropagation();toggleCreateSpell('${s.name.replace(/'/g,"\\'")}',${s.level})">${isSel?'✅':'☐'}</span>
        <strong style="flex:1 1 auto;min-width:8ch;overflow-wrap:break-word;word-break:break-word">${s.name}</strong>
        ${lvlBadge}
        ${diceBadge}
        ${ritualBadge}
        ${concBadge}
        <span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">${s.school||''}</span>
        ${srcBadge}
      </div>
      <div id="${uid}" style="display:none;padding:0.3rem 0.5rem 0.5rem 2.2rem;font-size:0.78rem;color:var(--text-muted);line-height:1.5;overflow-wrap:break-word;word-break:break-word">
        <p style="margin:0 0 0.3rem 0">${descText || '<em>No description available.</em>'}</p>
        <p style="margin:0;font-size:0.7rem;display:flex;flex-wrap:wrap;gap:0.5rem">
          <span>\\u23f1 ${timeText}</span>
          <span>\\ud83c\\udfaf ${rangeText}</span>
          <span>\\u23f3 ${durText}</span>
          ${s.ritual ? '<span style="color:var(--accent)">\\u26a1 Ritual</span>' : ''}
          ${s.concentration ? '<span style="color:var(--warning)">\\ud83e\\udde0 Concentration</span>' : ''}
        </p>
      </div>
    </div>`;
  }).join('');
  if (shown.length < spells.length) {
    div.innerHTML += `<div style="padding:0.3rem;font-size:0.7rem;color:var(--text-muted);text-align:center">Showing ${shown.length} of ${spells.length} — type to narrow</div>`;
  }
  
  document.getElementById('sp-pick-count').textContent = `${selected.length}/${total} selected`;
}

function toggleCreateSpell(name, level) {
  if (!state.spells) state.spells = [];
  const idx = state.spells.findIndex(x => x.name === name);
  const className = state.class_name;
  const needSpells = L1_SPELLS_KNOWN[className] || 0;
  const needCantrips = L1_CANTRIPS[className] || 0;
  const total = needSpells + needCantrips;
  
  if (idx >= 0) {
    state.spells.splice(idx, 1);
  } else {
    if (state.spells.length >= total) return;
    const selCantrips = state.spells.filter(x => x.level === 0).length;
    const selLeveled = state.spells.filter(x => x.level > 0).length;
    if (level === 0 && selCantrips >= needCantrips) return;
    if (level > 0 && selLeveled >= needSpells) return;
    state.spells.push({name, level});
  }
  
  filterCreateSpells();
  renderCreateSelected();
}

function renderCreateSelected() {
  const div = document.getElementById('sp-pick-selected');
  if (!div) return;
  const selected = state.spells || [];
  div.innerHTML = selected.map(s => {
    const lvlBadge = s.level === 0 
      ? '<span style="background:var(--accent2);color:var(--accent);font-size:0.72rem;padding:0.1rem 0.35rem;border-radius:3px">Cantrip</span>'
      : '<span style="background:var(--accent);color:#fff;font-size:0.72rem;padding:0.1rem 0.35rem;border-radius:3px">Lv'+s.level+'</span>';
    const fullSpell = _createSpellList.find(sp => sp.name === s.name);
    const srcBadge = (fullSpell && fullSpell.book) ? `${_srcBadge(fullSpell.book)}` : ((fullSpell && fullSpell.source) ? `${_srcBadge(fullSpell.source)}` : '');
    // Dice badge on selected chip
    let diceBadge = '';
    if (fullSpell && fullSpell.dice) {
      if (fullSpell.dice_healing) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(34,197,94,0.2);color:var(--success);font-family:monospace;padding:0 0.2rem;border-radius:2px">${fullSpell.dice}</span>`;
      } else if (fullSpell.dice_ac) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(59,130,246,0.2);color:#3b82f6;font-family:monospace;padding:0 0.2rem;border-radius:2px">${fullSpell.dice}</span>`;
      } else if (fullSpell.dice_buff) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(139,92,246,0.2);color:#8b5cf6;font-family:monospace;padding:0 0.2rem;border-radius:2px">${fullSpell.dice}</span>`;
      } else {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:var(--accent2);color:var(--accent);font-family:monospace;padding:0 0.2rem;border-radius:2px">${fullSpell.dice}</span>`;
      }
    }
    return `<div style="display:flex;align-items:center;gap:0.25rem;padding:0.2rem 0.4rem;background:rgba(99,102,241,0.12);border:1px solid var(--accent2);border-radius:4px;font-size:0.78rem">
      <strong>${s.name}</strong> ${diceBadge} ${lvlBadge} ${srcBadge}
      <button onclick="event.stopPropagation();toggleCreateSpell('${s.name.replace(/'/g,"\\'")}',${s.level})"
        style="background:none;border:none;color:var(--danger);cursor:pointer;font-size:0.7rem;padding:0;line-height:1">✕</button>
    </div>`;
  }).join('');
}

function skipSpellPicker() {
  if (!state.spells) state.spells = [];
  document.getElementById('spell-picker-overlay').style.display = 'none';
  showStep(5);
}

function confirmSpellPicker() {
  if (!state.spells) state.spells = [];
  document.getElementById('spell-picker-overlay').style.display = 'none';
  showStep(5);
}

// ── Subclass Bonus Picker ────────────────────────────────────────────
function showSubclassBonusPicker() {
  const bonus = SUBCLASS_BONUS_MAP[state.subclass];
  if (!bonus) { showStep(5); return; }
  
  state.subclass_bonus = state.subclass_bonus || [];
  const typeLabel = bonus.type === 'skills' ? 'skill proficiencies' : 'languages';
  const options = bonus.type === 'skills' ? ALL_SKILLS : bonus.options;
  
  document.getElementById('bonus-picker-title').textContent = `📋 ${state.subclass} — Bonus ${typeLabel.charAt(0).toUpperCase()+typeLabel.slice(1)}`;
  document.getElementById('bonus-picker-info').textContent = `Pick ${bonus.count} ${typeLabel}.`;
  renderBonusPickerOptions(options, bonus.count);
  document.getElementById('bonus-picker-overlay').style.display = 'flex';
}

function renderBonusPickerOptions(options, max) {
  const selected = state.subclass_bonus || [];
  const div = document.getElementById('bonus-picker-options');
  div.innerHTML = options.map(opt => {
    const isSel = selected.includes(opt);
    const atMax = selected.length >= max && !isSel;
    return `<label style="display:inline-block;padding:0.3rem 0.6rem;background:${isSel ? 'var(--accent2)' : 'var(--bg)'};border:1px solid var(--border);border-radius:4px;font-size:0.82rem;cursor:${atMax ? 'not-allowed' : 'pointer'};opacity:${atMax ? '0.5' : '1'}"
      onclick="event.preventDefault();toggleCreateBonus(this,${max})" data-opt="${opt}">
      ${isSel ? '✅ ' : ''}${opt}
    </label>`;
  }).join('');
  document.getElementById('bonus-picker-count').textContent = `${selected.length}/${max} selected`;
}

function toggleCreateBonus(el, max) {
  const opt = el.getAttribute('data-opt');
  if (!opt) return;
  state.subclass_bonus = state.subclass_bonus || [];
  const idx = state.subclass_bonus.indexOf(opt);
  if (idx >= 0) {
    state.subclass_bonus.splice(idx, 1);
  } else if (state.subclass_bonus.length < max) {
    state.subclass_bonus.push(opt);
  }
  const bonus = SUBCLASS_BONUS_MAP[state.subclass];
  renderBonusPickerOptions(bonus.type === 'skills' ? ALL_SKILLS : bonus.options, bonus.count);
}

function skipBonusPicker() {
  state.subclass_bonus = [];
  document.getElementById('bonus-picker-overlay').style.display = 'none';
  showStep(5);
}

function confirmBonusPicker() {
  document.getElementById('bonus-picker-overlay').style.display = 'none';
  showStep(5);
}
