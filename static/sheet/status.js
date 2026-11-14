/* sheet/status.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Conditions ──
let _conditionsExpanded = true;
const CONDITION_COLORS = window.DND_CONDITIONS.colors;
const STANDARD_CONDITIONS = window.DND_CONDITIONS.list;
const EXHAUSTION_LEVELS = window.DND_CONDITIONS.exhaustionLevels;

function openConditionPicker() {
  let html = '<div class="cond-popout" id="cond-picker" style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:2000;width:380px;max-width:92vw;max-height:75vh;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.8rem;box-shadow:0 4px 12px rgba(0,0,0,0.4)">';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><strong style="font-size:0.8rem">Add Condition</strong><span onclick="closeConditionPicker()" style="cursor:pointer;font-size:0.8rem">✕</span></div>';
  for (const c of STANDARD_CONDITIONS) {
    if (c.name === 'Exhaustion') {
      html += '<div onclick="openExhaustionPicker()" style="padding:0.3rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.15rem;font-size:0.68rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem" onmouseover="this.style.background=\'var(--bg)\'" onmouseout="this.style.background=\'transparent\'">';
      html += '<span class="cond-badge" style="flex-shrink:0;background:'+CONDITION_COLORS[c.name]+';border-color:'+CONDITION_COLORS[c.name].split(';')[0]+'">'+c.name+'</span>';
      html += '<span style="color:var(--text-muted);font-size:0.62rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">Pick level 1–6…</span></div>';
    } else {
    html += '<div onclick="addCondition(\''+c.name+'\', \''+c.desc.replace(/'/g,"\\'")+'\');closeConditionPicker()" style="padding:0.3rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.15rem;font-size:0.68rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem" onmouseover="this.style.background=\'var(--bg)\'" onmouseout="this.style.background=\'transparent\'">';
    html += '<span class="cond-badge" style="flex-shrink:0;background:'+CONDITION_COLORS[c.name]+';border-color:'+CONDITION_COLORS[c.name].split(';')[0]+'">'+c.name+'</span>';
    html += '<span style="color:var(--text-muted);font-size:0.62rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">'+c.desc+'</span></div>';
    }
  }
  html += '<div style="margin-top:0.5rem;border-top:1px solid var(--border);padding-top:0.4rem"><input id="custom-cond-name" placeholder="Custom condition…" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.68rem;margin-bottom:0.25rem"><button onclick="addCondition(document.getElementById(\'custom-cond-name\').value,\'\');closeConditionPicker()" style="width:100%;padding:0.25rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:0.68rem">Add Custom</button></div>';
  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);
}

function closeConditionPicker() {
  const el = document.getElementById('cond-picker');
  if (el) el.remove();
}

function openExhaustionPicker() {
  const el = document.getElementById('cond-picker');
  if (!el) return;
  el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><a href="#" onclick="event.preventDefault();closeConditionPicker();openConditionPicker()" style="font-size:0.72rem;color:var(--text-muted)">← Back</a><strong style="font-size:0.8rem">Exhaustion Level</strong><span onclick="closeConditionPicker()" style="cursor:pointer;font-size:0.8rem">✕</span></div>';
  for (const e of EXHAUSTION_LEVELS) {
    const label = 'Exhaustion '+e.level;
    const color = CONDITION_COLORS['Exhaustion'] || '#827717;color:#fff';
    el.innerHTML += '<div onclick="addCondition(\''+label+'\', \'Level '+e.level+': '+e.desc.replace(/'/g,"\\'")+'\');closeConditionPicker()" style="padding:0.3rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.15rem;font-size:0.7rem;display:flex;align-items:center;gap:0.4rem" onmouseover="this.style.background=\'var(--bg)\'" onmouseout="this.style.background=\'transparent\'"><span class="cond-badge" style="flex-shrink:0;background:'+color+';border-color:'+color.split(';')[0]+'">'+label+'</span><span style="color:var(--text-muted);font-size:0.62rem;flex:1">'+e.desc+'</span></div>';
  }
}

async function addCondition(name, desc) {
  if (!name) return;
  try {
    const r = await fetch('/api/character/'+CHAR_ID+'/conditions', {
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name,description:desc||'',source:''})
    });
    const d = await r.json();
    renderConditions(d.conditions);
  } catch(e) { /* silent */ }
}

