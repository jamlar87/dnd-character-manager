/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
function buildAbilities() {
  const grid = document.getElementById('ability-grid');
  grid.innerHTML = ABILITIES.map(a => {
    const val = state.abilities[a] || 10;
    const mod = Math.floor((val - 10) / 2);
    const modStr = mod >= 0 ? '+' + mod : '' + mod;
    return `
    <div class="ability-box clickable" data-ability="${a}" onclick="showAbilityEditor('${a}')">
      <div class="abl">${a.slice(0,3).toUpperCase()}</div>
      <div class="val" id="ab-val-${a}">${val}</div>
      <div class="abl" style="font-size:0.7rem;margin-top:0.1rem" id="ab-mod-${a}">${modStr}</div>
    </div>`;
  }).join('');
  // Initialize all stats to 10 if not set
  ABILITIES.forEach(a => { if (!(a in state.abilities)) state.abilities[a] = 10; });
  updateAsiPreview();
  // Show all ability descriptions
  const info = document.getElementById('ability-info');
  info.style.display = 'block';
  info.innerHTML = ABILITIES.map(a =>
    `<div style="margin-bottom:0.5rem"><strong>${a.charAt(0).toUpperCase()+a.slice(1)}</strong>: ${ABILITY_DESCS[a]}</div>`
  ).join('');
}

// ── Ability Score Popover Editor (create wizard) ──
function showAbilityEditor(statKey) {
  document.querySelectorAll('.ability-editor-overlay').forEach(o => o.remove());

  let score = state.abilities[statKey] || 10;
  const statLabel = statKey.slice(0,3).toUpperCase();

  const overlay = document.createElement('div');
  overlay.className = 'ability-editor-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const card = document.createElement('div');
  card.className = 'ability-editor-card';

  function render() {
    const mod = Math.floor((score - 10) / 2);
    const modStr = mod >= 0 ? '+' + mod : '' + mod;
    let modClass = mod > 0 ? 'positive' : mod < 0 ? 'negative' : 'zero';
    const statNames = {strength:'Strength',dexterity:'Dexterity',constitution:'Constitution',
      intelligence:'Intelligence',wisdom:'Wisdom',charisma:'Charisma'};
    const fullName = statNames[statKey] || statKey;

    card.innerHTML = [
      '<h3>' + statLabel + ' — ' + fullName + '</h3>',
      '<div class="ae-mod-preview ' + modClass + '">' + modStr + '</div>',
      '<div class="ability-editor-stepper">',
      '  <button id="ae-minus"' + (score <= 1 ? ' disabled style="opacity:0.3"' : '') + '>−</button>',
      '  <span class="ae-score-display" id="ae-score-text">' + score + '</span>',
      '  <button id="ae-plus"' + (score >= 30 ? ' disabled style="opacity:0.3"' : '') + '>+</button>',
      '</div>',
      '<div class="ability-editor-actions">',
      '  <button class="ae-cancel">Cancel</button>',
      '  <button class="ae-save">Save</button>',
      '</div>'
    ].join('');

    card.querySelector('#ae-score-text').addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'number'; input.min = 1; input.max = 30;
      input.className = 'ae-score-input'; input.value = score;
      const display = card.querySelector('#ae-score-text');
      display.replaceWith(input); input.focus(); input.select();
      input.addEventListener('blur', () => {
        score = Math.max(1, Math.min(30, parseInt(input.value) || score));
        render();
      });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); });
    });

    card.querySelector('#ae-minus').onclick = () => { score = Math.max(1, score - 1); render(); };
    card.querySelector('#ae-plus').onclick = () => { score = Math.min(30, score + 1); render(); };
    card.querySelector('.ae-cancel').onclick = () => overlay.remove();
    card.querySelector('.ae-save').onclick = () => {
      state.abilities[statKey] = score;
      // Update the grid display
      const valEl = document.getElementById('ab-val-' + statKey);
      const modEl = document.getElementById('ab-mod-' + statKey);
      if (valEl) valEl.textContent = score;
      if (modEl) modEl.textContent = modStr;
      updateAsiPreview();
      overlay.remove();
    };
  }

  render();
  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

