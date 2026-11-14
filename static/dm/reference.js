/* dm/reference.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
function renderMonsterCards() {
  const grid = document.getElementById('monsterGrid');
  if (!grid || grid.dataset.rendered || !Array.isArray(window.DM_MONSTERS)) return false;
  const html = window.DM_MONSTERS.map(function(m) {
    const name = dmEsc(m.n), type = dmEsc(m.t), src = dmEsc(m.src);
    const badge = src
      ? '<div style="font-size:0.72rem;color:var(--text-muted);opacity:0.6;margin-top:0.15rem;cursor:pointer"'
        + ' class="src-badge" data-src="' + src + '">📚 ' + src + '</div>'
      : '';
    return '<div class="monster-card" data-name="' + name.toLowerCase() + '" data-type="' + type + '"'
      + ' data-cr="' + dmEsc(m.cr) + '" data-source="' + src + '" data-monster-action="showMonster" data-monster-index="' + dmEsc(m.i) + '">'
      + '<div style="display:flex;align-items:center;gap:0.4rem">'
      + charPortraitTile(null, m.n, {size: 36, src: '/api/ref-image/creature/' + encodeURIComponent(m.n) + '?size=72'})
      + '<div class="m-name">' + name + '</div></div>'
      + '<div class="m-type">' + dmEsc(m.s) + ' ' + dmEsc(m.t) + (m.a ? ' · ' + dmEsc(m.a) : '') + '</div>'
      + badge
      + '<div class="m-stats">'
      + '<span class="m-stat">CR ' + dmEsc(m.cr) + '</span>'
      + '<span class="m-stat">AC ' + dmEsc(m.ac) + '</span>'
      + '<span class="m-stat">HP ' + dmEsc(m.hp) + '</span>'
      + '<span class="m-stat">XP ' + dmEsc(m.xp) + '</span>'
      + '</div></div>';
  });
  grid.innerHTML = html.join('');
  grid.dataset.rendered = '1';
  const count = document.getElementById('monsterCount');
  if (count) count.textContent = html.length + ' monsters';
  return true;
}

// ── Manual library tree ──────────────────────────────────────────────────────
// ~87 rows whose long names and repeated inline styles cost 70 KB of HTML per load. The
// tree now rides in /static/dm-library.js; markup, classes and sizes match the template's,
// so toggleCollapse() keeps working.
function renderManualGroups() {
  const host = document.getElementById('manualGroups');
  if (!host || host.dataset.rendered || !Array.isArray(window.DM_MANUALS)) return false;
  const esc = dmEsc;
  const html = window.DM_MANUALS.map(function(group) {
    const rows = (group.items || []).map(function(m) {
      const name = esc(m.n);
      const href = m.s ? '/api/reference/open/' + encodeURIComponent(m.s)
                       : '/api/reference/manual-file/' + esc(m.p || '');
      const size = m.mb ? '<span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">'
        + Number(m.mb).toFixed(1) + ' MB</span>' : '';
      return '<div style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;">'
        + '<span style="font-size:0.7rem;color:var(--text-muted);flex-shrink:0">📄</span>'
        + '<span style="font-size:0.82rem;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + name + '">' + name + '</span>'
        + '<a href="' + href + '" target="_blank" class="btn btn-outline btn-xs" style="font-size:0.7rem;padding:0.15rem 0.6rem;flex-shrink:0">Open</a>'
        + size
        + '</div>';
    }).join('');
    return '<div class="card" style="margin-bottom:0.5rem">'
      + '<div class="collapse-header" onclick="toggleCollapse(this)" style="margin:0;padding:0.5rem 0.75rem;border:none;display:flex;align-items:center;gap:0.5rem;justify-content:space-between">'
      + '<span style="display:flex;align-items:center;gap:0.4rem">'
      + '<span class="collapse-arrow">▶</span>'
      + '<span style="font-size:0.9rem;font-weight:600">' + esc(group.g) + '</span>'
      + '</span>'
      + '<span style="font-size:0.75rem;color:var(--text-muted)">' + (group.items || []).length + '</span>'
      + '</div>'
      + '<div class="collapse-body" style="padding:0 0.75rem 0.75rem 0.75rem">'
      + '<div style="display:flex;flex-direction:column;gap:0.25rem">' + rows + '</div>'
      + '</div></div>';
  });
  host.innerHTML = html.join('');
  host.dataset.rendered = '1';
  return true;
}

// ── Reference spell cards ────────────────────────────────────────────────────
// ~700 cards (~580 KB of HTML) used to be server-rendered on every /dm-tools load.
// The rows now ride in /static/dm-library.js; the markup and data-* attributes match
// the template's, so filterSpells() is unchanged (it scopes to '#spellGrid .monster-card').
function renderSpellCards() {
  const grid = document.getElementById('spellGrid');
  if (!grid || grid.dataset.rendered || !Array.isArray(window.DM_SPELLS)) return false;
  const esc = dmEsc;
  const html = window.DM_SPELLS.map(function(s) {
    const name = esc(s.n), src = esc(s.src);
    const lvl = String(s.lv) === '0' ? 'Cantrip' : 'Level ' + esc(s.lv);
    const badge = src
      ? '<div style="font-size:0.72rem;color:var(--text-muted);opacity:0.6;margin-top:0.15rem;cursor:pointer"'
        + ' class="src-badge" data-src="' + src + '">📚 ' + src + '</div>'
      : '';
    return '<div class="monster-card" data-name="' + name.toLowerCase() + '" data-level="' + esc(s.lv) + '"'
      + ' data-school="' + esc(s.sc) + '" data-source="' + src + '" data-classes="' + esc(s.cls) + '"'
      + ' onclick="showSpell(\'' + esc(s.i) + '\')">'
      + '<div class="m-name">' + name + '</div>'
      + '<div class="m-type">' + lvl + ' · ' + esc(s.scd || 'Unknown')
      + (s.rit ? '<span style="color:var(--accent)"> · Ritual</span>' : '')
      + (s.con ? '<span style="color:var(--warn)"> · Concentration</span>' : '')
      + '</div>'
      + badge
      + '<div class="m-stats">'
      + '<span class="m-stat">⏱ ' + esc(s.ct) + '</span>'
      + '<span class="m-stat">📏 ' + esc(s.rng) + '</span>'
      + '<span class="m-stat">⏳ ' + esc(s.dur) + '</span>'
      + '</div></div>';
  });
  grid.innerHTML = html.join('');
  grid.dataset.rendered = '1';
  return true;
}

// ── Reference trap cards ─────────────────────────────────────────────────────
// The reference trap library is ~380 rows / ~950 KB of server-rendered HTML on
// every /dm-tools load, so it now ships in /static/dm-library.js next to the
// monsters and manual NPCs. Same .trap-card markup and data-* attributes the
// template emitted, so filterTraps() is unchanged. Custom (per-DM) traps stay
// server-rendered in #trapGrid — they are user data and must never enter a
// cached, shared asset.
function dmCap(s) {
  const v = String(s === null || s === undefined ? '' : s);
  return v.charAt(0).toUpperCase() + v.slice(1).toLowerCase();
}

function renderTrapCards() {
  const grid = document.getElementById('trapGrid');
  if (!grid || grid.dataset.rendered || !Array.isArray(window.DM_TRAPS)) return false;
  const esc = dmEsc;
  const custom = grid.innerHTML;              // the DM's own traps, already rendered
  const html = window.DM_TRAPS.map(function(t) {
    const name = esc(t.n), src = esc(t.src), type = esc(t.t), danger = esc(t.d);
    const badge = src
      ? '<div style="font-size:0.72rem;color:var(--text-muted);opacity:0.6;margin-top:0.15rem;cursor:pointer"'
        + ' class="src-badge" data-src="' + src + '">📚 ' + src + '</div>'
      : '';
    const stats = '<span class="trap-type-badge">' + (t.t === 'magical' ? '🔮' : '⚙️') + ' ' + esc(dmCap(t.t)) + '</span>'
      + (t.sdc ? '<span class="trap-stat">DC ' + esc(t.sdc) + ' ' + esc(t.sab) + '</span>' : '')
      + (t.dmg ? '<span class="trap-stat">' + esc(dmCap(t.dt)) + '</span>' : '');
    const detail = '<div class="trap-detail" style="display:none;margin-top:0.5rem;font-size:0.82rem;line-height:1.5;color:var(--text-muted)">'
      + '<div class="trap-section"><strong style="color:var(--text)">Trigger:</strong> ' + esc(t.trig) + '</div>'
      + '<div class="trap-section"><strong style="color:var(--text)">Detection:</strong> DC ' + esc(t.detdc) + ' ' + esc(t.detsk) + ' — ' + esc(t.detd) + '</div>'
      + '<div class="trap-section"><strong style="color:var(--text)">Disarm:</strong> ' + esc(t.dism)
      + (t.disdc ? ' (DC ' + esc(t.disdc) + ')' : '') + ' — ' + esc(t.disd) + '</div>'
      + '<div class="trap-section"><strong style="color:var(--text)">Effect:</strong> ' + esc(t.eff) + '</div>'
      + (t.area ? '<div class="trap-section"><strong style="color:var(--text)">Area:</strong> ' + esc(t.area) + '</div>' : '')
      + '</div>';
    return '<div class="trap-card" data-name="' + name.toLowerCase() + '" data-type="' + type + '"'
      + ' data-danger="' + danger + '" data-source="' + src + '">'
      + '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem">'
      + '<div class="trap-name">' + name + '</div>'
      + '<span class="trap-danger-badge trap-danger-' + danger + '">' + esc(dmCap(t.d)) + '</span>'
      + '</div>'
      + '<div class="trap-meta">' + stats + '</div>'
      + badge + detail
      + '<div style="display:flex;gap:0.3rem;align-items:center;margin-top:0.4rem">'
      + '<button class="trap-expand-btn" onclick="event.stopPropagation();toggleTrapDetail(this)" style="font-size:0.7rem;color:var(--accent);background:none;border:none;cursor:pointer;padding:0">▶ Expand</button>'
      + '</div></div>';
  });
  // Reference traps first, the DM's own traps after — the order the server used.
  grid.innerHTML = html.join('') + custom;
  grid.dataset.rendered = '1';
  return true;
}

// ── Manual (read-only) NPC rows ──────────────────────────────────────────────
// ~400 extracted NPCs incl. long descriptions used to be server-rendered
// (~780 KB of HTML per load); they now come from /static/dm-library.js. Same
// .npc-row markup + data-* attributes so filterNpcs() is unchanged.
function renderManualNpcRows() {
  const host = document.getElementById('manualNpcList');
  if (!host || host.dataset.rendered || !Array.isArray(window.DM_MANUAL_NPCS)) return false;
  const esc = dmEsc;
  host.innerHTML = window.DM_MANUAL_NPCS.map(function(n) {
    const name = esc(n.n);
    const nameJs = String(n.n).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const cls = [n.r, n.c ? 'L' + n.lvl + ' ' + n.c : '', n.sc ? '(' + n.sc + ')' : '']
      .filter(Boolean).join(' ');
    const hpLine = n.narr
      ? '<span style="font-size:0.8rem;color:var(--text-muted)">Narrative NPC — no combat stats</span>'
      : '<span style="font-size:0.8rem">HP ' + esc(n.hp) + '/' + esc(n.hpmax) + ' AC ' + esc(n.ac) + '</span>';
    const srcBadge = n.src
      ? '<span style="font-size:0.7rem;color:var(--text-muted);cursor:pointer" class="src-badge"'
        + ' data-src="' + esc(n.src) + '">📚 ' + esc(n.src) + '</span>'
      : '';
    const notes = n.notes
      ? '<details class="npc-desc-toggle" style="margin-top:0.3rem"><summary style="cursor:pointer;font-size:0.75rem;'
        + 'color:var(--accent);user-select:none">📝 Description</summary><div style="font-size:0.8rem;'
        + 'color:var(--text-muted);line-height:1.5;margin-top:0.25rem;padding-left:0.25rem;border-left:2px solid '
        + 'var(--accent);max-width:60ch">' + esc(n.notes) + '</div></details>'
      : '';
    return '<div class="npc-row" data-name="' + name.toLowerCase() + '" data-source="' + esc(n.src) + '">'
      + '<div class="npc-info">'
      + charPortraitTile(null, n.n, {size: 40, src: '/api/ref-image/npc/' + encodeURIComponent(n.n) + '?size=80'})
      + '<span class="npc-badge" style="background:var(--accent2);color:var(--text)">📖 Manual</span>'
      + '<strong>' + name + '</strong>'
      + '<span style="color:var(--text-muted);font-size:0.85rem">' + esc(cls) + '</span>'
      + (n.role ? '<span class="badge badge-muted">' + esc(n.role) + '</span>' : '')
      + (n.al ? '<span style="font-size:0.75rem;color:var(--text-muted)">' + esc(n.al) + '</span>' : '')
      + hpLine + srcBadge + notes
      + '</div>'
      + '<div class="npc-actions">'
      + '<button class="btn btn-accent btn-sm" onclick="buildNpcToCharacter(' + n.id + ', \'' + nameJs + '\')"'
      + ' title="Build full character sheet from this NPC">🛠️ Build</button>'
      + '<span style="font-size:0.7rem;color:var(--text-muted)">read-only</span>'
      + '</div></div>';
  }).join('');
  host.dataset.rendered = '1';
  const empty = document.getElementById('npcEmptyState');
  if (empty && window.DM_MANUAL_NPCS.length) empty.style.display = 'none';
  return true;
}

function filterMonsters() {
  const q = document.getElementById('monsterSearch').value.toLowerCase();
  const type = document.getElementById('monsterTypeFilter').value.toLowerCase();
  const crRaw = document.getElementById('monsterCrFilter').value;
  let crMin = 0, crMax = 99;
  if (crRaw) { const parts = crRaw.split('-'); crMin = parseFloat(parts[0]); crMax = parseFloat(parts[1]); }

  let count = 0;
  // Scoped to the monster grid: spell cards share the .monster-card class.
  document.querySelectorAll('#monsterGrid .monster-card').forEach(card => {
    const name = card.dataset.name;
    const mtype = card.dataset.type;
    const cr = parseFloat(card.dataset.cr);
    const match = (!q || name.includes(q)) && (!type || mtype === type) && cr >= crMin && cr <= crMax
      && (!_coreOnlyDM || isCoreSourceDM(card.dataset.source))
      && _srcOk(card.dataset.source, 'dm-monsters');
    card.style.display = match ? '' : 'none';
    if (match) count++;
  });
  document.getElementById('monsterCount').textContent = count + ' monsters';
}

// ── Monster detail ──
async function showMonster(index) {
  openModal('monsterModal');
  document.getElementById('monsterDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';
  try {
    const r = await fetch('/api/dm/monster/' + encodeURIComponent(index));
    const m = await r.json();
    const ac = m.armor_class ? m.armor_class.map(a => a.value + (a.type ? ' (' + a.type + ')' : '')).join(', ') : '?';
    const mod = s => Math.floor((s - 10) / 2);
    const sign = v => v >= 0 ? '+' + v : '' + v;

    let html = refArtImg('creature', m.name, 300) + `<h2 style="margin:0">${m.name}</h2>
      <p style="color:var(--text-muted);margin:0.3rem 0">${m.size} ${m.type} · ${m.alignment || 'Unaligned'}</p>
      ${m.source ? `<p style="font-size:0.75rem;color:var(--text-muted);margin:0 0 0.3rem 0;cursor:pointer" class="src-badge" data-src="${dmEsc(m.source)}">📚 ${m.source}</p>` : ''}
      <div style="display:flex;gap:1rem;margin:0.5rem 0;font-size:0.9rem;flex-wrap:wrap">
        <span><strong>AC</strong> ${ac}</span>
        <span><strong>HP</strong> ${m.hit_points} (${m.hit_dice})</span>
        <span><strong>Speed</strong> ${Object.values(m.speed || {}).join(', ') || '?'}</span>
        <span><strong>CR</strong> ${m.challenge_rating} (${m.xp || 0} XP)</span>
        <span><strong>PB</strong> ${m.proficiency_bonus || '+' + Math.ceil(m.challenge_rating / 4)}</span>
      </div>
      <div class="stat-grid">
        <div class="stat-item"><div class="si-label">STR</div><div class="si-val">${m.strength}</div><div class="si-mod">${sign(mod(m.strength))}</div></div>
        <div class="stat-item"><div class="si-label">DEX</div><div class="si-val">${m.dexterity}</div><div class="si-mod">${sign(mod(m.dexterity))}</div></div>
        <div class="stat-item"><div class="si-label">CON</div><div class="si-val">${m.constitution}</div><div class="si-mod">${sign(mod(m.constitution))}</div></div>
        <div class="stat-item"><div class="si-label">INT</div><div class="si-val">${m.intelligence}</div><div class="si-mod">${sign(mod(m.intelligence))}</div></div>
        <div class="stat-item"><div class="si-label">WIS</div><div class="si-val">${m.wisdom}</div><div class="si-mod">${sign(mod(m.wisdom))}</div></div>
        <div class="stat-item"><div class="si-label">CHA</div><div class="si-val">${m.charisma}</div><div class="si-mod">${sign(mod(m.charisma))}</div></div>
      </div>`;

    // Saving throws & skills — enriched from proficiencies
    let defHtml = '';
    if (m.proficiencies && m.proficiencies.length) {
      const saves = m.proficiencies.filter(p => p.proficiency && p.proficiency.name && p.proficiency.name.startsWith('Saving Throw'));
      const skills = m.proficiencies.filter(p => p.proficiency && p.proficiency.name && p.proficiency.name.startsWith('Skill'));
      if (saves.length) {
        defHtml += `<p style="margin:0.3rem 0"><strong>Saving Throws:</strong> `;
        defHtml += saves.map(p => `<span class="ability-tag">${p.proficiency.name.replace('Saving Throw: ', '')} ${p.value >= 0 ? '+' + p.value : p.value}</span>`).join(' ');
        defHtml += `</p>`;
      }
      if (skills.length) {
        defHtml += `<p style="margin:0.3rem 0"><strong>Skills:</strong> `;
        defHtml += skills.map(p => `<span class="ability-tag">${p.proficiency.name.replace('Skill: ', '')} +${p.value}</span>`).join(' ');
        defHtml += `</p>`;
      }
    }
    // Fallback
    if (!m.proficiencies || !m.proficiencies.find(p => p.proficiency && p.proficiency.name && p.proficiency.name.startsWith('Saving Throw'))) {
      defHtml += `<p style="margin:0.3rem 0;font-size:0.8rem;color:var(--text-muted)"><strong>Saving Throws (est.):</strong> `;
      for (const [name, val] of Object.entries({STR:m.strength,DEX:m.dexterity,CON:m.constitution,INT:m.intelligence,WIS:m.wisdom,CHA:m.charisma})) {
        defHtml += `<span style="margin-right:0.5rem">${name} ${sign(mod(val))}</span>`;
      }
      defHtml += `</p>`;
    }
    if (m.damage_resistances && m.damage_resistances.length) defHtml += `<p><strong>Resistances:</strong> ${m.damage_resistances.join(', ')}</p>`;
    if (m.damage_immunities && m.damage_immunities.length) defHtml += `<p><strong>Immunities:</strong> ${m.damage_immunities.join(', ')}</p>`;
    if (m.condition_immunities && m.condition_immunities.length) defHtml += `<p><strong>Condition Immunities:</strong> ${m.condition_immunities.map(c => c.name || c).join(', ')}</p>`;
    if (m.languages) defHtml += `<p><strong>Languages:</strong> ${m.languages}</p>`;
    if (m.senses) defHtml += `<p><strong>Senses:</strong> ${Object.entries(m.senses).map(([k,v]) => k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()) + ' ' + v).join(', ')}</p>`;
    if (defHtml) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🛡️ Defenses</h3></div>`;
      html += `<div class="collapse-body">${defHtml}</div>`;
    }

    // Action type badge helper
    const actionBadge = (name, desc) => {
      const nl = (name + ' ' + (desc || '')).toLowerCase();
      if (nl.includes('multiattack')) return '👊 Multiattack';
      if (nl.includes('melee weapon attack') || nl.includes('melee spell attack')) return '⚔️ Melee';
      if (nl.includes('ranged weapon attack') || nl.includes('ranged spell attack')) return '🏹 Ranged';
      if (name.toLowerCase().includes('breath')) return '💨 Breath';
      if (nl.includes('cast') || nl.includes('spell')) return '✨ Spell';
      return '';
    };

    // Special abilities / traits
    if (m.special_abilities && m.special_abilities.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🧬 Traits (${m.special_abilities.length})</h3></div>`;
      html += `<div class="collapse-body">`;
      m.special_abilities.forEach(a => {
        const badge = actionBadge(a.name, a.desc);
        html += `<div style="margin-bottom:0.5rem;font-size:0.85rem">${badge ? `<span class="action-badge">${badge}</span> ` : ''}<strong>${a.name}.</strong> ${a.desc}</div>`;
      });
      html += `</div>`;
    }

    // Actions
    if (m.actions && m.actions.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">⚔️ Actions (${m.actions.length})</h3></div>`;
      html += `<div class="collapse-body">`;
      m.actions.forEach(a => {
        const badge = actionBadge(a.name, a.desc);
        const dmgInfo = a.damage && a.damage.length ? a.damage.map(d => d.damage_dice + ' ' + (d.damage_type?.name || '')).join(', ') : '';
        const dcInfo = a.dc ? `DC ${a.dc.dc_value} ${a.dc.dc_type?.name || ''}` : '';
        html += `<div style="margin-bottom:0.5rem;font-size:0.85rem">`;
        if (badge) html += `<span class="action-badge">${badge}</span> `;
        html += `<strong>${a.name}.</strong> ${a.desc}`;
        if (a.attack_bonus) html += `<br><span style="color:var(--text-muted)">↠ +${a.attack_bonus} to hit${a.range ? ', Range: ' + a.range : ''}</span>`;
        if (dcInfo) html += `<br><span style="color:var(--warn)">↠ ${dcInfo}</span>`;
        if (dmgInfo) html += `<br><span style="color:var(--success)">↠ ${dmgInfo} damage</span>`;
        html += `</div>`;
      });
      html += `</div>`;
    }

    // Reactions
    if (m.reactions && m.reactions.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">↻ Reactions (${m.reactions.length})</h3></div>`;
      html += `<div class="collapse-body">`;
      m.reactions.forEach(a => {
        html += `<div style="margin-bottom:0.4rem;font-size:0.85rem"><span class="action-badge">↻ Reaction</span> <strong>${a.name}.</strong> ${a.desc}</div>`;
      });
      html += `</div>`;
    }

    // Legendary actions
    if (m.legendary_actions && m.legendary_actions.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">👑 Legendary Actions (${m.legendary_actions.length})</h3></div>`;
      html += `<div class="collapse-body">`;
      if (m.legendary_desc) html += `<p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.5rem">${m.legendary_desc}</p>`;
      m.legendary_actions.forEach(a => {
        html += `<div style="margin-bottom:0.3rem;font-size:0.85rem"><span class="action-badge">👑 Costs ${a.cost || 1}</span> <strong>${a.name}.</strong> ${a.desc}</div>`;
      });
      html += `</div>`;
    }

    // Lair actions
    if (m.lair_actions && m.lair_actions.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🏰 Lair Actions (${m.lair_actions.length})</h3></div>`;
      html += `<div class="collapse-body">`;
      if (m.lair_desc) html += `<p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.5rem">${m.lair_desc}</p>`;
      m.lair_actions.forEach(a => {
        html += `<div style="margin-bottom:0.3rem;font-size:0.85rem"><span class="action-badge">🏰 Lair</span> <strong>${a.name || 'Lair Action'}.</strong> ${a.desc || a}</div>`;
      });
      html += `</div>`;
    }

    document.getElementById('monsterDetail').innerHTML = html;
    // Any die expression in the block becomes a clickable roll (static/dice.js). Only the
    // freshly rendered popup is walked, so the sheet's own re-renders never fight it.
    if (window.DiceRoller) window.DiceRoller.enhance(document.getElementById('monsterDetail'));
  } catch(e) {
    document.getElementById('monsterDetail').innerHTML = '<p style="color:var(--danger)">Failed to load monster details.</p>';
  }
}

// ── Spell filtering ──
function filterSpells() {
  const q = document.getElementById('spellSearch').value.toLowerCase();
  const level = document.getElementById('spellLevelFilter').value;
  const school = document.getElementById('spellSchoolFilter').value.toLowerCase();
  const cls = document.getElementById('spellClassFilter').value.toLowerCase();

  let count = 0;
  document.querySelectorAll('#spellGrid .monster-card').forEach(card => {
    const name = card.dataset.name;
    const cLevel = card.dataset.level;
    const cSchool = card.dataset.school;
    const classes = (card.dataset.classes || '').split(',');
    const match = (!q || name.includes(q))
      && (!level || cLevel === level)
      && (!school || cSchool === school)
      && (!cls || classes.includes(cls))
      && (!_coreOnlyDM || isCoreSourceDM(card.dataset.source))
      && _srcOk(card.dataset.source, 'dm-spells');
    card.style.display = match ? '' : 'none';
    if (match) count++;
  });
  document.getElementById('spellCount').textContent = count + ' spells';
}

// ── Spell detail ──
async function showSpell(index) {
  openModal('spellModal');
  document.getElementById('spellDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';
  try {
    const r = await fetch('/api/dm/spell/' + encodeURIComponent(index));
    if (!r.ok) throw new Error('Not found');
    const s = await r.json();

    const comps = (s.components_raw || []).join('');
    const badges = [];
    if (s.ritual) badges.push('<span class="action-badge" style="background:var(--accent2);color:var(--text)">Ritual</span>');
    if (s.concentration) badges.push('<span class="action-badge" style="background:var(--warn);color:var(--text)">Concentration</span>');
    if (s.attack_summary) badges.push(`<span class="action-badge">↠ ${s.attack_summary}</span>`);

    let html = `<h2 style="margin:0">${s.name}</h2>
      <p style="color:var(--text-muted);margin:0.3rem 0">${s.level_label === 'Cantrip' ? 'Cantrip' : 'Level ' + s.level_label} · ${s.school_name}${s.attack_summary ? ' · ' + s.attack_summary : ''}</p>
      ${s.source ? `<p style="font-size:0.75rem;color:var(--text-muted);margin:0 0 0.3rem 0;cursor:pointer" class="src-badge" data-src="${dmEsc(s.source)}">📚 ${s.source}</p>` : ''}
      <div style="display:flex;gap:1rem;margin:0.5rem 0;font-size:0.9rem;flex-wrap:wrap">
        <span><strong>Casting</strong> ${s.casting_time}</span>
        <span><strong>Range</strong> ${s.range}</span>
        <span><strong>Components</strong> ${comps || '—'}${s.material ? ` <em style="color:var(--text-muted)">(${s.material})</em>` : ''}</span>
        <span><strong>Duration</strong> ${s.duration}</span>
      </div>
      <div style="display:flex;gap:0.4rem;margin:0.4rem 0;flex-wrap:wrap">${badges.join(' ')}</div>`;

    // Damage / DC / AoE strip
    const extras = [];
    if (s.damage_summary) extras.push(`<span><strong>Damage</strong> ${s.damage_summary}</span>`);
    if (s.dc_summary) extras.push(`<span><strong>Save</strong> ${s.dc_summary}</span>`);
    if (s.aoe_summary) extras.push(`<span><strong>Area</strong> ${s.aoe_summary}</span>`);
    if (s.attack_summary && !s.damage_summary) extras.push(`<span><strong>Attack</strong> ${s.attack_summary}</span>`);
    if (extras.length) {
      html += `<div style="display:flex;gap:1rem;margin:0.3rem 0;font-size:0.9rem;flex-wrap:wrap;color:var(--text-muted)">${extras.join('')}</div>`;
    }

    // Description
    if (s.desc && s.desc.length) {
      html += `<div style="margin-top:0.8rem;font-size:0.9rem;line-height:1.55">`;
      s.desc.forEach(p => { html += `<p style="margin:0.5rem 0">${p}</p>`; });
      html += `</div>`;
    }

    // Higher levels
    if (s.higher_level && s.higher_level.length) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">⬆️ At Higher Levels</h3></div>`;
      html += `<div class="collapse-body">`;
      s.higher_level.forEach(p => { html += `<p style="margin:0.4rem 0;font-size:0.9rem">${p}</p>`; });
      html += `</div>`;
    }

    // Damage scaling table
    if (s.damage && s.damage.damage_at_character_level) {
      const scale = s.damage.damage_at_character_level;
      const rows = Object.entries(scale).map(([lvl, dice]) => `<tr><td style="padding:0.2rem 0.6rem 0.2rem 0">${lvl === '0' ? 'Cantrip' : 'Level ' + lvl}</td><td style="padding:0.2rem 0.6rem"><strong>${dice}</strong></td></tr>`).join('');
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🎲 Damage Scaling</h3></div>`;
      html += `<div class="collapse-body"><table class="dm-table" style="width:auto"><tbody>${rows}</tbody></table></div>`;
    }

    // Slot scaling (for spells with damage_at_slot_level)
    if (s.damage && s.damage.damage_at_slot_level) {
      const scale = s.damage.damage_at_slot_level;
      const rows = Object.entries(scale).map(([lvl, dice]) => `<tr><td style="padding:0.2rem 0.6rem 0.2rem 0">Slot ${lvl}</td><td style="padding:0.2rem 0.6rem"><strong>${dice}</strong></td></tr>`).join('');
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🎲 Slot Scaling</h3></div>`;
      html += `<div class="collapse-body"><table class="dm-table" style="width:auto"><tbody>${rows}</tbody></table></div>`;
    }

    // Classes / subclasses
    const avail = [];
    if (s.class_names && s.class_names.length) avail.push(`<span><strong>Classes:</strong> ${s.class_names.join(', ')}</span>`);
    if (s.subclass_names && s.subclass_names.length) avail.push(`<span><strong>Subclasses:</strong> ${s.subclass_names.join(', ')}</span>`);
    if (avail.length) {
      html += `<div style="margin-top:0.8rem;font-size:0.85rem;color:var(--text-muted)">${avail.join('<br>')}</div>`;
    }

    document.getElementById('spellDetail').innerHTML = html;
  } catch(e) {
    document.getElementById('spellDetail').innerHTML = '<div style="text-align:center;padding:2rem;color:var(--danger)">Spell not found.</div>';
  }
}