async function dismissCondition(name) {
  try {
    const r = await fetch('/api/character/'+CHAR_ID+'/conditions', {
      method:'DELETE',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name})
    });
    const d = await r.json();
    renderConditions(d.conditions);
  } catch(e) { /* silent */ }
}

function renderConditions(conditions) {
  const container = document.getElementById('condition-badges');
  if (!container) return;
  if (!conditions || !conditions.length) {
    container.innerHTML = '<span style="font-size:0.72rem;color:var(--text-muted);opacity:0.5">None</span>';
    return;
  }
  let html = '';
  for (const c of conditions) {
    const style = CONDITION_COLORS[c.name] || 'var(--accent);color:#fff;border-color:var(--accent)';
    html += '<span class="cond-badge" style="background:'+style.split(';')[0]+';'+style.split(';')[1]+'" data-name="'+c.name+'" onclick="toggleConditionDetail(event,\''+c.name+'\',\''+(c.description||'').replace(/'/g,"\\'")+'\')">'+c.name+'<span class="dismiss" onclick="event.stopPropagation();dismissCondition(\''+c.name+'\')">✕</span></span>';
  }
  container.innerHTML = html;
}

function toggleConditions() {
  _conditionsExpanded = !_conditionsExpanded;
  document.getElementById('cond-chevron').textContent = _conditionsExpanded ? '▾' : '▸';
  const container = document.getElementById('condition-badges');
  if (container) container.style.display = _conditionsExpanded ? '' : 'none';
}

function toggleConditionDetail(event, name, desc) {
  event.stopPropagation();
  // Remove any existing popouts
  document.querySelectorAll('.cond-detail-popout').forEach(el => el.remove());
  const badge = event.currentTarget;
  const popout = document.createElement('div');
  popout.className = 'cond-popout cond-detail-popout';
  popout.innerHTML = '<strong style="color:'+(CONDITION_COLORS[name]||'var(--text)').split(';')[0]+'">'+name+'</strong><div class="cond-desc">'+desc+'</div><button class="cond-dismiss" onclick="dismissCondition(\''+name+'\');this.parentElement.remove()">Remove</button>';
  document.body.appendChild(popout);
  const rect = badge.getBoundingClientRect();
  popout.style.left = (rect.left) + 'px';
  popout.style.top = (rect.bottom + 4) + 'px';
  // Dismiss on click outside the popout
  document.addEventListener('click', function _dismissOut(e) {
    if (!popout.isConnected) {
      document.removeEventListener('click', _dismissOut);
      return;
    }
    if (!popout.contains(e.target)) {
      popout.remove();
      document.removeEventListener('click', _dismissOut);
    }
  });
}

// ── Personality Section toggle ──
function togglePersonalitySection() {
  const body = document.getElementById('personality-body');
  const arrow = document.getElementById('personality-arrow');
  if (!body || !arrow) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  arrow.style.transform = open ? 'rotate(0deg)' : 'rotate(90deg)';
}

// ── Death Saves ──
function toggleDeathSaves() {
  const body = document.getElementById('ds-body');
  const toggle = document.getElementById('ds-toggle');
  if (!body || !toggle) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  toggle.textContent = open ? '▸' : '▾';
}

function toggleSensesBlurb() {
  const body = document.getElementById('senses-blurb');
  const toggle = document.getElementById('senses-blurb-toggle');
  if (!body || !toggle) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  toggle.textContent = open ? '▸' : '▾';
}

function toggleSavesBlurb() {
  const body = document.getElementById('saves-blurb');
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
}

function toggleDefensesBlurb() {
  const body = document.getElementById('defenses-blurb');
  if (!body) return;
  body.style.display = body.style.display !== 'none' ? 'none' : 'block';
}

function toggleProfsBlurb() {
  const body = document.getElementById('profs-blurb');
  if (!body) return;
  body.style.display = body.style.display !== 'none' ? 'none' : 'block';
}

// ── Sidebar Section Toggles ──
function toggleSection(bodyId, arrowId) {
  const body = document.getElementById(bodyId);
  const arrow = document.getElementById(arrowId);
  if (!body || !arrow) return;
  if (body.style.display === 'none') {
    body.style.display = '';
    arrow.textContent = '▾';
  } else {
    body.style.display = 'none';
    arrow.textContent = '▸';
  }
}

// ── Combat Notes auto-save ────────────────────────────────────────────
let _combatNotesTimer = null;
function autoSaveCombatNotes() {
  clearTimeout(_combatNotesTimer);
  _combatNotesTimer = setTimeout(async () => {
    const ta = document.getElementById('combat-notes');
    if (!ta) return;
    try {
      await fetch(`/api/character/${CHAR_ID}/combat-notes`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notes: ta.value})
      });
    } catch(e) { /* silent */ }
  }, 800);
}
function clearCombatNotes(btn) {
  const ta = document.getElementById('combat-notes');
  if (!ta) return;
  ta.value = '';
  btn.textContent = '✓ Cleared';
  setTimeout(() => { btn.textContent = '🗑 Clear'; }, 1500);
  autoSaveCombatNotes();
}

