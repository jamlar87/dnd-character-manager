/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
// ── Step 3: Skills ──
function buildSkills() {
  const c = CLASSES[state.class_name];
  const available = Array.isArray(c.skills) ? c.skills : ALL_SKILLS;
  const max = c.skill_count;
  document.getElementById('skill-count-label').textContent = `(pick ${max})`;

  const skills = state.showAllSkills ? ALL_SKILLS : [...new Set([...available, ...state.unlockedSkills])];
  const availableSet = new Set(available);
  const unlockedSet = new Set(state.unlockedSkills);
  document.getElementById('skill-grid').innerHTML = skills.map(s => {
    const inClass = availableSet.has(s);
    const unlocked = unlockedSet.has(s);
    const selectable = inClass || unlocked;
    let cls = 'option-card';
    let icon = '';
    let onclick;
    if (selectable) {
      cls = 'option-card';
      icon = unlocked && !inClass ? ' 🔓' : '';
      onclick = `onclick="toggleSkill('${s}')"`;
    } else {
      cls = 'option-card locked';
      icon = ' 🔒';
      onclick = `onclick="unlockSkill('${s}')"`;
    }
    return `<div class="${cls}" ${onclick}><div class="name">${s}${icon}</div><div class="detail">${SKILL_ABILITIES[s].slice(0,3).toUpperCase()}</div></div>`;
  }).join('');

  // Re-highlight previously selected skills
  document.querySelectorAll('#skill-grid .option-card').forEach(card => {
    const cardName = card.querySelector('.name').textContent.replace(/ [🔒🔓]/,'').trim();
    card.classList.toggle('selected', state.skills.includes(cardName));
  });
}

function toggleAllSkills() {
  state.showAllSkills = !state.showAllSkills;
  const btn = document.getElementById('show-all-skills-btn');
  btn.textContent = state.showAllSkills ? '👁 Class Skills' : '👁 Show All';
  buildSkills();
}

function unlockSkill(s) {
  if (!state.unlockedSkills.includes(s)) state.unlockedSkills.push(s);
  buildSkills();
  toggleSkill(s);
}

function toggleSkill(s) {
  if (state.skills.includes(s)) { state.skills = state.skills.filter(x => x !== s); }
  else {
    const c = CLASSES[state.class_name];
    if (state.skills.length >= c.skill_count) { alert(`Only ${c.skill_count} skills allowed`); return; }
    state.skills.push(s);
  }
  document.querySelectorAll('#skill-grid .option-card').forEach(c => {
    const cardName = c.querySelector('.name').textContent.replace(/ [🔒🔓]/,'').trim();
    c.classList.toggle('selected', state.skills.includes(cardName));
  });
  showSkillInfo(s);
}

function showSkillInfo(_s) {
  const info = document.getElementById('skill-info');
  if (!state.skills.length) { info.style.display = 'none'; return; }
  info.style.display = 'block';
  info.innerHTML = state.skills.map(s =>
    `<div style="margin-bottom:0.5rem"><strong>${s}</strong>: ${SKILL_DESCS[s]}</div>`
  ).join('');
}

// ── Step 4: Details ──
(function() {
  document.getElementById('char-bg').innerHTML = '<option value="">—</option>' + BACKGROUNDS.map(b => `<option>${b}</option>`).join('');
  document.getElementById('char-align').innerHTML = ALIGNMENTS.map(a => `<option>${a}</option>`).join('');
})();