function updateAsiPreview() {
  const r = state.race ? RACES[state.race] : null;
  if (!r) return;
  let bonuses = {...r.asi};
  if (state.subrace && SUBASIS[state.subrace]) Object.entries(SUBASIS[state.subrace]).forEach(([k,v]) => bonuses[k] = (bonuses[k]||0)+v);
  if (state.race === 'Half-Elf' && state.asi_picks.length === 2) state.asi_picks.forEach(a => bonuses[a] = (bonuses[a]||0)+1);
  if (FLEXIBLE_ASI.has(state.race) && state.asi_picks.length === 1) bonuses[state.asi_picks[0]] = (bonuses[state.asi_picks[0]]||0)+2;
  // MPMM: the chosen spread REPLACES the record's default +2 STR/+1 WIS.
  const _mpmm = isMpmmRace() ? mpmmPicks() : null;
  if (_mpmm && _mpmm.length) { bonuses = {}; _mpmm.forEach(([a, v]) => bonuses[a] = v); }
  renderMpmmGrids();
  document.getElementById('asi-bonus').innerHTML = Object.entries(bonuses).map(([k,v]) => `${k.slice(0,3).toUpperCase()}+${v}`).join(' · ') || '';

  // Half-Elf ASI picks
  const he = document.getElementById('halfelf-picks');
  if (state.race === 'Half-Elf') {
    he.style.display = 'block';
    document.getElementById('halfelf-grid').innerHTML = ABILITIES.filter(a => a !== 'charisma').map(a => `
      <label class="option-card" style="cursor:pointer">
        <input type="checkbox" onchange="toggleAsiPick('${a}', this.checked)" style="pointer-events:none">
        ${a.slice(0,3).toUpperCase()} +1
      </label>`).join('');
  } else { he.style.display = 'none'; }

  // Flexible ASI pick (Custom Lineage + zero-ASI races)
  const cl = document.getElementById('customlineage-picks');
  if (FLEXIBLE_ASI.has(state.race)) {
    cl.style.display = 'block';
    document.getElementById('flex-asi-label').textContent = `${state.race}: +2 to one ability of your choice`;
    document.getElementById('customlineage-grid').innerHTML = ABILITIES.map(a => `
      <label class="option-card" style="cursor:pointer">
        <input type="radio" name="cl-asi" value="${a}" onchange="setLineageAsi('${a}')" style="pointer-events:none">
        ${a.slice(0,3).toUpperCase()} +2
      </label>`).join('');
  } else { cl.style.display = 'none'; }
}

function toggleAsiPick(a, checked) {
  if (checked) { if (state.asi_picks.length < 2) state.asi_picks.push(a); }
  else { state.asi_picks = state.asi_picks.filter(x => x !== a); }
  updateAsiPreview();
}

// ── MPMM flexible ASI (Tortle and any future MMOM race) ──────────────────────
// Monsters of the Multiverse: +2 to one ability and +1 to a different one, or +1 to three
// different ones (MPMM p.5, "Ability Score Increases" — chapter-wide, not repeated per race). That pick REPLACES the record's default +2 STR/+1 WIS spread —
// the server does the same swap in routes/characters/creation.py. Nothing picked = the default
// stands, which MPMM also permits (the picker says so on screen).
// Monsters of the Multiverse's flexible spread applies to some races this app models as
// SUBRACES (Genasi's Air/Earth/Fire/Water are four races in MPMM p.16-17), so the entry may
// match either name. Every MPMM check goes through here — testing state.race alone made the
// Genasi entries unreachable.
function isMpmmRace() {
  return MPMM_ASI.has(state.race) || (!!state.subrace && MPMM_ASI.has(state.subrace));
}