// ── Spell level collapsible sections ────────────────────────────────────
function toggleSpellLevel(header) {
  header.classList.toggle('open');
  header.nextElementSibling.classList.toggle('open');
}

function toggleAllSpellLevels() {
  const btn = document.getElementById('spell-toggle-all-btn');
  const allOpen = [...document.querySelectorAll('.spell-level-header')].every(h => h.classList.contains('open'));
  document.querySelectorAll('.spell-level-header').forEach(h => {
    if (allOpen) { h.classList.remove('open'); h.nextElementSibling.classList.remove('open'); }
    else { h.classList.add('open'); h.nextElementSibling.classList.add('open'); }
  });
  if (btn) btn.textContent = allOpen ? '▸ All' : '▾ All';
}

// ── Hit Dice ──
function toggleHitDice() {
  const body = document.getElementById('hd-body');
  const toggle = document.getElementById('hd-toggle');
  if (!body || !toggle) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  toggle.textContent = open ? '▸' : '▾';
}

let hdUsed = HD_USED;

function showHDResult(msg, isGood) {
  const el = document.getElementById('hd-result');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.style.borderColor = isGood ? 'var(--success)' : 'var(--text-muted)';
}

async function spendHitDice() {
  const remaining = HD_TOTAL - hdUsed;
  if (remaining <= 0) {
    showHDResult('No Hit Dice remaining. Take a Long Rest to recover some.', false);
    return;
  }
  const roll = Math.floor(Math.random() * HD_DIE) + 1;
  const heal = roll + CON_MOD;
  const gain = Math.max(0, heal);

  // Update HP
  const curEl = document.getElementById('hp-cur');
  const hp = Math.min(parseInt(curEl.textContent) + gain, HP_MAX);
  curEl.textContent = hp;

  // Update HP bar
  const pct = HP_MAX > 0 ? (hp / HP_MAX * 100) : 0;
  const bar = document.getElementById('hp-bar-side');
  if (bar) {
    bar.style.width = pct + '%';
    bar.classList.toggle('danger', hp > 0 && pct < 25);
    bar.classList.toggle('warn', hp > 0 && pct >= 25 && pct < 50);
  }

  // Increment used counter
  hdUsed++;
  const remainEl = document.getElementById('hd-remaining');
  if (remainEl) remainEl.textContent = HD_TOTAL - hdUsed;

  // API call
  await sheetApi('/update', { hp_current: hp, hit_dice_used: hdUsed });

  showHDResult(`Rolled ${roll} on d${HD_DIE} + ${CON_MOD} = ${heal} HP regained (${remaining - 1} HD remaining)`, true);
}

async function rollMax() {
  const remaining = HD_TOTAL - hdUsed;
  if (remaining <= 0) {
    showHDResult('No Hit Dice remaining.', false);
    return;
  }
  const heal = HD_DIE + CON_MOD;
  const gain = Math.max(0, heal);

  const curEl = document.getElementById('hp-cur');
  const hp = Math.min(parseInt(curEl.textContent) + gain, HP_MAX);
  curEl.textContent = hp;

  const pct = HP_MAX > 0 ? (hp / HP_MAX * 100) : 0;
  const bar = document.getElementById('hp-bar-side');
  if (bar) {
    bar.style.width = pct + '%';
    bar.classList.toggle('danger', hp > 0 && pct < 25);
    bar.classList.toggle('warn', hp > 0 && pct >= 25 && pct < 50);
  }

  hdUsed++;
  const remainEl = document.getElementById('hd-remaining');
  if (remainEl) remainEl.textContent = HD_TOTAL - hdUsed;

  await sheetApi('/update', { hp_current: hp, hit_dice_used: hdUsed });

  showHDResult(`Max roll on d${HD_DIE} + ${CON_MOD} = ${heal} HP regained (${remaining - 1} HD remaining)`, true);
}