function showBgInfo(bg) {
  const el = document.getElementById('bg-info');
  if (!bg) { el.style.display = 'none'; document.getElementById('custom-bg-row').style.display = 'none'; return; }
  el.style.display = 'block';
  if (bg === 'Custom') {
    el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><span><strong>Custom Background</strong></span></div><br>${BACKGROUND_INFO['Custom']}`;
    document.getElementById('custom-bg-row').style.display = 'block';
  } else {
    const src = BACKGROUND_SOURCES[bg] || '';
    el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><span><strong>${bg}</strong></span>${src ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef(this.dataset.src)" data-src="${src.replace(/"/g, '&quot;')}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;white-space:nowrap;cursor:pointer" title="Click to open ${src}">📚 ${src}</span>` : ''}</div>${BACKGROUND_INFO[bg] || ''}`;
    document.getElementById('custom-bg-row').style.display = 'none';
  }
}

// ── Step 5: Review ──
function buildReview() {
  state.name = document.getElementById('char-name').value || 'Unnamed';
  const bgVal = document.getElementById('char-bg').value;
  state.background = bgVal === 'Custom' ? (document.getElementById('custom-bg-name').value || 'Custom') : bgVal;
  state.custom_background = bgVal === 'Custom' ? document.getElementById('custom-bg-name').value : '';
  state.alignment = document.getElementById('char-align').value;
  state.level = parseInt(document.getElementById('char-level').value) || 1;

  const r = RACES[state.race];
  const c = CLASSES[state.class_name];
  const con_mod = Math.floor(((state.abilities.constitution || 10) - 10) / 2);
  const hp = c.hd + con_mod;

  document.getElementById('review-card').innerHTML = `
    <h3>${state.name}</h3>
    <p><strong>${state.race}</strong>${state.subrace ? ' ('+state.subrace+')' : ''} · <strong>${state.class_name}</strong>${state.subclass ? ' — '+state.subclass : ''} · <strong>Lv${state.level}</strong></p>
    <p>${state.background || 'No background'} · ${state.alignment || 'Unaligned'}</p>
    <h4 style="margin-top:0.5rem">Abilities</h4>
    <div class="grid-3">${ABILITIES.map(a => `<div class="stat-box"><div class="abl">${a.slice(0,3).toUpperCase()}</div><div class="stat-num">${state.abilities[a]}</div><div class="stat-mod">${Math.floor((state.abilities[a]-10)/2) >= 0 ? '+' : ''}${Math.floor((state.abilities[a]-10)/2)}</div></div>`).join('')}</div>
    <p style="margin-top:0.5rem;font-size:0.85rem;color:var(--text-muted)">HP: ~${Math.max(1, hp)} · Hit Die: d${c.hd} · Speed: ${r.speed}ft</p>
    <p style="font-size:0.85rem;color:var(--text-muted)">Skills: ${state.skills.join(', ') || 'none'}</p>
    ${state.equipment && state.equipment.length ? `<p style="font-size:0.85rem;color:var(--text-muted)">Equipment: ${state.equipment.join(', ')}</p>` : ''}
    ${state.race === 'Half-Elf' && state.asi_picks.length ? `<p style="font-size:0.85rem;color:var(--accent)">Half-Elf bonus: ${state.asi_picks.map(a=>a.slice(0,3).toUpperCase()).join(', ')}</p>` : ''}
    ${FLEXIBLE_ASI.has(state.race) && state.asi_picks.length ? `<p style="font-size:0.85rem;color:var(--accent)">ASI pick: ${state.asi_picks.map(a=>a.slice(0,3).toUpperCase()).join(', ')} +2</p>` : ''}
    ${isMpmmRace() && mpmmPicks().length ? `<p style="font-size:0.85rem;color:var(--accent)">ASI choice: ${mpmmPicks().map(([a,v])=>a.slice(0,3).toUpperCase()+'+'+v).join(', ')} <span style="color:var(--text-muted)">(MPMM)</span></p>` : ''}
    ${(state.race === 'Dragonborn' || state.race === 'Dragonblood') && !state.dragonborn_ancestry ? `<p style="font-size:0.85rem;color:var(--danger);margin-top:0.5rem">⚠ Draconic Ancestry not selected — required for resistance & breath weapon</p>` : ''}
    ${(state.race === 'Dragonborn' || state.race === 'Dragonblood') && state.dragonborn_ancestry ? `<p style="font-size:0.85rem;color:var(--accent)">Draconic Ancestry: ${state.dragonborn_ancestry}</p>` : ''}
    ${state.subclass_bonus && state.subclass_bonus.length ? `<p style="font-size:0.85rem;color:var(--accent)">Bonus: ${state.subclass_bonus.join(', ')}</p>` : ''}
  `;
}

// ── Spell Picker for Creation ──────────────────────────────────────────
