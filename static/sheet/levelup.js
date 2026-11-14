/* sheet/levelup.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Level Up Wizard ───────────────────────────────────────────────────
let luData = null;
let luStep = 0;
let luSteps = [];       // [{label, render}]
let luChoices = {};     // accumulated choices
let luClassToLevel = null;  // multiclass: which class to level

async function openLevelUp() {
  // Ask for target level
  const maxTarget = 20;
  const target = prompt(`Level to? (${CHAR_LEVEL + 1}–${maxTarget})`, CHAR_LEVEL + 1);
  if (!target) return;
  const targetNum = parseInt(target);
  if (isNaN(targetNum) || targetNum <= CHAR_LEVEL || targetNum > maxTarget) {
    alert(`Enter a level between ${CHAR_LEVEL + 1} and ${maxTarget}`);
    return;
  }
  return openLevelUpDirect(targetNum);
}

async function openLevelUpDirect(targetNum) {
  // Always level up one at a time — frontend wizard handles single-level jumps
  const stepTarget = CHAR_LEVEL + 1;
  if (stepTarget > 20) return;
  
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/level-up-info?target=${stepTarget}`);
    luData = await resp.json();
    if (luData.error) { alert(luData.error); return; }
  } catch(e) { console.error(e); alert('Failed to load level-up info'); return; }
  
  // Stash the ultimate goal so applyLevelUp knows where we're headed
  luData._ultimate_target = targetNum;
  
  luStep = 0;
  luChoices = {};
  
  // Update title
  document.getElementById('lu-title').textContent = `⬆ Level Up — L${luData.current_level} → L${luData.target_level}`;
  
  // Build steps
  luSteps = [];
  luSteps.push({label: 'Overview', render: renderLuOverview});
  
  // Class picker — show whenever multiclass options exist (PHB p.163: any level-up)
  if (luData.multiclass && luData.multiclass.options && luData.multiclass.options.length > 0) {
    luClassToLevel = luData.multiclass.class_to_level;
    luSteps.push({label: 'Class', render: renderLuClassPick});
  }
  
  luSteps.push({label: 'HP', render: renderLuHP});
  if (luData.asi) luSteps.push({label: 'ASI/Feat', render: renderLuASI});
  if (luData.fighting_style) luSteps.push({label: 'Fighting Style', render: renderLuFightingStyle});
  if (luData.expertise) luSteps.push({label: 'Expertise', render: renderLuExpertise});
  if (luData.metamagic) luSteps.push({label: 'Metamagic', render: renderLuMetamagic});
  if (luData.invocations) luSteps.push({label: 'Invocations', render: renderLuInvocations});
  if (luData.pact_boon) luSteps.push({label: 'Pact Boon', render: renderLuPactBoon});
  if (luData.maneuvers) luSteps.push({label: 'Maneuvers', render: renderLuManeuvers});
  if (luData.magical_secrets) luSteps.push({label: 'Magical Secrets', render: renderLuMagicalSecrets});
  if (luData.totem_spirit) luSteps.push({label: 'Totem Spirit', render: renderLuTotemSpirit});
  if (luData.hunters_prey) luSteps.push({label: "Hunter's Prey", render: renderLuHuntersPrey});
  if (luData.favored_enemy) luSteps.push({label: 'Favored Enemy', render: renderLuFavoredEnemy});
  if (luData.favored_terrain) luSteps.push({label: 'Favored Terrain', render: renderLuFavoredTerrain});
  if (luData.infusions) luSteps.push({label: 'Infusions', render: renderLuInfusions});
  if (luData.subclass) luSteps.push({label: luData.subclass.label, render: renderLuSubclass});
  if (luData.subclass_bonus_map && Object.keys(luData.subclass_bonus_map).length > 0) {
    luSteps.push({label: 'Bonuses', render: renderLuSubclassBonus});
  }
  if (luData.spells) luSteps.push({label: 'Spells', render: renderLuSpells});
  luSteps.push({label: 'Confirm', render: renderLuConfirm});
  
  // Render step dots
  const dots = document.getElementById('lu-steps');
  dots.innerHTML = luSteps.map((s, i) => 
    `<span class="lu-step-dot" id="lu-dot-${i}" style="display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;border-radius:50%;font-size:0.7rem;font-weight:600;background:var(--bg);border:2px solid var(--border);color:var(--text-muted)">${i+1}</span>`
  ).join(' ');
  
  // Show modal
  document.getElementById('levelup-modal').style.display = 'flex';
  renderLevelUpStep();
}

function closeLevelUp() {
  document.getElementById('levelup-modal').style.display = 'none';
  luData = null;
}

function levelUpStep(delta) {
  if (delta > 0 && luStep >= luSteps.length - 1) return;
  if (delta < 0 && luStep <= 0) return;
  luStep += delta;
  renderLevelUpStep();
}

function renderLevelUpStep() {
  const content = document.getElementById('lu-content');
  content.innerHTML = '';
  
  // Step dots
  document.querySelectorAll('.lu-step-dot').forEach((d, i) => {
    d.style.background = i === luStep ? 'var(--accent)' : (i < luStep ? 'var(--success)' : 'var(--bg)');
    d.style.borderColor = i === luStep ? 'var(--accent)' : (i < luStep ? 'var(--success)' : 'var(--border)');
    d.style.color = i <= luStep ? '#fff' : 'var(--text-muted)';
  });
  
  // Show/hide nav
  document.getElementById('lu-back').style.display = luStep > 0 ? '' : 'none';
  document.getElementById('lu-next').style.display = luStep < luSteps.length - 1 ? '' : 'none';
  document.getElementById('lu-confirm').style.display = luStep === luSteps.length - 1 ? '' : 'none';
  
  // Render step content
  if (luSteps[luStep] && luSteps[luStep].render) {
    luSteps[luStep].render(content);
  }
}

function renderLuOverview(el) {
  const d = luData;
  let html = `<div style="font-size:0.9rem;line-height:1.6">`;
  html += `<p><strong>Class:</strong> ${d.class_name}</p>`;
  html += `<p><strong>New Level:</strong> ${d.target_level}</p>`;
  html += `<p><strong>HP:</strong> Gain 1d12 + CON (average ${d.hp.average}, max ${d.hp.max_roll})</p>`;
  if (d.all_features && d.all_features.length) {
    html += `<p><strong>New Features:</strong></p><ul>`;
    d.all_features.forEach(f => html += `<li>${f}</li>`);
    html += `</ul>`;
  }
  if (d.proficiency_bonus.changed) {
    html += `<p><strong>Proficiency Bonus:</strong> +${d.proficiency_bonus.old} → <strong style="color:var(--accent)">+${d.proficiency_bonus.new}</strong></p>`;
  }
  if (d.asi) html += `<p><strong>Ability Score Improvement</strong> at this level!</p>`;
  if (d.subclass) html += `<p><strong>${d.subclass.label}</strong> choice at this level!</p>`;
  if (d.spells) html += `<p><strong>Spellcasting</strong> updates available.</p>`;
  html += `</div>`;
  el.innerHTML = html;
}

function renderLuClassPick(el) {
  const mc = luData.multiclass;
  const cl = mc.class_levels || {};
  const curClass = mc.class_to_level;
  
  // Build current class levels display
  const lvlDisplay = Object.entries(cl).map(([c, l]) => `${c} ${l}`).join(' / ');
  
  let html = `<div style="font-size:0.9rem;line-height:1.6">
    <h3 style="margin-bottom:0.5rem">Choose Class to Level</h3>
    <p style="color:var(--text-muted);margin-bottom:1rem">Current: <strong>${lvlDisplay}</strong></p>`;
  
  // ── Existing classes (always shown first) ──
  html += `<p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.3rem">Continue existing class:</p>`;
  html += `<div style="display:flex;flex-direction:column;gap:0.4rem;margin-bottom:0.75rem">`;
  for (const [clsName, clsLvl] of Object.entries(cl)) {
    const checked = luClassToLevel === clsName;
    html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer">
      <input type="radio" name="lu-class" value="${clsName}" ${checked ? 'checked' : ''} onchange="luClassToLevel=this.value">
      <strong>${clsName}</strong>
      <span style="font-size:0.75rem;color:var(--text-muted)">Lv ${clsLvl} → Lv ${clsLvl + 1}</span>
    </label>`;
  }
  html += `</div>`;
  
  // ── New multiclass options ──
  if (mc.options && mc.options.length > 0) {
    const newOpts = mc.options.filter(o => !cl[o.class]);
    if (newOpts.length > 0) {
      // Split eligible vs ineligible
      const eligible = newOpts.filter(o => o.available);
      const ineligible = newOpts.filter(o => !o.available);
      html += `<p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.3rem">Multiclass into a new class:</p>`;
      html += `<div style="display:flex;flex-direction:column;gap:0.4rem;margin-bottom:0.75rem">`;
      // Eligible first
      eligible.forEach(opt => {
        const checked = luClassToLevel === opt.class;
        html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer">
          <input type="radio" name="lu-class" value="${opt.class}" ${checked ? 'checked' : ''} onchange="luClassToLevel=this.value">
          <strong>${opt.class}</strong>
          <span style="font-size:0.7rem;color:var(--accent)">(new)</span>
        </label>`;
      });
      html += `</div>`;
      // Ineligible (grayed out)
      if (ineligible.length > 0) {
        html += `<p style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.3rem">Locked (missing prerequisites):</p>`;
        html += `<div style="display:flex;flex-direction:column;gap:0.3rem;opacity:0.5">`;
        ineligible.forEach(opt => {
          const missing = opt.missing ? Object.entries(opt.missing).map(([s,v]) => `${s} ${v}`).join(', ') : 'prerequisites not met';
          html += `<div style="display:flex;align-items:center;gap:0.5rem;padding:0.4rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px">
            <span style="font-size:0.8rem;color:var(--text-muted)">🔒 <strong>${opt.class}</strong></span>
            <span style="font-size:0.65rem;color:var(--text-muted)">needs ${missing}</span>
          </div>`;
        });
        html += `</div>`;
      }
    }
  }
  
  html += `</div>`;
  el.innerHTML = html;
  if (!luClassToLevel) luClassToLevel = curClass;
}

function renderLuHP(el) {
  const d = luData;
  const avg = d.hp.average;
  const max = d.hp.max_roll;
  let html = `<div>
    <p style="margin-bottom:1rem">Roll ${d.hp.hit_die} + CON (${d.hp.con_mod >= 0 ? '+' : ''}${d.hp.con_mod}) for HP.</p>
    <div style="display:flex;flex-direction:column;gap:0.5rem">`;
  
  ['average','roll','max','custom'].forEach(opt => {
    let label;
    if (opt === 'average') label = `Take Average (${avg})`;
    else if (opt === 'max') label = `Take Max (${max})`;
    else if (opt === 'roll') label = `Roll (${d.hp.hit_die} + CON)`;
    else label = `I Rolled My Own`;
    const checked = luChoices.hp === opt || (!luChoices.hp && opt === 'average');
    html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer">
      <input type="radio" name="lu-hp" value="${opt}" ${checked ? 'checked' : ''} onchange="onHPChoice(this.value)">
      <span>${label}</span>
    </label>`;
    if (opt === 'custom') {
      html += `<div id="lu-hp-custom-wrap" style="display:${checked ? 'flex' : 'none'};align-items:center;gap:0.5rem;margin-left:2rem;margin-top:-0.25rem;margin-bottom:0.25rem">
        <span style="font-size:0.85rem;color:var(--text-muted)">Roll result:</span>
        <input type="number" id="lu-hp-custom" value="${luChoices.hp_custom || ''}" placeholder="e.g. 7"
          style="width:80px;padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--accent);border-radius:4px;color:var(--text);font-size:0.85rem"
          oninput="luChoices.hp_custom=this.value">
        <span style="font-size:0.75rem;color:var(--text-muted)">+ CON = ${luChoices.hp_custom ? parseInt(luChoices.hp_custom) + (d.hp.con_mod || 0) : '?'}</span>
      </div>`;
    }
  });
  
  html += `</div></div>`;
  el.innerHTML = html;
  if (!luChoices.hp) luChoices.hp = 'average';
}

function onHPChoice(val) {
  luChoices.hp = val;
  const wrap = document.getElementById('lu-hp-custom-wrap');
  if (wrap) wrap.style.display = val === 'custom' ? 'flex' : 'none';
  if (val !== 'custom') luChoices.hp_custom = undefined;
}

function renderLuASI(el) {
  const d = luData.asi;
  const feats = luData.feats;
  const prevPlus2 = d.previous_plus2 || [];
  const prevPlus1 = d.previous_plus1 || [];
  const prevByAb = d.previous_by_ability || {};
  let html = `<div>
    <p style="margin-bottom:0.75rem;font-weight:600">Ability Score Improvement (Level ${d.level})</p>
    <p style="font-size:0.85rem;color:var(--text-muted);margin-bottom:1rem">Choose +2 to one ability, +1 to two abilities, or pick a feat.</p>`;
  
  // Option 1: +2 to one
  html += `<div style="margin-bottom:1rem">
    <label style="font-weight:600;display:block;margin-bottom:0.3rem">+2 to one ability:</label>
    <select id="lu-asi-plus2" onchange="luChoices.asi = {[this.value]: 2}" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">
      <option value="">— Choose —</option>`;
  for (const [a, v] of Object.entries(d.abilities)) {
    const capped = (d.would_exceed_20 || d.max_20).includes(a);
    const taken2 = prevPlus2.includes(a);
    const disabled = capped || taken2;
    const tag = capped ? '[cap]' : taken2 ? `[taken L${prevByAb[a]?.join(', L')}]` : '';
    html += `<option value="${a}"${disabled ? ' disabled' : ''}>${a} (${v} → ${v+2})${tag ? ' ' + tag : ''}</option>`;
  }
  html += `</select></div>`;
  
  // Option 2: +1 to two
  html += `<div style="margin-bottom:1rem">
    <label style="font-weight:600;display:block;margin-bottom:0.3rem">+1 to two abilities:</label>
    <div style="display:flex;gap:0.5rem">`;
  html += `<select id="lu-asi-plus1a" onchange="updateASIplus1()" style="flex:1;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">
    <option value="">— A —</option>`;
  for (const [a, v] of Object.entries(d.abilities)) {
    const capped = d.max_20.includes(a);
    const taken1 = prevPlus1.includes(a);
    const disabled = capped || taken1;
    const tag = capped ? '[max]' : taken1 ? `[taken L${prevByAb[a]?.join(', L')}]` : '';
    html += `<option value="${a}"${disabled ? ' disabled' : ''}>${a} (${v} → ${v+1})${tag ? ' ' + tag : ''}</option>`;
  }
  html += `</select>`;
  html += `<select id="lu-asi-plus1b" onchange="updateASIplus1()" style="flex:1;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">
    <option value="">— B —</option>`;
  for (const [a, v] of Object.entries(d.abilities)) {
    const capped = d.max_20.includes(a);
    const taken1 = prevPlus1.includes(a);
    const disabled = capped || taken1;
    const tag = capped ? '[max]' : taken1 ? `[taken L${prevByAb[a]?.join(', L')}]` : '';
    html += `<option value="${a}"${disabled ? ' disabled' : ''}>${a} (${v} → ${v+1})${tag ? ' ' + tag : ''}</option>`;
  }
  html += `</select></div></div>`;
  
  // Option 3: Feat
  if (feats.length) {
    html += `<div style="margin-bottom:1rem">
      <label style="font-weight:600;display:block;margin-bottom:0.3rem">Or pick a Feat (${feats.filter(f=>f.eligible).length} eligible, ${feats.length} total):</label>
      <input id="lu-feat-search" placeholder="Filter feats..." oninput="filterLuFeats()" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--accent);border-radius:6px;color:var(--text);margin-bottom:0.4rem">
      <div id="lu-feat-list" style="max-height:250px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:0.3rem">`;
    feats.forEach(f => {
      const sel = luChoices.asi === 'feat:' + f.key;
      const inel = !f.eligible;
      const shortDesc = (f.desc || '').replace(/<\/?[^>]+(>|$)/g,'').substring(0, 140);
      html += `<div class="lu-feat-card" data-key="${f.key}" data-eligible="${f.eligible}" style="cursor:${inel?'not-allowed':'pointer'};padding:0.4rem 0.5rem;margin:0.15rem 0;border:1px solid ${sel?'var(--accent)':inel?'var(--border)':'var(--border)'};border-radius:6px;font-size:0.8rem;opacity:${inel?'0.5':'1'};background:${sel?'var(--accent2)':'var(--bg)'}"
        ${inel ? '' : `onclick="selectLuFeat('${f.key}')"`}>
        <strong style="color:${inel?'var(--text-muted)':sel?'var(--accent)':'var(--text)'}">${sel?'✓ ':''}${f.name}</strong>
        ${f.prereq ? `<span style="font-size:0.7rem;color:var(--warn)"> [${f.prereq}]</span>` : ''}
        ${inel ? `<span style="font-size:0.7rem;color:var(--danger)"> — ${f.reason}</span>` : ''}
        ${shortDesc ? `<div style="font-size:0.72rem;color:var(--text-muted);margin-top:0.15rem;line-height:1.3">${shortDesc}…</div>` : ''}
      </div>`;
    });
    html += `</div>
      <div id="lu-feat-detail" style="margin-top:0.5rem;font-size:0.8rem;color:var(--text-muted)"></div>
      <div id="lu-feat-desc" style="display:none"></div>
      <div id="lu-feat-asi-choice" style="display:none;margin-top:0.5rem"></div>
    </div>`;
  }
  
  el.innerHTML = html;
}

function updateASIplus1() {
  const a = document.getElementById('lu-asi-plus1a')?.value;
  const b = document.getElementById('lu-asi-plus1b')?.value;
  if (a && b && a !== b) {
    luChoices.asi = {[a]: 1, [b]: 1};
  }
}

function selectLuFeat(key) {
  const feat = luData.feats.find(f => f.key === key);
  if (!feat || !feat.eligible) return;
  luChoices.asi = 'feat:' + key;
  // Update card highlights
  document.querySelectorAll('.lu-feat-card').forEach(c => {
    const isSel = c.getAttribute('data-key') === key;
    c.style.borderColor = isSel ? 'var(--accent)' : 'var(--border)';
    c.style.background = isSel ? 'var(--accent2)' : 'var(--bg)';
    const strong = c.querySelector('strong');
    if (strong) strong.style.color = isSel ? 'var(--accent)' : 'var(--text)';
    if (isSel && !strong.textContent.startsWith('✓ ')) strong.textContent = '✓ ' + strong.textContent;
    else if (!isSel && strong.textContent.startsWith('✓ ')) strong.textContent = strong.textContent.substring(2);
  });
  // Show full description
  const detailEl = document.getElementById('lu-feat-detail');
  const srcTag = feat.source ? ` ${_srcBadge(feat.source)}` : '';
  detailEl.innerHTML = `<div style="padding:0.5rem;background:var(--bg);border:1px solid var(--accent);border-radius:6px"><strong>${feat.name}</strong>${srcTag}<br><span style="font-size:0.78rem">${feat.desc || ''}</span></div>`;
  // Half-feat ASI
  const asiEl = document.getElementById('lu-feat-asi-choice');
  if (feat.asi && feat.asi.choices) {
    let asiHtml = `<label style="font-weight:600;display:block;margin-bottom:0.3rem;font-size:0.85rem">+${feat.asi.amount} to:</label>
      <select id="lu-feat-asi" onchange="luChoices.feat_asi_choice=this.value" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">
        <option value="">— Choose —</option>`;
    feat.asi.choices.forEach(a => { asiHtml += `<option value="${a}">${a}</option>`; });
    asiHtml += '</select>';
    asiEl.innerHTML = asiHtml;
    asiEl.style.display = '';
  } else {
    asiEl.style.display = 'none';
  }
}

function filterLuFeats() {
  const q = (document.getElementById('lu-feat-search')?.value || '').toLowerCase();
  document.querySelectorAll('.lu-feat-card').forEach(c => {
    const text = c.textContent.toLowerCase();
    c.style.display = q ? (text.includes(q) ? '' : 'none') : '';
  });
}

function renderLuExpertise(el) {
  const exp = luData.expertise;
  const picks = luChoices.expertise || [];
  const count = exp.picks_gained;
  const options = exp.options || [];
  let html = `<p style="margin:0 0 0.75rem">Choose ${count} skill${count > 1 ? 's' : ''} or tools to double your proficiency bonus.<br>
    <span style="font-size:0.75rem;color:var(--text-muted)">(Current expertise: ${exp.level_count - count}, after level-up: ${exp.level_count})</span></p>`;
  html += '<div style="display:flex;flex-wrap:wrap;gap:0.4rem">';
  options.forEach(opt => {
    const sel = picks.includes(opt);
    html += `<span class="chip${sel ? ' selected' : ''}" onclick="toggleLuExp(this, '${opt.replace(/'/g, "\\'")}')">${opt}</span>`;
  });
  html += '</div>';
  html += `<p style="font-size:0.75rem;text-align:right;margin-top:0.4rem">${picks.length} / ${count} selected</p>`;
  el.innerHTML = html;
}

function renderLuFightingStyle(el) {
  const fs = luData.fighting_style;
  let html = `<p style="margin:0 0 0.75rem">Choose your Fighting Style (gained at L${fs.level}):</p>`;
  html += '<div>';
  fs.options.forEach(opt => {
    const sel = luChoices.fighting_style === opt.key;
    html += `<div class="card" style="cursor:pointer;padding:0.6rem 0.8rem;margin-bottom:0.4rem;border:1px solid ${sel ? 'var(--accent)' : 'var(--border)'};border-radius:6px;transition:border-color 0.15s" onclick="pickLuFightingStyle('${opt.key}')">
      <strong>${opt.name}</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ── Level-Up: Metamagic Picker ──────────────────────────────────────
function renderLuMetamagic(el) {
  const meta = luData.metamagic;
  if (!luChoices.metamagic) luChoices.metamagic = {};
  let html = '<div>';
  meta.levels.forEach(lvl => {
    const count = meta.picks_per_level[String(lvl)] || 0;
    const lvlPicks = luChoices.metamagic[String(lvl)] || [];
    const picked = lvlPicks.length;
    html += `<p style="font-weight:600;margin:0.5rem 0 0.25rem">🧠 Sorcerer L${lvl}: Pick ${count} Metamagic option${count>1?'s':''} (${picked}/${count})</p>`;
    meta.options.forEach(opt => {
      const sel = lvlPicks.includes(opt.key);
      const disabled = meta.existing.includes(opt.key);
      const atLimit = !sel && picked >= count;
      html += `<div class="card" style="cursor:${(disabled||atLimit)?'not-allowed':'pointer'};padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px;opacity:${(disabled||atLimit)?'0.4':'1'}"
        ${(disabled||atLimit)?'':`onclick="toggleLuMetamagic('${lvl}','${opt.key}')"`}>
        <strong>${opt.name}</strong>${disabled?' <span style="font-size:0.7rem;color:var(--text-muted)">(already known)</span>':''}${atLimit?' <span style="font-size:0.7rem;color:var(--text-muted)">(max reached)</span>':''}<br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
      </div>`;
    });
  });
  html += '</div>';
  el.innerHTML = html;
}
function toggleLuMetamagic(level, key) {
  if (!luChoices.metamagic) luChoices.metamagic = {};
  if (!luChoices.metamagic[level]) luChoices.metamagic[level] = [];
  const arr = luChoices.metamagic[level];
  const idx = arr.indexOf(key);
  if (idx >= 0) arr.splice(idx,1);
  else arr.push(key);
  renderLevelUpStep();
}

// ── Level-Up: Invocations Picker ────────────────────────────────────
function renderLuInvocations(el) {
  const inv = luData.invocations;
  if (!luChoices.invocations) luChoices.invocations = [];
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">📜 Pick ${inv.picks_gained} Eldritch Invocation${inv.picks_gained>1?'s':''}</p>`;
  inv.options.forEach(opt => {
    const sel = luChoices.invocations.includes(opt.key);
    const has = inv.existing.includes(opt.key);
    html += `<div class="card" style="cursor:${has?'not-allowed':'pointer'};padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px;opacity:${has?'0.4':'1'}"
      ${has?'':`onclick="toggleLuInvocation('${opt.key}')"`}>
      <strong>${opt.name}</strong>${opt.prereq?` <span style="font-size:0.65rem;color:var(--text-muted)">(req: ${opt.prereq})</span>`:''}${has?' <span style="font-size:0.7rem;color:var(--text-muted)">(already known)</span>':''}<br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.invocations.length} / ${inv.picks_gained} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuInvocation(key) {
  if (!luChoices.invocations) luChoices.invocations = [];
  const idx = luChoices.invocations.indexOf(key);
  if (idx >= 0) luChoices.invocations.splice(idx,1);
  else luChoices.invocations.push(key);
  renderLevelUpStep();
}

// ── Level-Up: Pact Boon Picker ──────────────────────────────────────
function renderLuPactBoon(el) {
  const pb = luData.pact_boon;
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">📋 Choose your Pact Boon (Warlock L${pb.level})</p>`;
  pb.options.forEach(opt => {
    const sel = luChoices.pact_boon === opt.key;
    html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px" onclick="luChoices.pact_boon='${opt.key}';renderLevelUpStep()">
      <strong>${opt.name}</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ── Level-Up: Maneuvers Picker ──────────────────────────────────────
function renderLuManeuvers(el) {
  const man = luData.maneuvers;
  if (!luChoices.maneuvers) luChoices.maneuvers = [];
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">⚔️ Pick ${man.picks_gained} Battle Master Maneuver${man.picks_gained>1?'s':''}</p>`;
  man.options.forEach(opt => {
    const sel = luChoices.maneuvers.includes(opt.key);
    const has = man.existing.includes(opt.key);
    html += `<div class="card" style="cursor:${has?'not-allowed':'pointer'};padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px;opacity:${has?'0.4':'1'}"
      ${has?'':`onclick="toggleLuManeuver('${opt.key}')"`}>
      <strong>${opt.name}</strong>${has?' <span style="font-size:0.7rem;color:var(--text-muted)">(already known)</span>':''}<br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.maneuvers.length} / ${man.picks_gained} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuManeuver(key) {
  if (!luChoices.maneuvers) luChoices.maneuvers = [];
  const idx = luChoices.maneuvers.indexOf(key);
  if (idx >= 0) luChoices.maneuvers.splice(idx,1);
  else luChoices.maneuvers.push(key);
  renderLevelUpStep();
}

// ── Level-Up: Magical Secrets Picker ────────────────────────────────
function renderLuMagicalSecrets(el) {
  const ms = luData.magical_secrets;
  if (!luChoices.magical_secrets) luChoices.magical_secrets = [];
  let html = '<div>';
  ms.levels.forEach(lvl => {
    const count = ms.picks_per_level[String(lvl)] || 2;
    html += `<p style="font-weight:600;margin-bottom:0.3rem">🎭 Bard L${lvl}: Pick ${count} spell${count>1?'s':''} from any class list</p>`;
  });
  html += `<input id="ms-search" placeholder="Search spells..." style="width:100%;padding:0.3rem;margin-bottom:0.4rem;background:var(--bg);border:1px solid var(--accent);border-radius:4px;color:var(--text)" oninput="document.querySelectorAll('#ms-options .ms-opt').forEach(d=>{d.style.display=d.textContent.toLowerCase().includes(this.value.toLowerCase())?'':'none'})">`;
  html += '<div id="ms-options" style="max-height:250px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:0.3rem">';
  ms.options.slice(0,100).forEach(opt => {
    const sel = luChoices.magical_secrets.includes(opt.key);
    html += `<div class="ms-opt card" style="cursor:pointer;padding:0.3rem 0.5rem;margin:0.15rem 0;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:4px;font-size:0.8rem" onclick="toggleLuMagicalSecret('${opt.key.replace(/'/g,"\'")}')">
      <strong>${opt.name}</strong> <span style="color:var(--text-muted)">${opt.class} ${opt.level}</span>
    </div>`;
  });
  html += '</div>';
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.magical_secrets.length} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuMagicalSecret(key) {
  if (!luChoices.magical_secrets) luChoices.magical_secrets = [];
  const idx = luChoices.magical_secrets.indexOf(key);
  if (idx >= 0) luChoices.magical_secrets.splice(idx,1);
  else luChoices.magical_secrets.push(key);
  renderLevelUpStep();
}

// ── Level-Up: Totem Spirit Picker ───────────────────────────────────
function renderLuTotemSpirit(el) {
  const tot = luData.totem_spirit;
  if (!luChoices.totem_spirits) luChoices.totem_spirits = {};
  let html = '<div>';
  tot.levels.forEach(lvl => {
    const label = tot.labels[String(lvl)] || `L${lvl}`;
    html += `<p style="font-weight:600;margin:0.5rem 0 0.25rem">🐻 ${label} (L${lvl}): Pick your spirit animal</p>`;
    tot.options.forEach(opt => {
      const sel = luChoices.totem_spirits[String(lvl)] === opt.key;
      html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px" onclick="luChoices.totem_spirits['${lvl}']='${opt.key}';renderLevelUpStep()">
        <strong>${opt.name}</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
      </div>`;
    });
  });
  html += '</div>';
  el.innerHTML = html;
}

// ── Level-Up: Hunter's Prey Picker ──────────────────────────────────
function renderLuHuntersPrey(el) {
  const hp = luData.hunters_prey;
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">🏹 Choose your Hunter's Prey (Ranger Hunter L${hp.level})</p>`;
  hp.options.forEach(opt => {
    const sel = luChoices.hunters_prey === opt.key;
    html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px" onclick="luChoices.hunters_prey='${opt.key}';renderLevelUpStep()">
      <strong>${opt.name}</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ── Level-Up: Favored Enemy ────────────────────────────────────────
function renderLuFavoredEnemy(el) {
  const fe = luData.favored_enemy;
  if (!luChoices.favored_enemies) luChoices.favored_enemies = [];
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">🎯 Pick ${fe.picks_gained} Favored Enemy (Ranger — ${fe.levels.join(', ')})</p>`;
  fe.options.forEach(opt => {
    const sel = luChoices.favored_enemies.includes(opt.key);
    const already = fe.existing && fe.existing.includes(opt.key);
    html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':already?'var(--text-muted)':'var(--border)'};border-radius:6px;opacity:${already?'0.5':'1'}" onclick="toggleLuFavoredEnemy('${opt.key.replace(/'/g, "\\\\'")}')">
      <strong>${opt.name}</strong>${already?' <span style="font-size:0.7rem;color:var(--text-muted)">(already chosen)</span>':''}<br><span style="font-size:0.75rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.favored_enemies.length} / ${fe.picks_gained} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuFavoredEnemy(key) {
  if (!luChoices.favored_enemies) luChoices.favored_enemies = [];
  const idx = luChoices.favored_enemies.indexOf(key);
  if (idx >= 0) { luChoices.favored_enemies.splice(idx, 1); }
  else { luChoices.favored_enemies.push(key); }
  renderLevelUpStep();
}

// ── Level-Up: Favored Terrain ──────────────────────────────────────
function renderLuFavoredTerrain(el) {
  const ft = luData.favored_terrain;
  if (!luChoices.favored_terrains) luChoices.favored_terrains = [];
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">🌲 Pick ${ft.picks_gained} Favored Terrain (Ranger — ${ft.levels.join(', ')})</p>`;
  ft.options.forEach(opt => {
    const sel = luChoices.favored_terrains.includes(opt.key);
    const already = ft.existing && ft.existing.includes(opt.key);
    html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':already?'var(--text-muted)':'var(--border)'};border-radius:6px;opacity:${already?'0.5':'1'}" onclick="toggleLuFavoredTerrain('${opt.key.replace(/'/g, "\\\\'")}')">
      <strong>${opt.name}</strong>${already?' <span style="font-size:0.7rem;color:var(--text-muted)">(already chosen)</span>':''}<br><span style="font-size:0.75rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.favored_terrains.length} / ${ft.picks_gained} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuFavoredTerrain(key) {
  if (!luChoices.favored_terrains) luChoices.favored_terrains = [];
  const idx = luChoices.favored_terrains.indexOf(key);
  if (idx >= 0) { luChoices.favored_terrains.splice(idx, 1); }
  else { luChoices.favored_terrains.push(key); }
  renderLevelUpStep();
}

// ── Level-Up: Infusions Picker ──────────────────────────────────────
function renderLuInfusions(el) {
  const inf = luData.infusions;
  if (!luChoices.infusions) luChoices.infusions = [];
  let html = `<div><p style="font-weight:600;margin-bottom:0.5rem">🔧 Pick ${inf.total_known} Infusions (Artificer L${inf.level})</p>`;
  inf.options.forEach(opt => {
    const sel = luChoices.infusions.includes(opt.key);
    html += `<div class="card" style="cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid ${sel?'var(--accent)':'var(--border)'};border-radius:6px" onclick="toggleLuInfusion('${opt.key}')">
      <strong>${opt.name}</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">${opt.desc}</span>
    </div>`;
  });
  html += `<p style="font-size:0.75rem;margin-top:0.4rem">${luChoices.infusions.length} / ${inf.total_known} selected</p></div>`;
  el.innerHTML = html;
}
function toggleLuInfusion(key) {
  if (!luChoices.infusions) luChoices.infusions = [];
  const idx = luChoices.infusions.indexOf(key);
  if (idx >= 0) luChoices.infusions.splice(idx,1);
  else luChoices.infusions.push(key);
  renderLevelUpStep();
}

function renderLuSubclass(el) {
  const sc = luData.subclass;
  let html = `<div>
    <p style="font-weight:600;margin-bottom:0.75rem">Choose your ${sc.label}</p>
    <div style="display:flex;flex-direction:column;gap:0.5rem">`;
  sc.options.forEach(opt => {
    const desc = (sc.descriptions && sc.descriptions[opt]) || '';
    html += `<label style="display:flex;align-items:flex-start;gap:0.5rem;padding:0.6rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer">
      <input type="radio" name="lu-subclass" value="${opt}" onchange="luChoices.subclass=this.value" style="margin-top:0.15rem;flex-shrink:0">
      <div>
        <strong>${opt}</strong>
        ${desc ? `<p style="margin:0.25rem 0 0 0;font-size:0.8rem;color:var(--text-muted);line-height:1.4">${desc}</p>` : ''}
      </div>
    </label>`;
  });
  html += `</div></div>`;
  el.innerHTML = html;
}

function renderLuSubclassBonus(el) {
  const chosen = luChoices.subclass;
  const bonus = chosen && luData.subclass_bonus_map ? luData.subclass_bonus_map[chosen] : null;
  
  if (!bonus) {
    el.innerHTML = `<div><p style="color:var(--text-muted)">No bonus proficiencies for this subclass.</p></div>`;
    return;
  }
  
  if (!luChoices.subclass_bonus) luChoices.subclass_bonus = [];
  const selected = luChoices.subclass_bonus;
  
  let html = `<div>
    <p style="font-weight:600;margin-bottom:0.5rem">${bonus.label}</p>
    <p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.75rem">Pick ${bonus.count} ${bonus.type === 'skills' ? 'skill proficiencies' : 'languages'}.</p>
    <div style="display:flex;flex-wrap:wrap;gap:0.35rem;margin-bottom:0.75rem">`;
  
  bonus.options.forEach(opt => {
    const isSel = selected.includes(opt);
    const atMax = selected.length >= bonus.count && !isSel;
    html += `<label style="display:inline-block;padding:0.3rem 0.6rem;background:${isSel ? 'var(--accent2)' : 'var(--bg)'};border:1px solid var(--border);border-radius:4px;font-size:0.8rem;cursor:${atMax ? 'not-allowed' : 'pointer'};opacity:${atMax ? '0.5' : '1'}"
      onclick="event.preventDefault();toggleLuBonus(this,${bonus.count})" data-opt="${opt}">
      ${isSel ? '✅ ' : ''}${opt}
    </label>`;
  });
  
  html += `</div>
    <p style="font-size:0.75rem;color:var(--text-muted)">Selected: ${selected.length}/${bonus.count}</p>
  </div>`;
  el.innerHTML = html;
}

function toggleLuBonus(el, max) {
  const opt = el.getAttribute('data-opt');
  if (!opt) return;
  if (!luChoices.subclass_bonus) luChoices.subclass_bonus = [];
  const idx = luChoices.subclass_bonus.indexOf(opt);
  if (idx >= 0) {
    luChoices.subclass_bonus.splice(idx, 1);
  } else if (luChoices.subclass_bonus.length < max) {
    luChoices.subclass_bonus.push(opt);
  }
  // Re-render current step
  renderLevelUpStep();
}

function toggleLuExp(el, opt) {
  if (!luChoices.expertise) luChoices.expertise = [];
  const count = luData.expertise.picks_gained;
  const idx = luChoices.expertise.indexOf(opt);
  if (idx >= 0) {
    luChoices.expertise.splice(idx, 1);
  } else if (luChoices.expertise.length < count) {
    luChoices.expertise.push(opt);
  }
  renderLevelUpStep();
}

function pickLuFightingStyle(key) {
  luChoices.fighting_style = (luChoices.fighting_style === key) ? '' : key;
  renderLevelUpStep();
}

function renderLuSpells(el) {
  const s = luData.spells;
  const needSpells = s.spells_known_change || 0;
  const needCantrips = s.cantrips_change || 0;
  if (!luChoices.spells) luChoices.spells = [];
  const selected = luChoices.spells;
  
  let html = `<div style="font-size:0.9rem;line-height:1.6">`;
  html += `<p><strong>Spellcasting Ability:</strong> ${s.spellcasting_ability}</p>`;
  html += `<p>New spell slots will be automatically applied.</p>`;
  
  if (needSpells > 0 || needCantrips > 0) {
    const totalNeeded = needSpells + needCantrips;
    html += `<p style="margin-top:0.75rem"><strong>Select ${totalNeeded} new spell${totalNeeded>1?'s':''}:</strong> `;
    if (needSpells > 0) html += `${needSpells} L1+ spell${needSpells>1?'s':''}`;
    if (needSpells > 0 && needCantrips > 0) html += ` + `;
    if (needCantrips > 0) html += `${needCantrips} cantrip${needCantrips>1?'s':''}`;
    html += `</p>`;
    
    // Search input
    html += `<input id="lu-spell-search" type="text" placeholder="Search spells..." 
      style="width:100%;padding:0.4rem 0.6rem;background:var(--bg);border:1px solid var(--accent);border-radius:6px;color:var(--text);font-size:0.85rem;outline:none;margin-bottom:0.4rem"
      oninput="filterLuSpells()">`;
    html += `<label style="display:flex;align-items:center;gap:0.3rem;font-size:0.68rem;color:${_coreOnly?'var(--accent)':'var(--text-muted)'};cursor:pointer;user-select:none;margin-bottom:0.4rem" onclick="event.stopPropagation();_coreOnly=!_coreOnly;document.getElementById('lu-core-box').textContent=_coreOnly?'☑':'☐';this.style.color=_coreOnly?'var(--accent)':'var(--text-muted)';filterLuSpells();return false">
      <span id="lu-core-box" style="display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-radius:3px;text-align:center;line-height:12px;font-size:10px;background:var(--bg)">${_coreOnly ? '☑' : '☐'}</span>
      Core content only
    </label>`;
    html += `<span data-src-filter="sheet-spells-lu" data-src-onchange="filterLuSpells" style="display:block;margin-bottom:0.4rem"></span>`;
    
    // Selected chips
    html += `<div id="lu-selected-spells" style="display:flex;flex-wrap:wrap;gap:0.25rem;margin-bottom:0.5rem;min-height:1.5rem">`;
    html += `</div>`;
    
    // Spell results
    html += `<div id="lu-spell-results" style="border:1px solid var(--border);border-radius:6px">
      <div style="padding:0.5rem;color:var(--text-muted);text-align:center">Loading spells...</div>
    </div>`;
    
    // Counter
    html += `<p id="lu-spell-count" style="font-size:0.8rem;color:var(--text-muted);margin-top:0.4rem;text-align:right">
      ${selected.length}/${totalNeeded} selected
    </p>`;
  } else {
    if (['Cleric','Druid','Paladin','Wizard'].includes(s.class_name)) {
      if (s.new_slot_levels && s.new_slot_levels.length) {
        const labels = s.new_slot_levels.map(l => l === 1 ? '1st' : (l === 2 ? '2nd' : (l === 3 ? '3rd' : l + 'th'))).join(' and ');
        html += `<p style="font-size:0.85rem;color:var(--success);margin-top:1rem">✨ You now have <strong>${labels} level</strong> spell slots! New spells added to your spellbook.</p>`;
      } else {
        html += `<p style="font-size:0.85rem;color:var(--success);margin-top:1rem">Your spellbook will be automatically updated with new spells from your class list. Prepare your daily spells on the Spells tab.</p>`;
      }
    } else {
      html += `<p style="font-size:0.85rem;color:var(--text-muted);margin-top:1rem">No new spells this level.</p>`;
    }
  }
  
  html += `</div>`;
  el.innerHTML = html;
  
  // Load spells
  if (needSpells > 0 || needCantrips > 0) {
    loadLuSpells(s.class_name, luData.target_level, needSpells, needCantrips);
  }
}

let _luSpellList = [];

async function loadLuSpells(className, targetLevel, needSpells, needCantrips) {
  try {
    const r = await fetch(`/api/character/${CHAR_ID}/available-spells?level=${targetLevel}`);
    _luSpellList = await r.json();
    // Sort: cantrips first, then by level, then name
    _luSpellList.sort((a,b) => a.level - b.level || a.name.localeCompare(b.name));
    filterLuSpells();
    renderLuSelected();
  } catch(e) {
    document.getElementById('lu-spell-results').innerHTML = 
      '<div style="padding:0.5rem;color:var(--danger);text-align:center">Failed to load spells</div>';
  }
}

function filterLuSpells() {
  const q = (document.getElementById('lu-spell-search')?.value || '').toLowerCase().trim();
  const div = document.getElementById('lu-spell-results');
  if (!div) return;
  
  let spells = _luSpellList;
  if (_coreOnly) {
    spells = spells.filter(s => isCoreSource(s.book));
  }
  if (window.SourceFilter) {
    const src = SourceFilter.slugs('sheet-spells-lu');
    if (src.length) spells = spells.filter(s => SourceFilter.matches(s.book || s.source || '', src));
  }
  if (q) {
    spells = spells.filter(s =>
      s.name.toLowerCase().includes(q) ||
      (s.school || '').toLowerCase().includes(q) ||
      s.source.toLowerCase().includes(q)
    );
  }
  
  if (!spells.length) {
    div.innerHTML = '<div style="padding:0.5rem;color:var(--text-muted);text-align:center">No spells match</div>';
    return;
  }
  
  const selected = luChoices.spells || [];
  const totalNeeded = (luData.spells.spells_known_change || 0) + (luData.spells.cantrips_change || 0);
  
  const shown = spells;
  div.innerHTML = shown.map((s, i) => {
    const selIdx = selected.findIndex(x => x.name === s.name);
    const isSelected = selIdx >= 0;
    const uid = 'lus' + i;
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
    let ritualBadge = s.ritual ? '<span style="font-size:0.68rem;color:var(--accent);opacity:0.7;flex-shrink:0" title="Ritual">\u26a1</span>' : '';
    let concBadge = s.concentration ? '<span style="font-size:0.68rem;color:var(--warning);opacity:0.8;flex-shrink:0" title="Concentration">\u23f3</span>' : '';
    const bg = isSelected ? 'background:rgba(99,102,241,0.15);' : '';
    // Description body
    let descText = (s.description || '').replace(/'/g, "\\'").replace(/`/g, '\\`').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    let timeText = s.casting_time || '1 action';
    let rangeText = s.range || 'Self';
    let durText = s.duration || 'Instantaneous';
    return `<div style="border-bottom:1px solid var(--border);${bg}">
      <div onclick="const b=document.getElementById('${uid}');b.style.display=b.style.display==='none'?'block':'none'" 
        style="padding:0.35rem 0.5rem;cursor:pointer;font-size:0.82rem;display:flex;align-items:center;gap:0.3rem;flex-wrap:wrap">
        <span style="font-size:0.8rem;width:1.2em;text-align:center;flex-shrink:0" onclick="event.stopPropagation();toggleLuSpell('${s.name.replace(/'/g,"\\'")}', ${s.level})">${isSelected ? '\u2705' : '\u2610'}</span>
        <strong style="flex:1 1 auto;min-width:8ch;overflow-wrap:break-word;word-break:break-word">${s.name}</strong>
        ${lvlBadge}
        ${diceBadge}
        ${ritualBadge}
        ${concBadge}
        <span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">${s.school||''}</span>
        ${s.book ? _srcBadge(s.book) : ''}
      </div>
      <div id="${uid}" style="display:none;padding:0.3rem 0.5rem 0.5rem 2.2rem;font-size:0.78rem;color:var(--text-muted);line-height:1.5;overflow-wrap:break-word;word-break:break-word">
        <p style="margin:0 0 0.3rem 0">${descText || '<em>No description available.</em>'}</p>
        <p style="margin:0;font-size:0.7rem;display:flex;flex-wrap:wrap;gap:0.5rem">
          <span>\u23f1 ${timeText}</span>
          <span>\ud83c\udfaf ${rangeText}</span>
          <span>\u23f3 ${durText}</span>
          ${s.ritual ? '<span style="color:var(--accent)">\u26a1 Ritual</span>' : ''}
          ${s.concentration ? '<span style="color:var(--warning)">\ud83e\udde0 Concentration</span>' : ''}
        </p>
      </div>
    </div>`;
  }).join('');
  if (shown.length < spells.length) {
    div.innerHTML += `<div style="padding:0.3rem;font-size:0.7rem;color:var(--text-muted);text-align:center">Showing ${shown.length} of ${spells.length} \u2014 type to narrow</div>`;
  }
}

function toggleLuSpell(name, level) {
  const selected = luChoices.spells || [];
  const idx = selected.findIndex(x => x.name === name);
  const needSpells = luData.spells.spells_known_change || 0;
  const needCantrips = luData.spells.cantrips_change || 0;
  const totalNeeded = needSpells + needCantrips;
  
  if (idx >= 0) {
    // Deselect
    selected.splice(idx, 1);
  } else {
    // Check limits
    if (selected.length >= totalNeeded) {
      // Already at max — swap if they click a different one
      return; // Just block for now
    }
    // Check cantrip vs leveled spell counts
    const selCantrips = selected.filter(x => x.level === 0).length;
    const selLeveled = selected.filter(x => x.level > 0).length;
    if (level === 0 && selCantrips >= needCantrips && needCantrips > 0) {
      return; // Already picked enough cantrips
    }
    if (level > 0 && selLeveled >= needSpells && needSpells > 0) {
      return; // Already picked enough leveled spells
    }
    selected.push({name, level});
  }
  
  filterLuSpells();
  renderLuSelected();
  updateLuSpellCount();
}

function renderLuSelected() {
  const div = document.getElementById('lu-selected-spells');
  if (!div) return;
  const selected = luChoices.spells || [];
  div.innerHTML = selected.map(s => {
    const lvlBadge = s.level === 0 
      ? '<span style="background:var(--accent2);color:var(--accent);font-size:0.72rem;padding:0.1rem 0.35rem;border-radius:3px">Cantrip</span>'
      : `<span style="background:var(--accent);color:#fff;font-size:0.72rem;padding:0.1rem 0.35rem;border-radius:3px">Lv${s.level}</span>`;
    // Dice badge on selected chip
    let diceBadge = '';
    if (s.dice) {
      if (s.dice_healing) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(34,197,94,0.2);color:var(--success);font-family:monospace;padding:0 0.2rem;border-radius:2px">${s.dice}</span>`;
      } else if (s.dice_ac) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(59,130,246,0.2);color:#3b82f6;font-family:monospace;padding:0 0.2rem;border-radius:2px">${s.dice}</span>`;
      } else if (s.dice_buff) {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:rgba(139,92,246,0.2);color:#8b5cf6;font-family:monospace;padding:0 0.2rem;border-radius:2px">${s.dice}</span>`;
      } else {
        diceBadge = `<span style="font-size:0.65rem;font-weight:600;background:var(--accent2);color:var(--accent);font-family:monospace;padding:0 0.2rem;border-radius:2px">${s.dice}</span>`;
      }
    }
    return `<div style="display:flex;align-items:center;gap:0.25rem;padding:0.2rem 0.4rem;background:rgba(99,102,241,0.12);border:1px solid var(--accent2);border-radius:4px;font-size:0.78rem">
      <strong>${s.name}</strong> ${lvlBadge} ${diceBadge}
      <button onclick="event.stopPropagation();toggleLuSpell('${s.name.replace(/'/g,"\\'")}',${s.level})" 
        style="background:none;border:none;color:var(--danger);cursor:pointer;font-size:0.7rem;padding:0;line-height:1">\u2715</button>
    </div>`;
  }).join('');
}

function updateLuSpellCount() {
  const el = document.getElementById('lu-spell-count');
  if (!el) return;
  const selected = luChoices.spells || [];
  const totalNeeded = (luData.spells.spells_known_change || 0) + (luData.spells.cantrips_change || 0);
  el.textContent = `${selected.length}/${totalNeeded} selected`;
  el.style.color = selected.length === totalNeeded ? 'var(--success)' : 'var(--text-muted)';
}

function renderLuConfirm(el) {
  let html = `<div style="font-size:0.9rem;line-height:1.6">
    <h3 style="margin-bottom:0.75rem">Confirm Changes</h3>
    <p><strong>Level:</strong> ${luData.current_level} → <strong style="color:var(--accent)">${luData.target_level}</strong></p>
    ${luClassToLevel ? '<p><strong>Leveling:</strong> ' + luClassToLevel + '</p>' : ''}
    <p><strong>HP:</strong> ${luChoices.hp === 'custom' ? 'Rolled ' + (luChoices.hp_custom || '?') : (luChoices.hp === 'roll' ? 'Roll 1d12' : (luChoices.hp === 'max' ? 'Max (+' + luData.hp.max_roll + ')' : 'Average (+' + luData.hp.average + ')'))}</p>`;
  
  if (luData.all_features && luData.all_features.length) {
    html += `<p><strong>Features:</strong> ${luData.all_features.join(', ')}</p>`;
  }
  if (luData.proficiency_bonus.changed) {
    html += `<p><strong>Proficiency:</strong> +${luData.proficiency_bonus.old} → +${luData.proficiency_bonus.new}</p>`;
  }
  if (luChoices.subclass) {
    html += `<p><strong>Subclass:</strong> ${luChoices.subclass}</p>`;
  }
  if (luChoices.subclass_bonus && luChoices.subclass_bonus.length) {
    html += `<p><strong>Bonus Proficiencies:</strong> ${luChoices.subclass_bonus.join(', ')}</p>`;
  }
  if (luChoices.asi) {
    if (typeof luChoices.asi === 'string' && luChoices.asi.startsWith('feat:')) {
      const featKey = luChoices.asi.substring(5);
      const feat = luData.feats.find(f => f.key === featKey);
      html += `<p><strong>Feat:</strong> ${feat ? feat.name : featKey}</p>`;
    } else if (typeof luChoices.asi === 'object') {
      for (const [a, inc] of Object.entries(luChoices.asi)) {
        html += `<p><strong>ASI:</strong> ${a} +${inc}</p>`;
      }
    }
  }
  if (luData.spells) {
    html += `<p><strong>Spell Slots:</strong> Updated automatically</p>`;
  }
  if (luChoices.spells && luChoices.spells.length) {
    html += `<p><strong>New Spells:</strong> ${luChoices.spells.map(s => s.name).join(', ')}</p>`;
  }
  
  html += `<p style="color:var(--warn);font-size:0.8rem;margin-top:1rem">⚠ This cannot be undone. HP will be fully restored.</p>`;
  html += `</div>`;
  el.innerHTML = html;
}

async function applyLevelUp() {
  const btn = document.getElementById('lu-confirm');
  btn.disabled = true;
  btn.textContent = 'Applying...';
  
  const nextLevel = (luData?.current_level || CHAR_LEVEL) + 1;
  const ultimateTarget = luData?._ultimate_target;
  
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/apply-level-up`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...luChoices, expertise_skills: (luChoices.expertise || []), fighting_style: (luChoices.fighting_style || ''), metamagic: (luChoices.metamagic || {}), invocations: (luChoices.invocations || []), pact_boon: (luChoices.pact_boon || ''), maneuvers: (luChoices.maneuvers || []), magical_secrets: (luChoices.magical_secrets || []), totem_spirits: (luChoices.totem_spirits || {}), hunters_prey: (luChoices.hunters_prey || ''), favored_enemies: (luChoices.favored_enemies || []), favored_terrains: (luChoices.favored_terrains || []), infusions: (luChoices.infusions || []), class_to_level: luClassToLevel, target_level: nextLevel}),
    });
    const result = await resp.json();
    if (result.ok) {
      // Chain to next level if we haven't reached the ultimate target
      if (ultimateTarget && nextLevel < ultimateTarget && nextLevel < 20) {
        location.href = `/character/${CHAR_ID}?level-up=${ultimateTarget}`;
      } else {
        location.reload();
      }
    } else {
      alert('Level up failed');
      btn.disabled = false;
      btn.textContent = '✅ Confirm Level Up';
    }
  } catch(e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = '✅ Confirm Level Up';
  }
}