function longRestConfirm() {
  // Show modal overlay
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const modal = document.createElement('div');
  modal.style.cssText = 'background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:400px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.4)';

  const recover = Math.max(1, Math.floor(HD_TOTAL / 2));
  const curHp = parseInt(document.getElementById('hp-cur').textContent);
  const hpRestore = HP_MAX - curHp;

  modal.innerHTML = `
    <div style="font-size:1.2rem;font-weight:700;margin-bottom:0.75rem;display:flex;align-items:center;gap:0.5rem">
      <span>🛌</span> Long Rest
    </div>
    <div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:1rem">
      <div style="margin-bottom:0.3rem">• Restore <strong style="color:var(--success)">${hpRestore} HP</strong> (full heal)</div>
      <div style="margin-bottom:0.3rem">• Recover up to <strong style="color:var(--accent)">${recover}</strong> Hit Dice</div>
      <div style="margin-bottom:0.3rem">• Reset <strong style="color:var(--danger)">Death Saves</strong> (successes & failures)</div>
      <div style="margin-bottom:0.3rem">• Reset <strong style="color:var(--accent)">Spell Slots</strong> (all levels)</div>
      <div style="margin-bottom:0.3rem">• Restore <strong>Class Features</strong> (Channel Divinity, Rage, etc.)</div>
      <div style="margin-bottom:0.3rem">• Reduce <strong>Exhaustion</strong> by 1</div>
      <div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-muted);opacity:0.7">This action cannot be undone.</div>
    </div>
    <div style="display:flex;gap:0.5rem;justify-content:flex-end">
      <button id="lr-cancel" style="padding:0.4rem 1rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer">Cancel</button>
      <button id="lr-confirm" style="padding:0.4rem 1rem;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-weight:600">Long Rest</button>
    </div>
  `;

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  document.getElementById('lr-cancel').onclick = () => overlay.remove();
  document.getElementById('lr-confirm').onclick = () => {
    overlay.remove();
    executeLongRest();
  };
}

async function executeLongRest() {
  // 1. Restore HP to max
  const hp = HP_MAX;
  const curEl = document.getElementById('hp-cur');
  curEl.textContent = hp;
  const pct = HP_MAX > 0 ? 100 : 0;
  const bar = document.getElementById('hp-bar-side');
  if (bar) {
    bar.style.width = pct + '%';
    bar.classList.remove('danger', 'warn');
  }

  // 2. Recover up to half total Hit Dice
  const recover = Math.max(1, Math.floor(HD_TOTAL / 2));
  const newUsed = Math.max(0, hdUsed - recover);
  const recovered = hdUsed - newUsed;

  hdUsed = newUsed;
  const remainEl = document.getElementById('hd-remaining');
  if (remainEl) remainEl.textContent = HD_TOTAL - hdUsed;

  // 3. Reset death saves
  const dsSuccessDots = document.querySelectorAll('#ds-body .ds-dot.filled-success');
  const dsFailDots = document.querySelectorAll('#ds-body .ds-dot.filled-fail');
  dsSuccessDots.forEach(d => d.classList.remove('filled-success'));
  dsFailDots.forEach(d => d.classList.remove('filled-fail'));

  // 4. Reset spell slots — clear all level-based and per-spell used dots
  document.querySelectorAll('.slot-box.used').forEach(d => d.classList.remove('used'));

  // Reset per-spell slots_used in DB — collect all spell IDs and reset to 0
  const spellSlotElements = document.querySelectorAll('[id^="sp-slots-"]');
  const spellResets = [];
  spellSlotElements.forEach(el => {
    const id = el.id.replace('sp-slots-', '');
    if (id) spellResets.push({ id: parseInt(id), slots_used: 0 });
  });

  // 5. Reset limited-use class features (Channel Divinity, Rage, etc.)
  resetAllFeatureUses();

  // 6. Reduce exhaustion by 1 (floor at 0) — also in DB
  const exhaustionEl = document.querySelector('#hd-body [style*="font-size:0.78rem"]');
  // We track it in the DB update below

  // API: update HP, hit_dice_used, death saves reset, spell slots, exhaustion
  await sheetApi('/update', {
    hp_current: hp,
    hit_dice_used: hdUsed,
    death_saves_success: 0,
    death_saves_fail: 0,
    spell_slots_used: JSON.stringify({}),
    spells: spellResets
  });

  // Save reset feature uses to DB separately
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });

  // Reset Magic Initiate 1/day uses — update DOM directly so it reflects immediately
  try {
    await fetch('/api/character/' + CHAR_ID + '/magic-initiate/reset', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    // Update the Magic Initiate card button on the spells tab
    const miCard = document.getElementById('mi-spells-card');
    if (miCard) {
      const btn = miCard.querySelector('button');
      if (btn) {
        btn.textContent = 'Available';
        btn.style.background = 'var(--success)';
      }
    }
    // Also update passive chip buttons (features tab)
    document.querySelectorAll('.passive-chip-body button').forEach(btn => {
      if (btn.textContent.trim() === 'Used ✓') {
        btn.textContent = 'Available';
        btn.style.background = 'var(--success)';
      }
    });
  } catch(e) {}

  // Show result notification
  const hdResult = document.getElementById('hd-result');
  if (hdResult) {
    const lines = [];
    lines.push(`Restored ${HP_MAX} HP`);
    if (recovered > 0) lines.push(`Recovered ${recovered} Hit Dice`);
    lines.push('Reset Death Saves, Spell Slots, Class Features');
    hdResult.textContent = 'Long Rest complete — ' + lines.join(', ');
    hdResult.style.display = 'block';
    hdResult.style.borderColor = 'var(--success)';
    hdResult.style.background = 'rgba(0,200,83,0.05)';
  }
}