function mpmmPicks() {
  if (state.mpmm_mode === 'two' && state.mpmm_p2 && state.mpmm_p1 && state.mpmm_p2 !== state.mpmm_p1)
    return [[state.mpmm_p2, 2], [state.mpmm_p1, 1]];
  if (state.mpmm_mode === 'three') {
    const t = state.mpmm_t3.filter(Boolean);
    if (new Set(t).size === 3) return t.map(a => [a, 1]);
  }
  return [];
}
function setMpmmMode(mode) {
  state.mpmm_mode = mode; state.mpmm_p2 = ''; state.mpmm_p1 = ''; state.mpmm_t3 = [];
  updateAsiPreview();
}
function setMpmmPick(slot, a) {
  if (state.mpmm_mode === 'three') {
    const t = state.mpmm_t3.slice(); t[slot] = a; state.mpmm_t3 = t;
  } else if (slot === 0) {
    state.mpmm_p2 = a; if (state.mpmm_p1 === a) state.mpmm_p1 = '';
  } else {
    state.mpmm_p1 = a; if (state.mpmm_p2 === a) state.mpmm_p2 = '';
  }
  updateAsiPreview();
}
function renderMpmmHint() {
  // Tell the truth about a blank picker: a race whose record carries a spread keeps it, but a
  // race ingested from MPMM has an all-zero record — there a blank picker means no bonus at all.
  const hint = document.getElementById('mpmm-hint');
  if (!hint) return;
  const _recAsi = (RACES[state.race] || {}).asi || {};
  const _hasDefault = Object.values(_recAsi).some(v => v > 0);
  if (mpmmPicks().length) {
    hint.style.color = 'var(--text-muted)';
    hint.textContent = 'Applied to your scores below.';
  } else if (_hasDefault) {
    hint.style.color = 'var(--text-muted)';
    hint.textContent = 'Leave both blank and you keep the classic +2 STR / +1 WIS.';
  } else {
    hint.style.color = 'var(--danger)';
    hint.textContent = '⚠ Pick a spread — ' + state.race + ' has no fixed increase in Monsters of the Multiverse, so a blank picker means no bonus at all.';
  }
}
function renderMpmmGrids() {
  const box = document.getElementById('mpmm-picks');
  if (!box) return;
  if (!isMpmmRace()) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  renderMpmmHint();
  document.querySelectorAll('input[name="mpmm-mode"]').forEach(el => { el.checked = (el.value === state.mpmm_mode); });
  const host = document.getElementById('mpmm-grids');
  if (!state.mpmm_mode) { host.innerHTML = ''; return; }
  const rows = state.mpmm_mode === 'three'
    ? [0, 1, 2].map(i => ({ slot: i, label: '+1', val: state.mpmm_t3[i] || '' }))
    : [{ slot: 0, label: '+2', val: state.mpmm_p2 }, { slot: 1, label: '+1', val: state.mpmm_p1 }];
  host.innerHTML = rows.map(({slot, label, val}) => `
    <div style="margin-bottom:0.5rem">
      <p style="font-size:0.8rem;color:var(--text-muted);margin:0 0 0.25rem">${label} → <strong>${val ? val.slice(0,3).toUpperCase() : '—'}</strong></p>
      <div class="grid-2">
        ${ABILITIES.filter(a => a === val || !rows.some(r => r.val === a)).map(a => `
          <label class="option-card" style="cursor:pointer">
            <input type="radio" name="mpmm-asi-${slot}" value="${a}" ${val === a ? 'checked' : ''} onchange="setMpmmPick(${slot},'${a}')" style="pointer-events:none">
            ${a.slice(0,3).toUpperCase()} ${label}
          </label>`).join('')}
      </div>
    </div>`).join('');
  renderMpmmHint();
}
function setLineageAsi(a) {
  state.asi_picks = [a];
  updateAsiPreview();
}