// ── Short Rest ──
let _srRolls = [];  // module-level roll storage

function shortRestConfirm() {
  const curHp = parseInt(document.getElementById('hp-cur').textContent);
  const hpMissing = HP_MAX - curHp;
  const available = HD_TOTAL - hdUsed;
  _srRolls = [];
  let diceToSpend = Math.min(available, Math.max(0, Math.ceil(hpMissing / (HD_DIE / 2 + 2))));
  if (diceToSpend < 0) diceToSpend = 0;

  if (available <= 0 && hpMissing <= 0) {
    doShortRest(0, []);
    return;
  }

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const modal = document.createElement('div');
  modal.style.cssText = 'background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.4)';

  function render() {
    const canSpend = Math.min(available, Math.max(0, diceToSpend));
    let totalHeal = 0;
    const rollDetails = _srRolls.map((r) => {
      const hv = r + Math.max(0, CON_MOD);
      totalHeal += hv;
      return `<span style="color:var(--success)">d${HD_DIE}: ${r} + ${Math.max(0, CON_MOD)} = ${hv}</span>`;
    }).join(', ');

    const finalHp = Math.min(HP_MAX, curHp + totalHeal);
    const actualHeal = finalHp - curHp;

    modal.innerHTML = `
      <div style="font-size:1.2rem;font-weight:700;margin-bottom:0.75rem;display:flex;align-items:center;gap:0.5rem">
        <span>☕</span> Short Rest
      </div>
      <div style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.5rem">
        <div style="margin-bottom:0.4rem;display:flex;justify-content:space-between;align-items:center">
          <span>Spend Hit Dice:</span>
          <div style="display:flex;gap:0.3rem;align-items:center">
            <button class="sr-adjust" data-delta="-1" style="width:28px;height:28px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-weight:700;font-size:1rem">−</button>
            <span style="font-weight:700;font-size:1.1rem;color:var(--text);min-width:2rem;text-align:center">${canSpend}</span>
            <button class="sr-adjust" data-delta="+1" style="width:28px;height:28px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-weight:700;font-size:1rem">+</button>
            <button class="sr-max" style="padding:0.2rem 0.5rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;font-size:0.65rem">Max</button>
          </div>
        </div>
        <div style="margin-bottom:0.4rem;font-size:0.75rem">
          Available: <strong style="color:var(--text)">${available}</strong> d${HD_DIE} · Con mod: <strong style="color:var(--text)">+${Math.max(0, CON_MOD)}</strong>
        </div>
        ${_srRolls.length > 0 ? `
          <div style="margin-bottom:0.4rem;padding:0.35rem;background:var(--bg);border-radius:6px;border:1px solid var(--border);font-size:0.78rem;line-height:1.6">
            <div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:0.15rem">Roll results:</div>
            ${rollDetails}
            <div style="margin-top:0.3rem;border-top:1px solid var(--border);padding-top:0.3rem;font-weight:600">
              Total heal: <strong style="color:var(--success)">+${actualHeal} HP</strong>
              <span style="color:var(--text-muted);font-weight:400"> (${curHp} → ${finalHp})</span>
            </div>
          </div>
        ` : ''}
        <div style="margin-top:0.4rem;display:flex;gap:0.25rem;">
          ${canSpend > 0 ? `<button id="sr-roll-btn" style="padding:0.3rem 0.8rem;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-weight:600;font-size:0.8rem">🎲 Roll ${canSpend} HD</button>` : ''}
          <button id="sr-skip-btn" style="padding:0.3rem 0.8rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:0.8rem">Skip Rolls</button>
        </div>
      </div>
      <div style="margin-bottom:0.5rem;font-size:0.75rem;color:var(--text-muted);opacity:0.7">
        • Short rest: at least 1 hour of downtime<br>
        • Reset features &amp; spell slots (Channel Divinity, Action Surge, Ki, Pact Magic, etc.)
      </div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end">
        <button id="sr-cancel-btn" style="padding:0.4rem 1rem;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer">Cancel</button>
        <button id="sr-confirm-btn" style="padding:0.4rem 1rem;border-radius:6px;border:none;background:var(--success);color:#fff;cursor:pointer;font-weight:600">Take Short Rest</button>
      </div>
    `;

    // Wire event listeners
    modal.querySelectorAll('.sr-adjust').forEach(btn => {
      btn.onclick = () => {
        diceToSpend = Math.max(0, Math.min(available, diceToSpend + parseInt(btn.dataset.delta)));
        render();
      };
    });
    modal.querySelector('.sr-max').onclick = () => { diceToSpend = available; render(); };
    const rollBtn = modal.querySelector('#sr-roll-btn');
    if (rollBtn) rollBtn.onclick = () => {
      const n = Math.min(available, Math.max(0, diceToSpend));
      for (let i = 0; i < n; i++) {
        _srRolls.push(Math.floor(Math.random() * HD_DIE) + 1);
      }
      diceToSpend = 0;
      render();
    };
    modal.querySelector('#sr-skip-btn').onclick = () => { overlay.remove(); doShortRest(0, _srRolls); };
    modal.querySelector('#sr-cancel-btn').onclick = () => overlay.remove();
    modal.querySelector('#sr-confirm-btn').onclick = () => {
      const healAmt = _srRolls.reduce((s, r) => s + r + Math.max(0, CON_MOD), 0);
      overlay.remove();
      doShortRest(healAmt, _srRolls);
    };
  }

  render();
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
}

async function doShortRest(healed, rolls) {
  const curHp = parseInt(document.getElementById('hp-cur').textContent);
  const newHp = Math.min(HP_MAX, curHp + healed);
  const diceUsed = rolls ? rolls.length : 0;

  // Apply HP
  const curEl = document.getElementById('hp-cur');
  curEl.textContent = newHp;
  const pct = HP_MAX > 0 ? (newHp / HP_MAX * 100) : 0;
  const bar = document.getElementById('hp-bar-side');
  if (bar) {
    bar.style.width = pct + '%';
    bar.classList.toggle('danger', newHp > 0 && pct < 25);
    bar.classList.toggle('warn', newHp > 0 && pct >= 25 && pct < 50);
  }

  // Update Hit Dice used
  if (diceUsed > 0) {
    hdUsed += diceUsed;
    const remainEl = document.getElementById('hd-remaining');
    if (remainEl) remainEl.textContent = HD_TOTAL - hdUsed;
  }

  // Reset short-rest features (Channel Divinity, Action Surge, Ki, etc.)
  resetShortRestFeatures();

  // Warlock Pact Magic: recover all spell slots on short rest (PHB p.107)
  // Other casters do NOT recover slots on short rest — that's a long rest only
  let spellUpdate = {};
  if (CASTER_TYPE === 'pact') {
    document.querySelectorAll('.slot-box.used').forEach(d => d.classList.remove('used'));
    spellUpdate = { spell_slots_used: JSON.stringify({}) };
  }

  // Arcane Recovery (Wizard, PHB p.115): recover slots = ceil(level/2) levels, max L5
  let arcaneUsed = false;
  if (cls === 'Wizard') {
    const arFeat = featureData.find(f => f.name && f.name.toLowerCase().includes('arcane recovery'));
    if (arFeat && arFeat.uses > 0) {
      const pool = Math.ceil(CHAR_LEVEL / 2);
      // Read current used slots from DOM
      const usedSlots = {};
      for (let lvl = 1; lvl <= 5; lvl++) {
        const tracker = document.getElementById(`slots-lvl-${lvl}`);
        if (tracker) {
          const used = tracker.querySelectorAll('.slot-box.used').length;
          if (used > 0) usedSlots[lvl] = used;
        }
      }
      // Recover highest-level slots first
      let remaining = pool;
      const recovered = {};
      for (let lvl = 5; lvl >= 1 && remaining > 0; lvl--) {
        const used = usedSlots[lvl] || 0;
        if (used > 0) {
          const toRecover = Math.min(used, Math.floor(remaining / lvl));
          if (toRecover > 0) {
            recovered[lvl] = toRecover;
            remaining -= toRecover * lvl;
            // Update DOM
            const boxes = document.querySelectorAll(`#slots-lvl-${lvl} .slot-box`);
            const newUsed = used - toRecover;
            boxes.forEach((b, i) => b.classList.toggle('used', i < newUsed));
          }
        }
      }
      // Merge with existing spellUpdate
      if (Object.keys(recovered).length > 0) {
        // Build new spell_slots_used: subtract recovered from current
        const ssu = {};
        for (let lvl = 1; lvl <= 9; lvl++) {
          const tracker = document.getElementById(`slots-lvl-${lvl}`);
          if (tracker) {
            const used = tracker.querySelectorAll('.slot-box.used').length;
            if (used > 0) ssu[lvl] = used;
          }
        }
        spellUpdate = Object.assign(spellUpdate, { spell_slots_used: JSON.stringify(ssu) });
        arcaneUsed = true;
        // Mark Arcane Recovery as used
        arFeat.uses = 0;
        renderFeatureDots(featureData.indexOf(arFeat));
      }
    }
  }

  // API: save HP, hit_dice_used, and feature_data
  await sheetApi('/update', Object.assign({ hp_current: newHp, hit_dice_used: hdUsed }, spellUpdate));
  await sheetApi('/update', { feature_data: JSON.stringify(featureData) });

  // Show result
  const hdResult = document.getElementById('hd-result');
  if (hdResult) {
    const parts = [];
    if (healed > 0) parts.push(`Regained ${healed} HP (${diceUsed} HD spent)`);
    if (diceUsed > 0) parts.push(`${diceUsed} HD used`);
    parts.push('Features reset');
    if (CASTER_TYPE === 'pact') parts.push('Pact slots recovered');
    if (arcaneUsed) parts.push(`Arcane Recovery (${Math.ceil(CHAR_LEVEL/2)} levels)`);
    hdResult.textContent = 'Short Rest — ' + parts.join(', ');
    hdResult.style.display = 'block';
    hdResult.style.borderColor = 'var(--success)';
    hdResult.style.background = 'rgba(0,200,83,0.05)';
  }
}

async function toggleDS(type, n) {
  await sheetApi('/update', { death_saves_success: type==='success' ? n : 0, death_saves_fail: type==='fail' ? n : 0 });
  location.reload();
}
async function resetDS() {
  await sheetApi('/update', { death_saves_success: 0, death_saves_fail: 0 });
  location.reload();
}

// ── Inspiration ──
async function toggleInspiration() {
  const btn = document.getElementById('insp-btn');
  const on = btn.classList.contains('on');
  btn.classList.toggle('on', !on);
  btn.classList.toggle('off', on);
  // Also update sidebar icon
  const sidebarIcon = document.querySelector('.combat-quick-item span[style*="font-size:1rem"]');
  if (sidebarIcon) sidebarIcon.textContent = !on ? '⭐' : '☆';
  await sheetApi('/update', { inspiration: on ? 0 : 1 });
}

