
// ── Source reference: click 📚 badge → open PDF ──
/* SOURCE_SLUG_MAP and NAMED_ITEM_TYPES set by template */  // server-side weapon/armor classification
function openSourceRef(src, slug) {
  if (!src || src.startsWith('SRD')) return;
  const pageMatch = src.match(/\b[pP]\.?\s*(\d+)/);
  const page = pageMatch ? parseInt(pageMatch[1]) : 0;
  // If we have a direct slug, use it immediately — no matching needed
  if (slug) {
    window.open(`/api/reference/open/${slug}${page?'?page='+page+'#page='+page:''}`, '_blank');
    return;
  }
  let bookPart = src.replace(/\s+[pP]\.?\s*\d+.*$/, '').replace(/\s+\d{4}$/, '').trim();
  // Try slug match first (e.g., "PHB", "DMG")
  for (const [slug, info] of Object.entries(SOURCE_SLUG_MAP)) {
    if (slug.toLowerCase() === bookPart.toLowerCase()) {
      window.open(`/api/reference/open/${slug}${page?'?page='+page+'#page='+page:''}`, '_blank');
      return;
    }
  }
  // Try display name match
  for (const [slug, info] of Object.entries(SOURCE_SLUG_MAP)) {
    if (info.display.toLowerCase() === bookPart.toLowerCase()) {
      window.open(`/api/reference/open/${slug}${page?'?page='+page+'#page='+page:''}`, '_blank');
      return;
    }
  }
  // Fallback: substring match
  for (const [slug, info] of Object.entries(SOURCE_SLUG_MAP)) {
    const d = info.display.toLowerCase();
    const b = bookPart.toLowerCase();
    if (d.includes(b) || b.includes(d)) {
      window.open(`/api/reference/open/${slug}${page?'?page='+page+'#page='+page:''}`, '_blank');
      return;
    }
  }
  // Last resort: normalized match (strip all non-alphanumeric)
  const norm = s => s.replace(/[^a-z0-9]/g, '');
  const bNorm = norm(bookPart);
  for (const [slug, info] of Object.entries(SOURCE_SLUG_MAP)) {
    const dNorm = norm(info.display);
    if (dNorm.includes(bNorm) || bNorm.includes(dNorm)) {
      window.open(`/api/reference/open/${slug}${page?'?page='+page+'#page='+page:''}`, '_blank');
      return;
    }
  }
  // No match found — don't open a broken URL, just alert
  alert(`📚 Could not find the source book for:\n"${src}"\n\nIt may reference a book not in the library.`);
}

// ── Tab switching (persisted to localStorage) ──
function activateTab(tabName) {
  document.querySelectorAll('.dm-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.dm-panel').forEach(p => p.classList.remove('active'));
  const tabBtn = document.querySelector(`.dm-tab[data-tab="${tabName}"]`);
  const panel = document.getElementById('panel-' + tabName);
  if (tabBtn) tabBtn.classList.add('active');
  if (panel) panel.classList.add('active');
  localStorage.setItem('dmToolsTab', tabName);
}

// Restore last active tab on page load
(function() {
  const saved = localStorage.getItem('dmToolsTab');
  const validTabs = ['campaigns','encounters','combat','monsters','npcs','items','traps'];
  const tab = validTabs.includes(saved) ? saved : 'campaigns';
  activateTab(tab);
})();

document.querySelectorAll('.dm-tab').forEach(tab => {
  tab.addEventListener('click', () => activateTab(tab.dataset.tab));
});

// ── Modal helpers ──
let _modalZBase = 1000;
function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  _modalZBase += 10;
  el.style.zIndex = _modalZBase;
  el.classList.add('open');
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('open');
}

function toggleCollapse(header) {
  const arrow = header.querySelector('.collapse-arrow');
  const body = header.nextElementSibling;
  if (!body || !body.classList.contains('collapse-body')) return;
  const isOpen = !body.classList.contains('hidden');
  if (isOpen) {
    body.classList.add('hidden');
    arrow.classList.remove('open');
  } else {
    body.classList.remove('hidden');
    arrow.classList.add('open');
  }
}
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', (e) => { if (e.target === m) m.classList.remove('open'); });
});

// ── Character Sheet Preview ──
function previewCharSheet(charId, charName) {
  document.getElementById('charSheetTitle').textContent = charName || 'Character Sheet';
  document.getElementById('charSheetFrame').src = `/character/${charId}?dm_preview=1`;
  openModal('charSheetModal');
}

// ── Core Content Toggle (DM Tools) ──
let _coreOnlyDM = localStorage.getItem('coreOnly') === 'true';
const _CORE_BOOKS_DM = ['phb','phb 2014','dmg','dmg 2014','mm','monster manual','xge','xanathar','tce','tasha','tce 2020','vgm','volo','mtf','mordenkainen','scag','sword coast','erlw','eberron','ftd','fizban','mpmm','mordenkainen presents','monsters of the multiverse','bpgg','bigby','glory of the giants'];
function isCoreSourceDM(src) {
  if (!src || src === 'SRD' || src.startsWith('SRD')) return true;
  const lower = src.toLowerCase();
  // Extract abbreviation before " p." if present
  const abbr = lower.split(' p.')[0].trim();
  for (const b of _CORE_BOOKS_DM) {
    if (abbr === b || abbr.startsWith(b) || lower.includes(b)) return true;
  }
  return false;
}
function toggleCoreDM() {
  _coreOnlyDM = !_coreOnlyDM;
  localStorage.setItem('coreOnly', _coreOnlyDM);
  document.getElementById('dm-core-box').textContent = _coreOnlyDM ? '☑' : '☐';
  document.getElementById('dm-core-toggle').style.color = _coreOnlyDM ? 'var(--accent)' : 'var(--text-muted)';
  // Re-filter whichever panel is active
  const active = document.querySelector('.dm-panel.active');
  if (active) {
    if (active.id === 'panel-monsters') filterMonsters();
    else if (active.id === 'panel-traps') filterTraps();
    else if (active.id === 'panel-items' && _itemsCampId) loadAllItems();
    else if (active.id === 'panel-combat') filterCombatCreatures();
  }
  // Also re-filter encounter builder palette if visible
  if (typeof filterCreaturePalette === 'function') filterCreaturePalette();
}
// Init toggle UI from localStorage
if (_coreOnlyDM) {
  document.getElementById('dm-core-box').textContent = '☑';
  document.getElementById('dm-core-toggle').style.color = 'var(--accent)';
}

// ── Monster filtering ──
function filterMonsters() {
  const q = document.getElementById('monsterSearch').value.toLowerCase();
  const type = document.getElementById('monsterTypeFilter').value.toLowerCase();
  const crRaw = document.getElementById('monsterCrFilter').value;
  let crMin = 0, crMax = 99;
  if (crRaw) { const parts = crRaw.split('-'); crMin = parseFloat(parts[0]); crMax = parseFloat(parts[1]); }

  let count = 0;
  document.querySelectorAll('.monster-card').forEach(card => {
    const name = card.dataset.name;
    const mtype = card.dataset.type;
    const cr = parseFloat(card.dataset.cr);
    const match = (!q || name.includes(q)) && (!type || mtype === type) && cr >= crMin && cr <= crMax
      && (!_coreOnlyDM || isCoreSourceDM(card.dataset.source));
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

    let html = `<h2 style="margin:0">${m.name}</h2>
      <p style="color:var(--text-muted);margin:0.3rem 0">${m.size} ${m.type} · ${m.alignment || 'Unaligned'}</p>
      ${m.source ? `<p style="font-size:0.75rem;color:var(--text-muted);margin:0 0 0.3rem 0;cursor:pointer" class="src-badge" onclick="openSourceRef(this.textContent.replace('📚 ',''))">📚 ${m.source}</p>` : ''}
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
  } catch(e) {
    document.getElementById('monsterDetail').innerHTML = '<p style="color:var(--danger)">Failed to load monster details.</p>';
  }
}

// ── Encounters ──
function showCreateEncounter() {
  const name = prompt('Encounter name:');
  if (!name) return;
  fetch('/api/dm/encounter/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description: '', location: '', environment: '', difficulty: 'medium'})
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
  });
}

// ── AI Encounter Builder ──
async function showAiEncounterBuilder() {
  openModal('aiEncounterModal');
  document.getElementById('aiEncounterResult').style.display = 'none';
  // Populate campaign dropdown
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const sel = document.getElementById('aiCampaignSelect');
    sel.innerHTML = '<option value="">— Manual party level/size —</option>';
    (d.campaigns || []).forEach(c => {
      sel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
    });
    document.getElementById('aiPartyPreview').style.display = 'none';
    const lvl = document.getElementById('aiPartyLevel');
    const sz = document.getElementById('aiPartySize');
    lvl.disabled = false;
    sz.disabled = false;
    lvl.value = 5;
    sz.value = 4;
  } catch(e) {}
}

async function onAiCampaignChange() {
  const sel = document.getElementById('aiCampaignSelect');
  const cid = sel.value;
  const preview = document.getElementById('aiPartyPreview');
  const lvl = document.getElementById('aiPartyLevel');
  const sz = document.getElementById('aiPartySize');
  if (!cid) {
    preview.style.display = 'none';
    return;
  }
  // Fetch party profile from campaign
  try {
    const r = await fetch(`/api/dm/ai/party-profile?campaign_id=${cid}`);
    const d = await r.json();
    if (d.profile) {
      const s = d.profile.summary;
      lvl.value = Math.max(1, Math.round(s.avg_level));
      sz.value = s.size;
      preview.style.display = 'block';
      preview.style.color = 'var(--accent)';
      preview.textContent = `${s.size} characters, avg L${s.avg_level} — encounter tailored to their stats`;
    } else if (d.campaign) {
      // Fallback to campaign's stored values when no characters linked
      lvl.value = d.campaign.party_level || 1;
      sz.value = d.campaign.party_size || 4;
      preview.style.display = 'block';
      preview.style.color = 'var(--accent)';
      preview.textContent = `L${lvl.value} · ${sz.value} players (campaign default)`;
    } else {
      preview.style.display = 'block';
      preview.style.color = 'var(--text-muted)';
      preview.textContent = 'No characters in this campaign. Enter values manually.';
    }
  } catch(e) {
    preview.style.display = 'block';
    preview.style.color = 'var(--danger)';
    preview.textContent = 'Failed to load party. Enter values manually.';
  }
}

async function generateAiEncounter(event) {
  event.preventDefault();
  const form = document.getElementById('aiEncounterForm');
  const data = Object.fromEntries(new FormData(form));
  data.party_level = parseInt(data.party_level) || 5;
  data.party_size = parseInt(data.party_size) || 4;

  const resultDiv = document.getElementById('aiEncounterResult');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<div style="text-align:center;padding:2rem">🧙 Crafting encounter...</div>';

  try {
    const r = await fetch('/api/dm/ai/build-encounter', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    const enc = await r.json();

    const difficultyColors = {Easy: 'var(--success)', Medium: 'var(--warn)', Hard: 'var(--danger)', Deadly: 'var(--danger)'};
    const diffColor = difficultyColors[enc.difficulty] || 'var(--text-muted)';

    let html = `<div style="border-top:1px solid var(--border);padding-top:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
        <h3 style="margin:0">${enc.name}</h3>
        <div style="display:flex;gap:0.3rem;align-items:center">
          <span class="badge badge-accent">${enc.difficulty}</span>
          <span style="font-size:0.8rem;color:var(--text-muted)">L${enc.party.level} · ${enc.party.size} players</span>
        </div>
      </div>`;

    if (enc.description) html += `<p style="color:var(--text-muted);font-size:0.85rem;margin:0.5rem 0">${enc.description}</p>`;

    // XP breakdown
    html += `<div style="display:flex;gap:1rem;margin:0.5rem 0;font-size:0.85rem;flex-wrap:wrap">
      <span><strong>Raw XP:</strong> ${enc.xp.raw_total}</span>
      <span><strong>Adjusted XP:</strong> ${enc.xp.adjusted}</span>
      <span><strong>Budget:</strong> ${enc.xp.budget}</span>
      <span><strong>Budget used:</strong> <span style="color:${diffColor}">${enc.xp.budget_pct}%</span></span>
    </div>`;

    // Composition
    html += `<div style="margin:0.5rem 0"><strong>Composition:</strong></div>`;
    enc.composition.forEach(m => {
      const srcBadge = m.source
        ? ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${m.source.replace(/'/g, "\\'")}', '')" title="${m.source}" style="cursor:pointer;background:var(--accent2);border:1px solid var(--border);font-size:0.7rem;padding:0.1rem 0.4rem;border-radius:4px;white-space:nowrap;flex-shrink:0;color:var(--text);display:inline-flex;align-items:center;gap:0.15rem">📚</span>`
        : '';
      html += `<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:0.4rem 0.5rem;background:var(--bg);border-radius:4px;margin-bottom:0.3rem;flex-wrap:wrap;gap:0.3rem">
        <span style="flex:1 1 auto;min-width:0;overflow-wrap:break-word;word-break:break-word;display:flex;align-items:center;gap:0.25rem;flex-wrap:wrap"><strong>${m.count}× ${m.name}</strong>${srcBadge}<span style="color:var(--text-muted);font-size:0.8rem;white-space:nowrap">${m.size} ${m.type} · CR ${m.cr}</span></span>
        <span style="font-size:0.8rem;color:var(--text-muted);flex-shrink:0">AC ${m.ac} HP ${m.hp} · ${m.xp * m.count} XP</span>
      </div>`;
    });

    if (enc.tactics) html += `<p style="font-size:0.85rem;color:var(--text-muted);margin-top:0.5rem"><strong>Tactics:</strong> ${enc.tactics}</p>`;
    if (enc.dynamic) html += `<p style="font-size:0.85rem;color:var(--accent);margin-top:0.25rem;border-left:3px solid var(--accent);padding-left:0.5rem">⚡ <strong>Dynamic:</strong> ${enc.dynamic}</p>`;

    // Store composition + tactics + dynamic for saveAiEncounter
    window._aiComposition = enc.composition;
    window._aiTactics = enc.tactics || '';
    window._aiDynamic = enc.dynamic || '';

    // Save button
    html += `<div style="margin-top:1rem;display:flex;gap:0.5rem">
      <button class="btn btn-primary" onclick="saveAiEncounter('${enc.name.replace(/'/g, "\\'")}', '${enc.description.replace(/'/g, "\\'")}', '${data.environment}', '${enc.difficulty.toLowerCase()}')">💾 Save as Encounter</button>
      <button class="btn btn-outline" onclick="closeModal('aiEncounterModal')">Close</button>
    </div></div>`;

    resultDiv.innerHTML = html;
  } catch(e) {
    resultDiv.innerHTML = '<p style="color:var(--danger)">Failed to generate encounter. Try again.</p>';
  }
  return false;
}

async function saveAiEncounter(name, description, environment, difficulty) {
  const tactics = window._aiTactics || '';
  const dynamic = window._aiDynamic || '';
  // Append dynamic element to notes
  const notes = dynamic ? (tactics + '\n⚡ Dynamic: ' + dynamic) : tactics;
  const r = await fetch('/api/dm/encounter/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description, environment, difficulty, location: '', notes})
  });
  const d = await r.json();
  if (!d.ok) return;
  const encId = d.id;
  // Add creatures from AI composition to the encounter
  let added = 0, failed = 0, total = 0;
  if (window._aiComposition && window._aiComposition.length) {
    for (const m of window._aiComposition) {
      total += m.count || 1;
      for (let i = 0; i < (m.count || 1); i++) {
        try {
          const ar = await fetch(`/api/dm/encounter/${encId}/add-creature`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              name: m.name, race: m.type || '', class_name: '', level: 1,
              hp: m.hp || 10, hp_max: m.hp || 10, ac: m.ac || 10,
              is_enemy: 1, role: `${m.size || ''} ${m.type || ''}`.trim(),
              xp_reward: m.xp || 0, _monster_index: m.index || '',
            })
          });
          const ad = await ar.json();
          if (ad.ok) added++;
          else failed++;
        } catch(e) { failed++; }
      }
    }
  }
  const note = failed > 0 ? ` (${failed} of ${total} creatures failed to save)` : '';
  window._aiComposition = null;
  window._aiTactics = null;
  window._aiDynamic = null;
  closeModal('aiEncounterModal');
  if (failed > 0) { alert(`Saved ${added} creatures to encounter${note}.`); }
  location.reload();
}

async function openEncounter(id) {
  openModal('encounterModal');
  document.getElementById('encounterDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';
  try {
    const r = await fetch(`/api/dm/encounter/${id}`);
    const d = await r.json();
    const enc = d.encounter;

    // Fetch available NPCs and monsters for adding
    const [nr, mr] = await Promise.all([
      fetch('/api/dm/npcs').then(r => r.json()),
      fetch('/api/dm/monsters').then(r => r.json())
    ]);
    const allNpcs = (nr.npcs || []).map(n => ({...n, _kind: 'npc'}));
    const allMonsters = (mr.monsters || []).map(m => ({
      id: `m_${m.index || m.name}`, name: m.name,
      race: m.type || '', class_name: '', level: (m.challenge_rating || '?'),
      hp_current: m.hit_points || 10, hp_max: m.hit_points || 10,
      ac: (m.armor_class && m.armor_class[0] ? m.armor_class[0].value : 10),
      is_enemy: 1, role: (m.size || '') + ' ' + (m.type || ''),
      xp_reward: m.xp || 0, _kind: 'monster',
      _raw: m  // preserve full monster data
    }));
    const allCreatures = [...allMonsters, ...allNpcs];  // monsters first so info buttons are visible
    window._creatureCache = allCreatures;  // store for onclick lookups

    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem">
      <h2 style="margin:0">${enc.name}</h2>
      <button class="btn btn-outline btn-sm" onclick="editEncounterMeta(${id})">✏️</button>
    </div>`;
    if (enc.description) html += `<p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.5rem">${enc.description}</p>`;
    if (enc.notes) html += `<details style="margin-bottom:0.5rem"><summary style="cursor:pointer;font-size:0.85rem;color:var(--accent);user-select:none">📋 Tactics & Notes</summary><p style="font-size:0.85rem;color:var(--text-muted);white-space:pre-wrap;margin:0.3rem 0 0 0.5rem;border-left:2px solid var(--accent);padding-left:0.5rem">${enc.notes}</p></details>`;

    // Encounter builder: searchable palette + tracking
    html += `<div class="encounter-builder">
      <div>
        <h4 style="margin-bottom:0.5rem">📋 Available Creatures</h4>
        <div style="margin-bottom:0.5rem;display:flex;gap:0.4rem;flex-wrap:wrap">
          <input type="text" id="creatureSearch" placeholder="Search creatures..."
            style="flex:1;min-width:150px;padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem"
            oninput="filterCreaturePalette()">
          <select id="creatureKindFilter" onchange="filterCreaturePalette()"
            style="padding:0.3rem 0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
            <option value="all">All</option>
            <option value="npc">NPCs</option>
            <option value="monster">Monsters</option>
          </select>
          <span style="font-size:0.75rem;color:var(--text-muted);align-self:center" id="creatureCount">${allCreatures.length}</span>
        </div>
        <div class="monster-palette" id="creaturePalette" style="max-height:400px;overflow-y:auto">`;
    allCreatures.forEach((c, ci) => {
      const kindBadge = c._kind === 'monster'
        ? '<span class="badge badge-accent" style="font-size:0.55rem">MON</span>'
        : c.id < 0 ? '<span class="badge badge-muted" style="font-size:0.55rem">📖</span>' : '';
      const sourceBadge = c._kind === 'monster' && c._raw && c._raw.source
        ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef('${c._raw.source.replace(/'/g, "\\'")}')" style="font-size:0.6rem;color:var(--text-muted);opacity:0.7;cursor:pointer" title="Click to open ${c._raw.source}">📚</span> `
        : '';
      const tagBadge = c._kind === 'monster' && c._raw && c._raw.tags && c._raw.tags.length
        ? c._raw.tags.map(t => `<span class="badge badge-muted" style="font-size:0.5rem;opacity:0.8">${t}</span>`).join(' ')
        : '';
      const hpDisplay = c._kind === 'monster'
        ? `HP ${c.hp_current}`
        : `HP ${c.hp_current}/${c.hp_max}`;
      const crDisplay = c._kind === 'monster' && c.level ? `CR ${c.level} · ` : '';
      const detailDisplay = c._kind === 'monster'
        ? `${crDisplay}AC ${c.ac} · ${c.race || '?'}`
        : `${c.race}${c.class_name ? ' L' + c.level + ' ' + c.class_name : ''} · AC ${c.ac}`;
      html += `<div class="creature-row" data-kind="${c._kind}" data-name="${c.name.toLowerCase()}" data-source="${(c._raw && c._raw.source) || ''}"
        style="display:flex;align-items:center;gap:0.3rem;padding:0.35rem 0.5rem;background:var(--bg);border-radius:4px;margin-bottom:0.25rem">
        ${c._kind === 'monster' && c._raw && c._raw.index ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showMonster('${c._raw.index}')" title="Monster details" style="font-size:0.65rem;padding:0.15rem 0.35rem;flex-shrink:0">ℹ️</button>` : (c._kind === 'npc' ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showNpcInfo(${c.id}, '${c.name.replace(/'/g, "\\'")}')" title="NPC details" style="font-size:0.65rem;padding:0.15rem 0.35rem;flex-shrink:0">ℹ️</button>` : '')}
        <span style="font-size:0.8rem;flex:1 1 auto;min-width:0;overflow-wrap:break-word;word-break:break-word">${kindBadge}${sourceBadge}${tagBadge}<strong>${c.name}</strong> <span style="color:var(--text-muted)">${detailDisplay} · ${hpDisplay}</span></span>
        <button class="btn btn-primary btn-sm" style="flex-shrink:0" onclick="addCreatureToEncounter(${id}, ${ci})">+ Add</button>
      </div>`;
    });
    html += `</div></div>
      <div>
        <h4 style="margin-bottom:0.5rem">⚡ Combat Tracker</h4>
        <div class="encounter-tracking">`;

    if (enc.participants && enc.participants.length) {
      const alive = enc.participants.filter(p => !p.defeated).length;
      html += `<div class="encounter-toolbar">
        <span style="font-size:0.8rem;color:var(--text-muted)">${alive}/${enc.participants.length} active · ${d.xp_total || 0} XP</span>
        <button class="btn btn-outline btn-sm" onclick="updateInitiatives(${id})">💾 Save All</button>
      </div>`;
      enc.participants.sort((a, b) => {
        // Sort: alive first (by initiative desc), then defeated
        if ((a.defeated || 0) !== (b.defeated || 0)) return (a.defeated ? 1 : -1);
        return (b.initiative || 0) - (a.initiative || 0);
      });
      enc.participants.forEach((p, idx) => {
        const hpPct = p.hp_max > 0 ? Math.round(p.hp_current / p.hp_max * 100) : 0;
        const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'danger' : hpPct < 50 ? 'warn' : 'ok';
        const isDefeated = p.defeated || hpPct <= 0;
        // Parse NPC spell slot data
        let npcSlots = {};
        try { npcSlots = typeof p.npc_spell_slots === 'string' ? JSON.parse(p.npc_spell_slots || '{}') : (p.npc_spell_slots || {}); } catch(e) {}
        // Parse encounter-level spell_slots_used for this participant
        let usedSlots = {};
        try { usedSlots = typeof p.spell_slots_used === 'string' ? JSON.parse(p.spell_slots_used || '{}') : (p.spell_slots_used || {}); } catch(e) {}
        // Detect if NPC has spells (has class_name that's a caster, or has spell_slot_data)
        const hasSpells = p.class_name && ['Bard','Cleric','Druid','Paladin','Ranger','Sorcerer','Warlock','Wizard','Artificer'].includes(p.class_name);
        const isCaster = hasSpells && Object.keys(npcSlots).length > 0;

        html += `<div class="participant-row${isDefeated ? ' defeated' : ''}" onclick="toggleParticipantStats(${idx})">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
              <span class="badge ${p.is_enemy ? 'badge-accent' : 'badge-muted'}" style="font-size:0.65rem">${p.is_enemy ? 'ENEMY' : 'ALLY'}</span>
              <strong class="p-name" style="font-size:0.85rem">${p.npc_name || '?'}</strong>
              <span style="font-size:0.75rem;color:var(--text-muted)">L${p.level} ${p.role || ''}</span>
              <span style="font-size:0.75rem;color:var(--text-muted)">AC ${p.ac || p.npc_ac || '?'}</span>
              ${isCaster ? '<span style="font-size:0.7rem;color:var(--accent)">🔮 Caster</span>' : ''}
            </div>
            <div style="display:flex;align-items:center;gap:0.4rem;margin-top:0.15rem;flex-wrap:wrap">
              <input type="number" class="init-input" value="${p.initiative || 0}" data-enid="${p.id}" placeholder="Init" onclick="event.stopPropagation()" onchange="markDirty()">
              <span style="font-size:0.75rem">HP</span>
              <input type="number" class="hp-input" value="${p.hp_current}" data-enid="${p.id}" placeholder="HP" onclick="event.stopPropagation()" onchange="markDirty()" style="width:50px">
              <span style="font-size:0.75rem">/${p.hp_max}</span>
              <div class="hp-bar-mini" style="width:80px"><div class="hp-bar-mini-fill ${hpClass}" style="width:${Math.max(0, hpPct)}%"></div></div>
              <button class="defeat-btn ${isDefeated ? 'dead' : 'alive'}" onclick="event.stopPropagation();toggleDefeated(${id}, ${p.id}, ${idx})">${isDefeated ? '✓ Alive' : '💀 Defeat'}</button>
              <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();removeNpcFromEncounter(${id}, ${p.id})" title="Remove">✕</button>
              ${p._monster_index ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showMonster('${p._monster_index}')" title="Monster details" style="font-size:0.7rem;padding:0.15rem 0.4rem">ℹ️</button>` : (p.npc_id && p.npc_id > 0 ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showNpcEditor(${p.npc_id})" title="NPC details" style="font-size:0.7rem;padding:0.15rem 0.4rem">ℹ️</button>` : '')}
            </div>
          </div>
        </div>
        <div class="p-stats" id="p-stats-${idx}">
          <div class="ps-grid">
            <div class="ps-stat"><div class="ps-lbl">Initiative</div><div class="ps-val">${p.initiative || '-'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">AC</div><div class="ps-val">${p.ac || p.npc_ac || '?'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">HP</div><div class="ps-val">${p.hp_current}/${p.hp_max}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Race</div><div class="ps-val">${p.race || '?'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Class</div><div class="ps-val">${p.class_name || '—'}</div></div>
            <div class="ps-stat"><div class="ps-lbl">Level</div><div class="ps-val">${p.level || '?'}</div></div>
          </div>`;


        // Spell slot trackers (for caster NPCs)
        if (isCaster) {
          html += `<div style="margin-top:0.3rem"><strong style="font-size:0.75rem">🔮 Spell Slots</strong>`;
          // Determine max spell level from slots data
          const maxLevel = Math.max(...Object.keys(npcSlots).map(Number).filter(k => k > 0 && k < 10), 0);
          for (let lvl = 1; lvl <= maxLevel; lvl++) {
            const max = npcSlots[lvl] || 0;
            const used = usedSlots[lvl] || 0;
            if (max > 0) {
              html += `<div class="spell-slot-row">
                <span style="font-size:0.7rem;width:24px">L${lvl}</span>
                <div class="spell-slot-track">`;
              for (let s = 0; s < max; s++) {
                html += `<div class="spell-slot-dot ${s < used ? 'used' : ''}" onclick="event.stopPropagation();toggleSlot(${id}, ${p.id}, ${idx}, ${lvl}, ${s}, ${max})" title="${s < used ? 'Used — click to restore' : 'Available — click to expend'}"></div>`;
              }
              html += `</div>
                <span style="font-size:0.65rem;color:var(--text-muted)">${used}/${max}</span>
              </div>`;
            }
          }
          html += `</div>`;
        }

        html += `</div>`;
      });
    } else {
      html += `<div class="empty-state" style="padding:1rem"><p>No participants. Add NPCs from the palette.</p></div>`;
    }

    html += `</div></div></div>
      <div style="text-align:right;margin-top:0.75rem">
        <button class="btn btn-danger btn-sm" onclick="deleteEncounter(${id});closeModal('encounterModal')">✕ Delete Encounter</button>
      </div>`;
    document.getElementById('encounterDetail').innerHTML = html;
  } catch(e) {
    document.getElementById('encounterDetail').innerHTML = '<p style="color:var(--danger)">Failed to load encounter.</p>';
  }
}

function editEncounterMeta(id) {
  const name = prompt('Encounter name:');
  if (!name) return;
  const location = prompt('Location:', '') || '';
  const difficulty = prompt('Difficulty (easy/medium/hard/deadly):', 'medium') || 'medium';
  fetch(`/api/dm/encounter/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, location, difficulty, status: 'active'})
  }).then(r => r.json()).then(() => openEncounter(id));
}

function addNpcToEncounter(encId, npcId) {
  fetch(`/api/dm/encounter/${encId}/add-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(() => openEncounter(encId));
}

// ── Add creature (monster or manual NPC) to encounter ──
function addCreatureToEncounter(encId, creatureIndex) {
  const c = window._creatureCache && window._creatureCache[creatureIndex];
  if (!c) return;
  if (c._kind === 'npc' && c.id > 0) {
    // DB NPC — use add-npc
    return addNpcToEncounter(encId, c.id);
  }
  // Monster or manual NPC — use add-creature
  fetch(`/api/dm/encounter/${encId}/add-creature`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: c.name, race: c.race, class_name: c.class_name,
      level: typeof c.level === 'number' ? c.level : 1,
      hp: c.hp_current, hp_max: c.hp_max, ac: c.ac,
      is_enemy: c.is_enemy || 0, role: c.role || '',
      xp_reward: c.xp_reward || 0,
      _monster_index: c.id || c._raw?.index || '',
      // Full stat block fields (from manual NPCs, SRD monsters)
      ability_scores: c.ability_scores || null,
      spellcasting: c.spellcasting || null,
      features: c.features || c.special_abilities || [],
      actions: c.actions || [],
      skills: c.skills || {},
      saving_throws: c.saving_throws || {},
      speed: c.speed || '',
      alignment: c.alignment || '',
      description: c.description || '',
      equipment: c.equipment || [],
      senses: c.senses || '',
      languages: c.languages || [],
      damage_resistances: c.damage_resistances || [],
      damage_immunities: c.damage_immunities || [],
      condition_immunities: c.condition_immunities || [],
      challenge_rating: c.challenge_rating || null,
    })
  }).then(r => r.json()).then(() => openEncounter(encId));
}

// ── Filter creature palette ──
function filterCreaturePalette() {
  const q = (document.getElementById('creatureSearch')?.value || '').toLowerCase();
  const kind = document.getElementById('creatureKindFilter')?.value || 'all';
  let count = 0;
  document.querySelectorAll('.creature-row').forEach(row => {
    const name = row.dataset.name || '';
    const rowKind = row.dataset.kind || '';
    const match = (!q || name.includes(q)) && (kind === 'all' || rowKind === kind)
      && (!_coreOnlyDM || rowKind === 'npc' || isCoreSourceDM(row.dataset.source || ''));
    row.style.display = match ? '' : 'none';
    if (match) count++;
  });
  const countEl = document.getElementById('creatureCount');
  if (countEl) countEl.textContent = count;
}

function removeNpcFromEncounter(encId, enId) {
  fetch(`/api/dm/encounter/${encId}/remove-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({en_id: enId})
  }).then(r => r.json()).then(() => openEncounter(encId));
}

function updateInitiatives(encId) {
  const btn = document.querySelector('.encounter-toolbar .btn-outline');
  if (btn) { btn.textContent = '⏳ Saving...'; btn.disabled = true; }
  const participants = [];
  document.querySelectorAll('.participant-row').forEach(row => {
    const initInput = row.querySelector('.init-input');
    const hpInput = row.querySelector('.hp-input');
    if (initInput) {
      const entry = {
        id: parseInt(initInput.dataset.enid),
        initiative: parseInt(initInput.value) || 0,
        defeated: row.classList.contains('defeated') ? true : false,
      };
      if (hpInput && hpInput.value.trim() !== '') {
        const hp = parseInt(hpInput.value);
        if (!isNaN(hp)) entry.hp_current = hp;
      }
      participants.push(entry);
    }
  });
  fetch(`/api/dm/encounter/${encId}/update-initiative`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({participants})
  }).then(r => r.json()).then(data => {
    if (btn) {
      btn.textContent = '✅ Saved!';
      btn.style.background = 'var(--success)'; btn.style.color = '#fff'; btn.style.borderColor = 'var(--success)';
      setTimeout(() => {
        btn.textContent = '💾 Save All'; btn.disabled = false;
        btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = '';
      }, 2000);
    }
  }).catch(() => {
    if (btn) {
      btn.textContent = '❌ Failed';
      btn.style.background = 'var(--danger)'; btn.style.color = '#fff';
      setTimeout(() => {
        btn.textContent = '💾 Save All'; btn.disabled = false;
        btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = '';
      }, 2500);
    }
  });
}

// ── Participant helpers ──
function toggleParticipantStats(idx) {
  const el = document.getElementById('p-stats-' + idx);
  if (el) el.classList.toggle('open');
}

function toggleDefeated(encId, enId, idx) {
  const row = document.querySelector(`.participant-row input[data-enid="${enId}"]`)?.closest('.participant-row');
  const isDead = row ? row.classList.contains('defeated') : false;
  if (row) {
    row.classList.toggle('defeated');
    const btn = row.querySelector('.defeat-btn');
    if (btn) {
      btn.textContent = isDead ? '💀 Defeat' : '✓ Alive';
      btn.className = 'defeat-btn ' + (isDead ? 'alive' : 'dead');
    }
  }
  // Save immediately
  fetch(`/api/dm/encounter/${encId}/update-initiative`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({single: {id: enId, defeated: !isDead}})
  }).then(r => r.json()).then(() => {});
}

function toggleSlot(encId, enId, idx, level, slotIdx, max) {
  // Get the stat block
  const statBlock = document.getElementById('p-stats-' + idx);
  if (!statBlock) return;
  // Find the dots for this level
  const levelRow = statBlock.querySelectorAll('.spell-slot-row')[level - 1];
  if (!levelRow) return;
  const dots = levelRow.querySelectorAll('.spell-slot-dot');
  if (slotIdx < dots.length) {
    const dot = dots[slotIdx];
    const wasUsed = dot.classList.contains('used');
    // Toggle this dot
    if (wasUsed) {
      dot.classList.remove('used');
    } else {
      dot.classList.add('used');
    }
    // Recompute used count for this level
    let used = 0;
    dots.forEach((d, i) => {
      if (i < dots.length && d.classList.contains('used')) used++;
    });
    // Update the text
    const textSpan = levelRow.querySelector('span:last-child');
    if (textSpan) textSpan.textContent = used + '/' + max;
  }
  // Save
  const newUsed = {};
  statBlock.querySelectorAll('.spell-slot-row').forEach((row, lvl) => {
    const lvlNum = lvl + 1;
    const used = row.querySelectorAll('.spell-slot-dot.used').length;
    if (used > 0) newUsed[lvlNum] = used;
  });
  fetch(`/api/dm/encounter/${encId}/update-initiative`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({single: {id: enId, spell_slots_used: newUsed}})
  }).then(r => r.json()).then(() => {});
}

function markDirty() {
  // Visual indicator that unsaved changes exist
  const btn = document.querySelector('.encounter-toolbar .btn-outline');
  if (btn && !btn.textContent.includes('*')) {
    btn.textContent = '💾 Save All *';
    btn.style.borderColor = 'var(--warning)';
    btn.style.color = 'var(--warning)';
  }
}

function deleteEncounter(id) {
  if (!confirm('Delete this encounter?')) return;
  fetch(`/api/dm/encounter/${id}/delete`, {method: 'POST'})
    .then(r => r.json()).then(() => location.reload());
}

function filterEncounters() {
  const status = document.getElementById('encounterFilter').value;
  document.querySelectorAll('.encounter-card').forEach(card => {
    card.style.display = (!status || card.dataset.status === status) ? '' : 'none';
  });
}

// ── NPCs ──
function showCreateNpc() {
  showNpcEditor(null);
}

function editNpc(id) {
  showNpcEditor(id);
}

// Show NPC info — editable for DB NPCs, read-only for manual NPCs
function showNpcInfo(id, name) {
  if (id > 0) { showNpcEditor(id); return; }
  // Manual NPC: show read-only stat card from cache
  const c = (window._creatureCache || []).find(c => c.id === id && c._kind === 'npc');
  if (!c) return;
  openModal('npcModal');
  let html = `<h2 style="margin:0 0 0.5rem 0">📖 ${c.name || name}</h2>
    <p style="color:var(--text-muted);font-size:0.8rem;margin-bottom:1rem">Reference NPC (read-only)</p>
    <div class="ps-grid">
      <div class="ps-stat"><div class="ps-lbl">Race</div><div class="ps-val">${c.race || '?'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">Class</div><div class="ps-val">${c.class_name || '—'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">Level</div><div class="ps-val">${c.level || '?'}</div></div>
      <div class="ps-stat"><div class="ps-lbl">HP</div><div class="ps-val">${c.hp_current}/${c.hp_max}</div></div>
      <div class="ps-stat"><div class="ps-lbl">AC</div><div class="ps-val">${c.ac}</div></div>
      <div class="ps-stat"><div class="ps-lbl">XP</div><div class="ps-val">${c.xp_reward || 0}</div></div>
    </div>
    <div style="margin-top:1rem;text-align:right">
      <button class="btn btn-outline btn-sm" onclick="closeModal('npcModal')">Close</button>
    </div>`;
  document.getElementById('npcEditor').innerHTML = html;
}

async function showNpcEditor(id) {
  openModal('npcModal');
  document.getElementById('npcEditor').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';

  let npc = {name: '', race: 'Human', class_name: 'Fighter', subclass: '', level: 1,
    strength: 10, dexterity: 10, constitution: 10, intelligence: 10, wisdom: 10, charisma: 10,
    hp_max: 10, hp_current: 10, ac: 10, speed: 30, alignment: 'True Neutral', role: 'NPC',
    faction: '', notes: '', is_enemy: false, xp_reward: 0};

  if (id) {
    try {
      const r = await fetch(`/api/dm/npc/${id}`);
      npc = await r.json();
    } catch(e) {}
  }

  const races = ['Dwarf','Elf','Halfling','Human','Dragonborn','Gnome','Half-Elf','Half-Orc','Tiefling'];
  const classes = ['Barbarian','Bard','Cleric','Druid','Fighter','Monk','Paladin','Ranger','Rogue','Sorcerer','Warlock','Wizard'];
  const alignments = ['Lawful Good','Neutral Good','Chaotic Good','Lawful Neutral','True Neutral','Chaotic Neutral','Lawful Evil','Neutral Evil','Chaotic Evil'];
  const roles = ['NPC','Guard','Merchant','Artisan','Noble','Cultist','Bandit','Assassin','Spy','Prisoner','Guardian','Villain','Lieutenant','Boss','Minion'];

  let html = `<h2 style="margin:0 0 1rem 0">${id ? '✏️ Edit' : '➕ Create'} NPC</h2>
    <form id="npcForm" onsubmit="return saveNpc(event, ${id || 'null'})">
      <div class="form-row">
        <div class="form-group"><label>Name</label><input name="name" value="${npc.name || ''}" required></div>
        <div style="display:flex;gap:1rem;align-items:center;padding-top:1.5rem">
          <label><input type="checkbox" name="is_enemy" ${npc.is_enemy ? 'checked' : ''}> Enemy/Villain</label>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Race</label><select name="race">${races.map(r => `<option value="${r}" ${npc.race === r ? 'selected' : ''}>${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Class</label><select name="class_name">${classes.map(c => `<option value="${c}" ${npc.class_name === c ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Subclass</label><input name="subclass" value="${npc.subclass || ''}"></div>
        <div class="form-group"><label>Level</label><input name="level" type="number" min="1" max="20" value="${npc.level}"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Role</label><select name="role">${roles.map(r => `<option value="${r}" ${npc.role === r ? 'selected' : ''}>${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Alignment</label><select name="alignment">${alignments.map(a => `<option value="${a}" ${npc.alignment === a ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Faction</label><input name="faction" value="${npc.faction || ''}"></div>
        <div class="form-group"><label>XP Reward</label><input name="xp_reward" type="number" min="0" value="${npc.xp_reward || 0}"></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.5rem;margin:0.75rem 0">
        ${['strength','dexterity','constitution','intelligence','wisdom','charisma'].map(s => `
          <div class="form-group"><label style="font-size:0.7rem;text-align:center">${s.slice(0,3).toUpperCase()}</label>
          <input name="${s}" type="number" min="1" max="30" value="${npc[s] || 10}" style="text-align:center"></div>
        `).join('')}
      </div>
      <div class="form-row">
        <div class="form-group"><label>HP Max</label><input name="hp_max" type="number" value="${npc.hp_max}" min="1"></div>
        <div class="form-group"><label>HP Current</label><input name="hp_current" type="number" value="${npc.hp_current}" min="0"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>AC</label><input name="ac" type="number" value="${npc.ac}" min="1"></div>
        <div class="form-group"><label>Speed</label><input name="speed" type="number" value="${npc.speed}" min="0"></div>
      </div>
      <div class="form-group"><label>Notes</label><textarea name="notes" rows="3" style="width:100%;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">${npc.notes || ''}</textarea></div>
      <div style="display:flex;gap:0.5rem;margin-top:1rem">
        <button type="submit" class="btn btn-primary">Save NPC</button>
        <button type="button" class="btn btn-outline" onclick="closeModal('npcModal')">Cancel</button>
      </div>
    </form>`;
  document.getElementById('npcEditor').innerHTML = html;
}

async function saveNpc(event, id) {
  event.preventDefault();
  const form = document.getElementById('npcForm');
  const data = Object.fromEntries(new FormData(form));
  data.is_enemy = form.querySelector('[name="is_enemy"]').checked ? true : false;
  data.level = parseInt(data.level) || 1;

  const url = id ? `/api/dm/npc/${id}/update` : '/api/dm/npc/create';
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await r.json();
  if (result.ok) { closeModal('npcModal'); location.reload(); }
  return false;
}

function deleteNpc(id) {
  if (!confirm('Delete this NPC?')) return;
  fetch(`/api/dm/npc/${id}/delete`, {method: 'POST'}).then(r => r.json()).then(() => location.reload());
}

function filterNpcs() {
  const q = document.getElementById('npcSearch').value.toLowerCase();
  document.querySelectorAll('.npc-row').forEach(row => {
    row.style.display = row.dataset.name.includes(q) ? '' : 'none';
  });
}

// ── AI NPC Builder ──
function handleNpcCrChange(sel) {
  const levelInput = document.querySelector('#aiNpcForm input[name=level]');
  const hint = document.getElementById('crLevelHint');
  if (!sel.value) {
    levelInput.disabled = false;
    levelInput.style.opacity = '1';
    hint.style.display = 'none';
    return;
  }
  // CR → level: L ≈ CR*2 + 1
  let cr;
  if (sel.value.includes('/')) {
    const parts = sel.value.split('/');
    cr = parseFloat(parts[0]) / parseFloat(parts[1]);
  } else {
    cr = parseFloat(sel.value);
  }
  const level = Math.min(20, Math.max(1, Math.round(cr * 2) + 1));
  levelInput.value = level;
  levelInput.disabled = true;
  levelInput.style.opacity = '0.6';
  hint.textContent = `→ L${level}`;
  hint.style.display = 'inline';
}

function showAiNpcWizard() {
  openModal('npcModal');
  const races = ['Human','Elf','Dwarf','Halfling','Dragonborn','Gnome','Half-Elf','Half-Orc','Tiefling'];
  const classes = ['Barbarian','Bard','Cleric','Druid','Fighter','Monk','Paladin','Ranger','Rogue','Sorcerer','Warlock','Wizard'];

  let html = `<h2 style="margin:0 0 1rem 0">🤖 AI Build NPC</h2>
    <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:1rem">AI generates name, personality, backstory + full PHB-grounded build.</p>
    <form id="aiNpcForm" onsubmit="return generateAiNpc(event)">
      <div class="form-row">
        <div class="form-group"><label>Race</label><select name="race">${races.map(r => `<option value="${r}">${r}</option>`).join('')}</select></div>
        <div class="form-group"><label>Class</label><select name="class_name">${classes.map(c => `<option value="${c}">${c}</option>`).join('')}</select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Subclass (optional)</label><input name="subclass" placeholder="e.g. Champion, Lore"></div>
        <div class="form-group"><label>Level</label><input name="level" type="number" min="1" max="20" value="5"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Target CR (optional — auto-sets level)</label>
          <select name="target_cr" onchange="handleNpcCrChange(this)">
            <option value="">— Use manual level —</option>
            <option value="0">0</option>
            <option value="1/8">1/8</option>
            <option value="1/4">1/4</option>
            <option value="1/2">1/2</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
            <option value="5">5</option>
            <option value="6">6</option>
            <option value="7">7</option>
            <option value="8">8</option>
            <option value="9">9</option>
            <option value="10">10</option>
            <option value="11">11</option>
            <option value="12">12</option>
            <option value="13">13</option>
            <option value="14">14</option>
            <option value="15">15</option>
            <option value="16">16</option>
            <option value="17">17</option>
            <option value="18">18</option>
            <option value="19">19</option>
            <option value="20">20</option>
          </select>
          <span id="crLevelHint" style="font-size:0.7rem;color:var(--accent);display:none"></span>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Role</label>
          <select name="role"><option value="NPC">Friendly NPC</option><option value="Guard">Guard</option><option value="Bandit">Bandit</option><option value="Cultist">Cultist</option><option value="Merchant">Merchant</option><option value="Villain" selected>Villain</option></select></div>
        <div class="form-group"><label>Type</label>
          <select name="is_enemy"><option value="0">Friendly NPC</option><option value="1" selected>Enemy / Monster</option></select></div>
      </div>
      <div class="form-group"><label>Personality hint (optional)</label><input name="personality_hint" placeholder="e.g. sinister, greedy, wise"></div>
      <div style="display:flex;gap:0.5rem;margin-top:1rem">
        <button type="submit" class="btn btn-primary">🤖 Generate NPC</button>
        <button type="button" class="btn btn-outline" onclick="closeModal('npcModal')">Cancel</button>
      </div>
    </form>`;
  document.getElementById('npcEditor').innerHTML = html;
}

async function generateAiNpc(event) {
  event.preventDefault();
  const form = document.getElementById('aiNpcForm');
  const data = Object.fromEntries(new FormData(form));
  data.is_enemy = data.is_enemy === '1';
  data.level = parseInt(data.level) || 5;

  document.getElementById('npcEditor').innerHTML = '<div style="text-align:center;padding:2rem">🧙 Generating NPC...</div>';

  try {
    const r = await fetch('/api/dm/ai/build-npc', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    const result = await r.json();
    const build = result.build || {};

    // Save to DB
    const saveData = {
      name: result.name || data.race + ' ' + data.role,
      race: data.race,
      class_name: data.class_name,
      subclass: data.subclass || '',
      level: data.level,
      is_enemy: data.is_enemy,
      role: data.role,
      alignment: result.alignment || 'True Neutral',
      faction: result.faction || '',
      notes: (result.personality ? 'Personality: ' + result.personality + '\n' : '') + (result.backstory ? 'Backstory: ' + result.backstory : ''),
      xp_reward: build.hit_points || 0,
      strength: build.ability_scores?.strength || 10,
      dexterity: build.ability_scores?.dexterity || 10,
      constitution: build.ability_scores?.constitution || 10,
      intelligence: build.ability_scores?.intelligence || 10,
      wisdom: build.ability_scores?.wisdom || 10,
      charisma: build.ability_scores?.charisma || 10,
      hp_max: build.hit_points || 10,
      hp_current: build.hit_points || 10,
      ac: build.armor_class || 10,
      speed: build.speed || 30,
      proficiency_bonus: build.proficiency_bonus || 2,
      hit_dice: build.hit_dice || '1d8',
      skills: build.skills || [],
      features: build.features || [],
      inventory: build.equipment || [],
    };

    const sr = await fetch('/api/dm/npc/create', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(saveData)
    });
    const saveResult = await sr.json();
    if (saveResult.ok) { closeModal('npcModal'); location.reload(); }
  } catch(e) {
    document.getElementById('npcEditor').innerHTML = '<p style="color:var(--danger)">Failed to generate NPC. Try again.</p>';
  }
}

// ── Traps ──
function toggleTrapDetail(btn) {
  const card = btn.closest('.trap-card');
  const detail = card.querySelector('.trap-detail');
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? '▶ Expand' : '▼ Collapse';
}

function filterTraps() {
  const query = (document.getElementById('trapSearch')?.value || '').toLowerCase().trim();
  const typeFilter = document.getElementById('trapTypeFilter')?.value || '';
  const dangerFilter = document.getElementById('trapDangerFilter')?.value || '';
  let count = 0;
  document.querySelectorAll('.trap-card').forEach(card => {
    const name = card.getAttribute('data-name') || '';
    const type = card.getAttribute('data-type') || '';
    const danger = card.getAttribute('data-danger') || '';
    const match = (!query || name.includes(query))
      && (!typeFilter || type === typeFilter)
      && (!dangerFilter || danger === dangerFilter)
      && (!_coreOnlyDM || isCoreSourceDM(card.getAttribute('data-source') || ''));
    card.style.display = match ? '' : 'none';
    if (match) count++;
  });
  const countEl = document.getElementById('trapCount');
  if (countEl) countEl.textContent = count + ' trap' + (count !== 1 ? 's' : '');
}

// ── Custom trap create/edit ──
const TRAP_DANGER_PRESETS = {
  setback:  { dc: 10, damage: '1d10' },
  dangerous: { dc: 15, damage: '2d10' },
  deadly:   { dc: 20, damage: '4d10' }
};

function showCreateTrap() {
  document.getElementById('trapModalTitle').textContent = '⚙️ Create Custom Trap';
  document.getElementById('trapEditId').value = '';
  document.getElementById('trapForm').reset();
  document.getElementById('trapDanger').value = 'dangerous';
  onTrapDangerChange();
  openModal('trapModal');
}

function onTrapDangerChange() {
  const danger = document.getElementById('trapDanger').value;
  const preset = TRAP_DANGER_PRESETS[danger];
  if (preset) {
    document.getElementById('trapSaveDc').value = preset.dc;
    if (!document.getElementById('trapDamage').value) {
      document.getElementById('trapDamage').value = preset.damage;
    }
  }
}

async function saveTrap(event) {
  event.preventDefault();
  const id = document.getElementById('trapEditId').value;
  const data = {
    name: document.getElementById('trapName').value.trim(),
    type: document.getElementById('trapType').value,
    danger: document.getElementById('trapDanger').value,
    save_dc: parseInt(document.getElementById('trapSaveDc').value) || null,
    save_ability: document.getElementById('trapSaveAbility').value,
    damage: document.getElementById('trapDamage').value.trim(),
    damage_type: document.getElementById('trapDamageType').value,
    area: document.getElementById('trapArea').value.trim(),
    trigger: document.getElementById('trapTrigger').value.trim(),
    detection_dc: parseInt(document.getElementById('trapDetectDc').value) || null,
    detection_skill: document.getElementById('trapDetectSkill').value,
    detection_detail: document.getElementById('trapDetectDetail').value.trim(),
    disarm_dc: parseInt(document.getElementById('trapDisarmDc').value) || null,
    disarm_method: document.getElementById('trapDisarmMethod').value.trim(),
    disarm_detail: document.getElementById('trapDisarmDetail').value.trim(),
    effect: document.getElementById('trapEffect').value.trim(),
    description: document.getElementById('trapDesc').value.trim()
  };
  if (!data.name) return alert('Name is required.');

  const url = id ? `/api/dm/traps/${id}/update` : '/api/dm/traps/create';
  try {
    const r = await fetch(url, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (!d.ok && d.error) return alert(d.error);
    closeModal('trapModal');
    location.reload();
  } catch(e) { alert('Failed to save trap.'); }
}

async function editTrap(id) {
  try {
    const r = await fetch('/api/dm/traps');
    const d = await r.json();
    const t = (d.traps || []).find(t => t.id === id);
    if (!t) return alert('Trap not found');

    document.getElementById('trapModalTitle').textContent = '✏️ Edit Trap';
    document.getElementById('trapEditId').value = t.id;
    document.getElementById('trapName').value = t.name || '';
    document.getElementById('trapType').value = t.type || 'mechanical';
    document.getElementById('trapDanger').value = t.danger || 'dangerous';
    document.getElementById('trapSaveDc').value = t.save_dc || '';
    document.getElementById('trapSaveAbility').value = t.save_ability || 'Dexterity';
    document.getElementById('trapDamage').value = t.damage || '';
    document.getElementById('trapDamageType').value = t.damage_type || '';
    document.getElementById('trapArea').value = t.area || '';
    document.getElementById('trapTrigger').value = t.trigger || '';
    document.getElementById('trapDetectDc').value = t.detection_dc || '';
    document.getElementById('trapDetectSkill').value = t.detection_skill || 'Perception';
    document.getElementById('trapDetectDetail').value = t.detection_detail || '';
    document.getElementById('trapDisarmDc').value = t.disarm_dc || '';
    document.getElementById('trapDisarmMethod').value = t.disarm_method || '';
    document.getElementById('trapDisarmDetail').value = t.disarm_detail || '';
    document.getElementById('trapEffect').value = t.effect || '';
    document.getElementById('trapDesc').value = t.description || '';
    openModal('trapModal');
  } catch(e) { alert('Failed to load trap.'); }
}

async function deleteTrap(id) {
  if (!confirm('Delete this custom trap?')) return;
  try {
    const r = await fetch(`/api/dm/traps/${id}/delete`, { method: 'POST' });
    const d = await r.json();
    if (!d.ok) return alert(d.error || 'Failed');
    location.reload();
  } catch(e) { alert('Failed to delete trap.'); }
}

// ── AI Trap Generation ──
async function generateAiTrap() {
  const btn = document.getElementById('btnAiTrap');
  const status = document.getElementById('aiTrapStatus');
  btn.disabled = true;
  status.style.display = 'block';
  status.textContent = '🤖 Generating trap...';

  try {
    const data = {
      danger: document.getElementById('trapDanger').value,
      type: document.getElementById('trapType').value,
      theme: (document.getElementById('trapName').value || document.getElementById('trapDesc').value || '').trim(),
      location: document.getElementById('trapArea').value.trim(),
    };
    // Use party level info if available from combat campaign
    if (typeof _campaignPlayers !== 'undefined' && _campaignPlayers.length > 0) {
      const avgLevel = Math.round(_campaignPlayers.reduce((s, p) => s + (p.level || 1), 0) / _campaignPlayers.length);
      data.party_level = String(avgLevel);
    }

    const r = await fetch('/api/dm/ai/build-trap', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const t = await r.json();

    // Populate form fields with AI-generated content
    if (t.name) document.getElementById('trapName').value = t.name;
    if (t.trigger) document.getElementById('trapTrigger').value = t.trigger;
    if (t.detection_dc) document.getElementById('trapDetectDc').value = t.detection_dc;
    if (t.detection_skill) document.getElementById('trapDetectSkill').value = t.detection_skill;
    if (t.detection_detail) document.getElementById('trapDetectDetail').value = t.detection_detail;
    if (t.disarm_dc) document.getElementById('trapDisarmDc').value = t.disarm_dc;
    if (t.disarm_method) document.getElementById('trapDisarmMethod').value = t.disarm_method;
    if (t.disarm_detail) document.getElementById('trapDisarmDetail').value = t.disarm_detail;
    if (t.effect) document.getElementById('trapEffect').value = t.effect;
    if (t.save_dc) document.getElementById('trapSaveDc').value = t.save_dc;
    if (t.save_ability) document.getElementById('trapSaveAbility').value = t.save_ability;
    if (t.damage) document.getElementById('trapDamage').value = t.damage;
    if (t.damage_type) document.getElementById('trapDamageType').value = t.damage_type;
    if (t.area) document.getElementById('trapArea').value = t.area;
    if (t.description) document.getElementById('trapDesc').value = t.description;

    status.textContent = '✅ Trap generated! Review and Save.';
    status.style.color = 'var(--success)';
  } catch(e) {
    status.textContent = '❌ Generation failed. Try again or fill manually.';
    status.style.color = 'var(--danger)';
  }
  btn.disabled = false;
  setTimeout(() => { status.style.display = 'none'; status.style.color = 'var(--accent)'; }, 4000);
}

// ── Campaigns ──
function showCreateCampaign() {
  const name = prompt('Campaign name:');
  if (!name) return;
  fetch('/api/dm/campaign/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description: '', party_level: 1, party_size: 4})
  }).then(r => r.json()).then(d => {
    if (d.ok) location.reload();
  });
}

async function openCampaign(id) {
  openModal('campaignModal');
  document.getElementById('campaignDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';

  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const camp = (d.campaigns || []).find(c => c.id === id);
    if (!camp) throw 'Not found';

    const quests = typeof camp.quests === 'string' ? (JSON.parse(camp.quests) || []) : (camp.quests || []);
    const locations = typeof camp.locations === 'string' ? (JSON.parse(camp.locations) || []) : (camp.locations || []);
    const chars = typeof camp.characters === 'string' ? (JSON.parse(camp.characters) || []) : (camp.characters || []);

    // Compute live party stats from linked characters
    const liveSize = chars.length || camp.party_size;
    const liveLevel = chars.length ? Math.round(chars.reduce((s, c) => s + (c.level || 1), 0) / chars.length) : camp.party_level;

    // Fetch user's characters for the party picker
    const cr = await fetch('/api/dm/user-characters');
    const cd = await cr.json();
    const allChars = cd.characters || [];

    let html = `<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem">
      <div>
        <h2 style="margin:0">📜 ${camp.name}</h2>
        <p style="color:var(--text-muted);font-size:0.85rem">Party: L${liveLevel} · ${liveSize} players · ${chars.length} characters</p>
      </div>
      <div style="display:flex;gap:0.3rem">
        <button class="btn btn-outline btn-sm" onclick="editCampaignMeta(${camp.id})">Edit</button>
        <button class="btn btn-outline btn-sm" onclick="addCampQuest(${camp.id})">+ Quest</button>
        <button class="btn btn-outline btn-sm" onclick="addCampLocation(${camp.id})">+ Location</button>
      </div>
    </div>`;
    if (camp.description) html += `<p style="margin-bottom:1rem">${camp.description}</p>`;

    // ── Party Characters Section ──
    html += `<div style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <h4 style="margin:0">👥 Party Characters</h4>
        <div style="display:flex;gap:0.3rem">
          <select id="charPicker" style="padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
            <option value="">— Add character —</option>`;
    allChars.forEach(ch => {
      const inCamp = chars.some(c => c.id === ch.id);
      if (!inCamp) {
        html += `<option value="${ch.id}">${ch.name} (L${ch.level} ${ch.race} ${ch.class_name})</option>`;
      }
    });
    html += `</select>
          <button class="btn btn-primary btn-sm" onclick="addCharToCampaign(${camp.id})">+ Add</button>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:0.3rem">`;
    if (chars.length) {
      chars.forEach(ch => {
        const hpPct = ch.hp_max > 0 ? Math.round(ch.hp_current / ch.hp_max * 100) : 0;
        const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'danger' : hpPct < 50 ? 'warn' : 'ok';
        const mod = s => ch.modifiers && ch.modifiers[s] !== undefined ? (ch.modifiers[s] >= 0 ? '+' + ch.modifiers[s] : ch.modifiers[s]) : '?';
        const slotStr = ch.spell_slots && Object.entries(ch.spell_slots).filter(([k,v]) => v > 0).length
          ? ' · 🔮 ' + Object.entries(ch.spell_slots).filter(([k,v]) => v > 0).map(([k,v]) => `${k}:${v}`).join(' ')
          : '';
        html += `<div style="display:flex;flex-direction:column;padding:0.5rem 0.7rem;background:var(--bg);border:1px solid var(--border);border-radius:6px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.3rem">
            <span>
              <strong style="cursor:pointer" onclick="previewCharSheet(${ch.id}, '${ch.name}')" title="View character sheet">${ch.name}</strong>
              <span style="color:var(--text-muted);font-size:0.85rem">L${ch.level} ${ch.race}${ch.subrace ? ' (' + ch.subrace + ')' : ''} ${ch.class_name}${ch.subclass ? ' — ' + ch.subclass : ''}</span>
              <span class="badge badge-muted" style="font-size:0.65rem">${ch.status || 'active'}</span>
            </span>
            <button class="btn btn-outline btn-sm" onclick="previewCharSheet(${ch.id}, '${ch.name}')" title="View sheet">📋</button>
            <button class="btn btn-danger btn-sm" onclick="removeCharFromCampaign(${camp.id}, ${ch.id})">✕</button>
          </div>
          <div style="display:flex;gap:0.75rem;margin-top:0.3rem;flex-wrap:wrap;align-items:center;font-size:0.8rem">
            <span><strong>HP</strong> ${ch.hp_current}/${ch.hp_max}${ch.temp_hp > 0 ? ' (+' + ch.temp_hp + ' temp)' : ''}</span>
            <span><strong>AC</strong> ${ch.ac}</span>
            <span><strong>Speed</strong> ${ch.speed || 30}</span>
            <span><strong>PB</strong> +${ch.proficiency_bonus || 2}</span>
            <span><strong>Initiative</strong> ${mod('dexterity')}</span>
            ${ch.passive_perception ? `<span><strong>PP</strong> ${ch.passive_perception}</span>` : ''}
            ${ch.inspiration ? `<span class="badge badge-accent" style="font-size:0.65rem">✨ Inspiration</span>` : ''}
            ${ch.exhaustion > 0 ? `<span style="color:var(--warn);font-size:0.75rem">Exhaustion: ${ch.exhaustion}</span>` : ''}
          </div>
          <div style="display:flex;gap:0.3rem;margin-top:0.25rem;font-size:0.75rem">
            <span class="ability-tag">STR ${ch.strength} (${mod('strength')})</span>
            <span class="ability-tag">DEX ${ch.dexterity} (${mod('dexterity')})</span>
            <span class="ability-tag">CON ${ch.constitution} (${mod('constitution')})</span>
            <span class="ability-tag">INT ${ch.intelligence} (${mod('intelligence')})</span>
            <span class="ability-tag">WIS ${ch.wisdom} (${mod('wisdom')})</span>
            <span class="ability-tag">CHA ${ch.charisma} (${mod('charisma')})</span>
          </div>
          <div class="hp-bar-mini" style="width:100%;margin-top:0.25rem"><div class="hp-bar-mini-fill ${hpClass}" style="width:${Math.max(0, hpPct)}%"></div></div>
          ${slotStr ? `<div style="font-size:0.7rem;color:var(--accent);margin-top:0.15rem">🔮 Spell slots: ${slotStr}</div>` : ''}
        </div>`;
      });
    } else {
      html += `<p style="color:var(--text-muted);font-size:0.85rem">No characters in this campaign yet. Add from your character list above.</p>`;
    }
    html += `</div></div>`;

    // ── Campaign NPCs Section ──
    const npcs = typeof camp.npcs === 'string' ? (JSON.parse(camp.npcs) || []) : (camp.npcs || []);
    html += `<div style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <h4 style="margin:0">🧑 NPCs (${npcs.length})</h4>
        <button class="btn btn-primary btn-sm" onclick="showNpcPicker(${camp.id})">+ Add NPC</button>
      </div>
      <div id="npcPicker-${camp.id}" style="display:none;margin-bottom:0.5rem;max-height:300px;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:0.5rem">
        <div style="text-align:center;color:var(--text-muted);padding:1rem">Loading NPCs...</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:0.3rem">`;
    if (npcs.length) {
      npcs.forEach(n => {
        const isEnemy = n.is_enemy ? 'enemy' : 'npc';
        html += `<div style="display:flex;flex-direction:column;padding:0.5rem 0.7rem;background:var(--bg);border:1px solid var(--border);border-radius:6px" data-npc-id="${n.id}">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.3rem">
            <span>
              <strong>${n.name}</strong>
              <span class="npc-badge ${isEnemy}" style="font-size:0.65rem">${n.is_enemy ? 'Enemy' : 'NPC'}</span>
              <span style="color:var(--text-muted);font-size:0.85rem">${n.race}${n.class_name ? ' · L' + n.level + ' ' + n.class_name : ''}${n.subclass ? ' (' + n.subclass + ')' : ''}</span>
              ${n.role ? '<span class="badge badge-muted" style="font-size:0.65rem">' + n.role + '</span>' : ''}
              ${n.alignment ? '<span style="font-size:0.75rem;color:var(--text-muted)">' + n.alignment + '</span>' : ''}
            </span>
            <button class="btn btn-danger btn-sm" onclick="removeNpcFromCampaign(${camp.id}, ${n.id})">✕</button>
          </div>
          <div style="display:flex;gap:0.75rem;margin-top:0.3rem;font-size:0.8rem;flex-wrap:wrap">
            <span><strong>HP</strong> ${n.hp_current || 0}/${n.hp_max || 0}</span>
            <span><strong>AC</strong> ${n.ac || 10}</span>
            ${n.faction ? '<span><strong>Faction</strong> ' + n.faction + '</span>' : ''}
          </div>
          <div style="margin-top:0.3rem">
            <textarea id="npcNotes-${camp.id}-${n.id}" rows="2" style="width:100%;padding:0.3rem 0.5rem;background:var(--card-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.75rem;resize:vertical" placeholder="DM notes for this NPC…">${n.notes || ''}</textarea>
            <button class="btn btn-outline btn-sm" style="margin-top:0.2rem;font-size:0.7rem" onclick="saveNpcNotes(${camp.id}, ${n.id}, 'npcNotes-${camp.id}-${n.id}')">💾 Save Notes</button>
          </div>
        </div>`;
      });
    } else {
      html += `<p style="color:var(--text-muted);font-size:0.85rem">No NPCs linked yet. Use the NPCs tab to add them.</p>`;
    }
    html += `</div></div>`;

    if (camp.session_notes) {
      html += `<h4>Session Notes</h4>
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:0.75rem;margin-bottom:1rem;font-size:0.85rem;white-space:pre-wrap">${camp.session_notes}</div>`;
    }

    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">`;

    // Quests
    html += `<div><h4 style="margin-bottom:0.5rem">📋 Quests (${quests.length})</h4>`;
    if (quests.length) {
      quests.forEach((q, i) => {
        html += `<div class="camp-quest">
          <span>${q.name || 'Quest ' + (i+1)} ${q.status ? '<span class="badge badge-' + (q.status === 'completed' ? 'accent' : 'muted') + '">' + q.status + '</span>' : ''}</span>
          <button class="btn btn-danger btn-sm" onclick="removeCampQuest(${camp.id}, ${i})">✕</button>
        </div>`;
      });
    } else { html += '<p style="color:var(--text-muted);font-size:0.85rem">No quests yet</p>'; }
    html += `</div>`;

    // Locations
    html += `<div><h4 style="margin-bottom:0.5rem">📍 Locations (${locations.length})</h4>`;
    if (locations.length) {
      locations.forEach((l, i) => {
        html += `<div class="camp-quest">
          <span>${l.name || 'Location ' + (i+1)}${l.type ? ' <span class="badge badge-muted">' + l.type + '</span>' : ''}</span>
          <button class="btn btn-danger btn-sm" onclick="removeCampLocation(${camp.id}, ${i})">✕</button>
        </div>`;
      });
    } else { html += '<p style="color:var(--text-muted);font-size:0.85rem">No locations yet</p>'; }
    html += `</div></div>`;

    // Notes editor
    html += `<h4>Campaign Notes</h4>
      <textarea id="campNotes" rows="4" style="width:100%;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);margin-bottom:0.5rem">${camp.notes || ''}</textarea>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <button class="btn btn-outline btn-sm" onclick="saveCampNotes(${camp.id})">Save Notes</button>
        <button class="btn btn-danger btn-sm" onclick="deleteCampaign(${camp.id});closeModal('campaignModal')">Delete Campaign</button>
      </div>`;

    document.getElementById('campaignDetail').innerHTML = html;
  } catch(e) {
    document.getElementById('campaignDetail').innerHTML = '<p style="color:var(--danger)">Failed to load campaign.</p>';
  }
}

function addCharToCampaign(campId) {
  const sel = document.getElementById('charPicker');
  const charId = parseInt(sel.value);
  if (!charId) return;
  fetch(`/api/dm/campaign/${campId}/add-character`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({character_id: charId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function removeCharFromCampaign(campId, charId) {
  if (!confirm('Remove this character from the campaign?')) return;
  fetch(`/api/dm/campaign/${campId}/remove-character`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({character_id: charId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

// ── NPC ↔ Campaign functions ──
async function showNpcPicker(campId) {
  const picker = document.getElementById('npcPicker-' + campId);
  if (!picker) return;
  // Toggle
  if (picker.style.display === 'block') { picker.style.display = 'none'; return; }
  picker.style.display = 'block';
  picker.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:1rem">Loading NPCs...</div>';
  
  try {
    const r = await fetch('/api/dm/npcs');
    const d = await r.json();
    const allNpcs = d.npcs || [];
    // Filter out already-linked NPCs
    const linked = document.querySelectorAll('[data-npc-id]');
    const linkedIds = new Set();
    linked.forEach(el => linkedIds.add(parseInt(el.dataset.npcId)));
    const available = allNpcs.filter(n => !linkedIds.has(n.id));
    
    if (!available.length) {
      picker.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:1rem">All NPCs already linked</div>';
      return;
    }
    
    let html = '<div style="display:flex;flex-direction:column;gap:0.2rem">';
    available.forEach(n => {
      const isEnemy = n.is_enemy ? 'enemy' : 'npc';
      html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:0.3rem 0.5rem;background:var(--bg);border-radius:4px;font-size:0.8rem">
        <span>
          <strong>${n.name}</strong>
          <span class="npc-badge ${isEnemy}" style="font-size:0.6rem">${n.is_enemy ? 'Enemy' : 'NPC'}</span>
          <span style="color:var(--text-muted)">${n.race}${n.class_name ? ' L' + n.level + ' ' + n.class_name : ''}${n.role ? ' · ' + n.role : ''}</span>
        </span>
        <button class="btn btn-outline btn-sm" onclick="addNpcToCampaign(${n.id}, ${campId})">➕ Add</button>
      </div>`;
    });
    html += '</div>';
    picker.innerHTML = html;
  } catch(e) {
    picker.innerHTML = '<div style="color:var(--danger);padding:1rem">Failed to load NPCs</div>';
  }
}

function addNpcToCampaign(npcId, campId) {
  fetch(`/api/dm/campaign/${campId}/add-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(d => {
    if (d.ok) { openCampaign(campId); }
    else alert(d.error || 'Failed');
  });
}

function removeNpcFromCampaign(campId, npcId) {
  if (!confirm('Remove this NPC from the campaign?')) return;
  fetch(`/api/dm/campaign/${campId}/remove-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function saveNpcNotes(campId, npcId, elementId) {
  const notes = document.getElementById(elementId).value;
  fetch(`/api/dm/campaign/${campId}/update-npc-notes`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({npc_id: npcId, notes})
  }).then(r => r.json()).then(d => {
    if (d.ok) { /* silent save */ }
  });
}

function editCampaignMeta(id) {
  const name = prompt('Campaign name:');
  if (!name) return;
  const pl = prompt('Party level:', '1') || '1';
  const ps = prompt('Party size:', '4') || '4';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, party_level: parseInt(pl), party_size: parseInt(ps)})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function addCampQuest(id) {
  const qName = prompt('Quest name:');
  if (!qName) return;
  const status = prompt('Status (active/completed):', 'active') || 'active';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({addQuest: JSON.stringify({name: qName, status})})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function removeCampQuest(campId, idx) {
  if (!confirm('Remove quest?')) return;
  fetch(`/api/dm/campaign/${campId}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({removeQuest: idx})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function addCampLocation(id) {
  const lName = prompt('Location name:');
  if (!lName) return;
  const type = prompt('Type (city/dungeon/forest/etc):', '') || '';
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({addLocation: JSON.stringify({name: lName, type})})
  }).then(r => r.json()).then(() => openCampaign(id));
}

function removeCampLocation(campId, idx) {
  if (!confirm('Remove location?')) return;
  fetch(`/api/dm/campaign/${campId}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({removeLocation: idx})
  }).then(r => r.json()).then(() => openCampaign(campId));
}

function saveCampNotes(id, elementId) {
  const elId = elementId || 'campNotes';
  const notes = document.getElementById(elId).value;
  fetch(`/api/dm/campaign/${id}/update`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({notes})
  }).then(r => r.json()).then(() => { /* saved */ });
}

function deleteCampaign(id) {
  if (!confirm('Delete this campaign?')) return;
  fetch(`/api/dm/campaign/${id}/delete`, {method: 'POST'}).then(r => r.json()).then(() => location.reload());
}

// ── Items Panel ──
let _itemsDragItem = null;
let _itemsCampId = null;
let _itemsPartyChars = [];

async function loadItemsPanel() {
  const sel = document.getElementById('itemsCampaignSelect');
  _itemsCampId = sel.value ? parseInt(sel.value) : null;

  // Always populate campaign dropdown if empty
  if (sel.options.length <= 1) {
    try {
      const cr = await fetch('/api/dm/campaigns');
      const cd = await cr.json();
      (cd.campaigns || []).forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id; opt.textContent = c.name;
        sel.appendChild(opt);
      });
    } catch(e) {}
  }

  if (!_itemsCampId) {
    document.getElementById('itemsPanelContent').innerHTML =
      '<div style="color:var(--text-muted);padding:2rem;text-align:center;width:100%">Select a campaign to manage items</div>';
    return;
  }
  const content = document.getElementById('itemsPanelContent');
  content.innerHTML = '<div style="text-align:center;padding:2rem;width:100%">Loading...</div>';

  try {
    const cr = await fetch('/api/dm/campaigns');
    const cd = await cr.json();
    const camps = cd.campaigns || [];

    // Find selected campaign
    const camp = camps.find(c => c.id == _itemsCampId);
    if (!camp) { content.innerHTML = '<p>Campaign not found</p>'; return; }
    const chars = typeof camp.characters === 'string' ? JSON.parse(camp.characters) : (camp.characters || []);
    _itemsPartyChars = chars;

    // Fetch team items
    const tr = await fetch(`/api/campaign/${_itemsCampId}/team-items`);
    const td = await tr.json();
    const items = td.items || [];

    renderItemsPanel(items, chars);
  } catch (e) {
    content.innerHTML = '<p style="color:var(--danger)">Failed to load. Are you the DM?</p>';
  }
}

function renderItemsPanel(items, chars) {
  const content = document.getElementById('itemsPanelContent');

  // Team items pool (left)
  let poolHtml = '<div class="item-pool"><div class="card"><h3 style="margin:0 0 0.5rem 0">📦 Team Items Pool</h3>';
  if (items.length === 0) {
    poolHtml += '<div style="color:var(--text-muted);font-size:0.85rem;padding:0.5rem 0">No items in pool. Click +Add or drag items from characters below.</div>';
  } else {
    poolHtml += '<div style="display:flex;flex-wrap:wrap;gap:0.35rem;min-height:2rem">';
    items.forEach(item => {
      const qtyBadge = `<span class="item-qty" onclick="event.stopPropagation();dmToggleItemExpand(this.closest('.item-card').querySelector('.item-expand-btn'))" title="Click to expand" style="cursor:pointer">${item.qty}</span>`;
      const gpInfo = item.gp_value > 0 ? `<span style="font-size:0.55rem;color:#ffd700;margin-left:0.2rem">${item.gp_value}gp</span>` : '';
      const srcBadge = item.source ? ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${item.source.replace(/'/g, "\\'")}')" style="font-size:0.55rem;cursor:pointer;opacity:0.6" title="Click to open ${item.source}">📚</span>` : '';
      poolHtml += `<div class="item-card" draggable="true"
        data-item-name="${item.name.replace(/"/g, '&quot;')}" data-item-id="${item.id}"
        ondragstart="itemsDragStart(event, '${item.name.replace(/'/g, "\\'")}', 'pool', ${item.id})"
        ondragend="itemsDragEnd(event)">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();dmToggleItemExpand(this)" title="Expand">▶</button>
        <span class="drag-handle" title="Drag to award">⋮⋮</span>
        <span class="item-card-name" style="cursor:default">${item.name}</span>${srcBadge}${qtyBadge}${gpInfo}
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        <button class="item-card-btn item-card-award" onclick="event.stopPropagation();openAwardPopover(${item.id}, '${item.name.replace(/'/g, "\\'")}', ${item.qty})" title="Award to character">🎁</button>
        <button class="item-card-btn item-card-del" onclick="deleteTeamItem(${item.id})" title="Remove">✕</button>
        <div class="item-detail" data-item-name="${item.name.replace(/"/g, '&quot;')}"></div>
      </div>`;
    });
    poolHtml += '</div>';
  }
  poolHtml += '</div></div>';

  // Party members (right)
  let partyHtml = '<div class="party-drops"><div class="card"><h3 style="margin:0 0 0.5rem 0">👥 Party</h3>';
  if (chars.length === 0) {
    partyHtml += '<div style="color:var(--text-muted);font-size:0.85rem">No characters in party. Add them in the Campaign tab.</div>';
  } else {
    chars.forEach(ch => {
      partyHtml += `<div style="margin-bottom:0.5rem">
        <div class="party-member-label">
          <strong>${ch.name}</strong>
          <span style="color:var(--text-muted);font-size:0.75rem">L${ch.level} ${ch.class_name}</span>
        </div>
        <div class="party-drop-zone"
          ondragover="itemsDragOver(event)"
          ondragleave="itemsDragLeave(event)"
          ondrop="itemsDrop(event, ${ch.id}, '${ch.name.replace(/'/g, "\\'")}')"
          id="party-zone-${ch.id}">
          <span style="color:var(--text-muted);font-size:0.7rem">Drop items here to award</span>
        </div>
      </div>`;
    });
  }
  partyHtml += '</div></div>';

  content.innerHTML = `<div style="display:flex;gap:1rem;flex-wrap:wrap">${poolHtml}${partyHtml}</div>`;
  setTimeout(dmTagItemBadges, 50);

  // Initialize campaigns dropdown on first load
  if (!document.getElementById('itemsCampaignSelect').dataset.loaded) {
    document.getElementById('itemsCampaignSelect').dataset.loaded = '1';
    fetch('/api/dm/campaigns').then(r => r.json()).then(d => {
      const sel = document.getElementById('itemsCampaignSelect');
      (d.campaigns || []).forEach(c => {
        if (!sel.querySelector(`option[value="${c.id}"]`)) {
          const opt = document.createElement('option');
          opt.value = c.id; opt.textContent = c.name;
          sel.appendChild(opt);
        }
      });
    });
  }
}

// ── Tag team item cards with weapon/armor badges ──
function dmTagItemBadges() {
  const cards = document.querySelectorAll('#lootStagingItems .item-card, #itemsPanelContent .item-card');
  if (!cards.length) return;
  const wpnKw = ['sword','axe','hammer','bow','dagger','mace','spear','flail','rapier','scimitar','glaive','halberd','pike','lance','whip','club','staff','quarterstaff','crossbow','sling','dart','javelin','trident','war pick','morningstar','greatsword','longsword','shortsword','battleaxe','greataxe','warhammer','maul','handaxe','sickle','blowgun','net','pistol','musket','rifle'];
  const armKw = ['padded','leather','studded','hide','chain shirt','scale mail','breastplate','half plate','ring mail','chain mail','splint','plate','shield','armor'];
  cards.forEach(card => {
    const name = (card.querySelector('.item-card-name')?.textContent || '').toLowerCase().trim();
    if (!name) return;
    const wpnBadge = card.querySelector('.wpn-badge');
    const armBadge = card.querySelector('.arm-badge');
    // Check server-side classification first
    const classified = NAMED_ITEM_TYPES[name];
    if (classified === 'wpn') {
      if (wpnBadge) wpnBadge.style.display = 'inline-block';
      if (armBadge) armBadge.style.display = 'none';
      return;
    }
    if (classified === 'arm') {
      if (wpnBadge) wpnBadge.style.display = 'none';
      if (armBadge) armBadge.style.display = 'inline-block';
      return;
    }
    // Fall back to keyword matching
    if (wpnBadge) wpnBadge.style.display = wpnKw.some(kw => name.includes(kw)) ? 'inline-block' : 'none';
    if (armBadge) armBadge.style.display = armKw.some(kw => name.includes(kw)) ? 'inline-block' : 'none';
  });
}

// ── Items drag/drop ──
function itemsDragStart(e, name, source, itemId) {
  _itemsDragItem = { name, source, itemId };
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', name);
  e.target.closest('.item-card')?.classList.add('dragging');
}

function itemsDragEnd(e) {
  e.target.closest('.item-card')?.classList.remove('dragging');
  document.querySelectorAll('.drop-zone-active').forEach(el => el.classList.remove('drop-zone-active'));
  _itemsDragItem = null;
}

function itemsDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const zone = e.currentTarget;
  if (!zone.classList.contains('drop-zone-active')) zone.classList.add('drop-zone-active');
}

function itemsDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) {
    e.currentTarget.classList.remove('drop-zone-active');
  }
}

async function itemsDrop(e, charId, charName) {
  e.preventDefault();
  e.currentTarget.classList.remove('drop-zone-active');
  if (!_itemsDragItem || !_itemsCampId) return;

  const { name, source, itemId } = _itemsDragItem;
  if (source !== 'pool') return;

  // Confirm award
  if (!confirm(`Award "${name}" to ${charName}?`)) return;
  
  // Claim on behalf of character
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_id: charId })
  });
  const data = await resp.json();
  if (data.ok) {
    loadItemsPanel(); // Refresh
  } else {
    alert(data.error || 'Failed to award item');
  }
  _itemsDragItem = null;
}

// ── Add / delete team items ──
function addTeamItem() {
  if (!_itemsCampId) return alert('Select a campaign first');
  openItemPicker();
}

async function deleteTeamItem(itemId) {
  if (!confirm('Remove this item from the pool?')) return;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}`, { method: 'DELETE' });
  const data = await resp.json();
  if (data.ok) loadItemsPanel();
  else alert(data.error || 'Failed to remove item');
}

// ── Item Picker Popup ──
let _pickerTimer = null;
let _pickerSelectedName = null;

function openItemPicker() {
  const popup = document.getElementById('itemPickerPopup');
  if (!popup) return;
  document.getElementById('itemPickerSearch').value = '';
  document.getElementById('itemPickerForm').style.display = 'none';
  _pickerSelectedName = null;
  popup.style.display = 'flex';
  loadAllItems();
}

function closeItemPicker() {
  document.getElementById('itemPickerPopup').style.display = 'none';
}

async function loadAllItems() {
  const results = document.getElementById('itemPickerResults');
  results.innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">Loading...</div>';
  try {
    const r = await fetch('/api/items/search?limit=200');
    const d = await r.json();
    renderPickerResults(d.results || []);
  } catch(e) {
    results.innerHTML = '<div style="padding:1rem;color:var(--danger);text-align:center;font-size:0.85rem">Failed to load items</div>';
  }
}

function searchItemPicker(query) {
  clearTimeout(_pickerTimer);
  const q = query.trim();
  if (!q) { loadAllItems(); return; }
  _pickerTimer = setTimeout(async () => {
    const results = document.getElementById('itemPickerResults');
    try {
      const r = await fetch(`/api/items/search?q=${encodeURIComponent(q)}&limit=50`);
      const d = await r.json();
      renderPickerResults(d.results || []);
    } catch(e) {}
  }, 250);
}

function renderPickerResults(items) {
  const results = document.getElementById('itemPickerResults');
  // Apply core filter
  if (_coreOnlyDM) items = items.filter(it => isCoreSourceDM(it.source || ''));
  if (!items.length) {
    results.innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">No items found</div>';
    return;
  }
  results.innerHTML = items.map(item =>
    `<div onclick="selectPickerItem('${item.name.replace(/'/g, "\\'")}')" style="padding:0.35rem 0.6rem;cursor:pointer;border-bottom:1px solid var(--border);font-size:0.8rem;color:var(--text);display:flex;justify-content:space-between;align-items:center">
      <span>${item.name}${item.source ? ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${item.source.replace(/'/g, "\\'")}')" style="font-size:0.6rem;color:var(--text-muted);opacity:0.7;cursor:pointer" title="Click to open ${item.source}">📚 ${item.source}</span>` : ''}</span>
      <span style="color:var(--text-muted);font-size:0.7rem;flex-shrink:0;margin-left:0.5rem">${item.type}${item.rarity ? ' · '+item.rarity : ''}</span>
    </div>`
  ).join('');
}

function selectPickerItem(name) {
  _pickerSelectedName = name;
  document.getElementById('itemPickerSelected').textContent = name;
  document.getElementById('itemPickerForm').style.display = 'block';
  document.getElementById('itemPickerQty').value = '1';
  document.getElementById('itemPickerGp').value = '0';
}

async function confirmItemPicker() {
  if (!_pickerSelectedName) return;
  const qty = parseInt(document.getElementById('itemPickerQty').value) || 1;
  const gp = parseInt(document.getElementById('itemPickerGp').value) || 0;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: _pickerSelectedName, qty, gp_value: gp })
  });
  const data = await resp.json();
  if (data.ok) {
    closeItemPicker();
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to add item');
  }
}

// ── Award item to character (popover picker) ──
let _awardItemId = null, _awardItemName = null, _awardItemQty = null, _awardSelectedChar = null;

function openAwardPopover(itemId, itemName, itemQty) {
  if (!_itemsCampId) return;
  const chars = _itemsPartyChars;
  if (chars.length === 0) { alert('No characters in this campaign'); return; }
  if (chars.length === 1) {
    claimTeamItem(itemId, chars[0].id, itemName, itemQty);
    return;
  }
  _awardItemId = itemId;
  _awardItemName = itemName;
  _awardItemQty = itemQty;
  _awardSelectedChar = null;

  document.getElementById('awardPopoverTitle').textContent = `Award "${itemName}" to:`;
  const container = document.getElementById('awardPopoverChars');
  container.innerHTML = chars.map(c => `
    <div class="award-char-card" data-char-id="${c.id}" onclick="selectAwardChar(this, ${c.id})">
      <span style="font-size:1.1rem">👤</span>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:0.85rem;overflow-wrap:break-word;word-break:break-word">${c.name}</div>
        <div style="font-size:0.7rem;color:var(--text-muted)">L${c.level} ${c.class_name}${c.race ? ' · ' + c.race : ''}</div>
      </div>
    </div>
  `).join('');
  document.getElementById('awardConfirmBtn').disabled = true;
  document.getElementById('awardPopover').style.display = 'flex';
}

function selectAwardChar(el, charId) {
  document.querySelectorAll('#awardPopoverChars .award-char-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  _awardSelectedChar = charId;
  document.getElementById('awardConfirmBtn').disabled = false;
}

function closeAwardPopover() {
  document.getElementById('awardPopover').style.display = 'none';
  _awardSelectedChar = null;
}

async function confirmAwardItem() {
  if (!_awardSelectedChar || !_awardItemId) return;
  await claimTeamItem(_awardItemId, _awardSelectedChar, _awardItemName, _awardItemQty);
  closeAwardPopover();
}

// Close popover on backdrop click
document.getElementById('awardPopover').addEventListener('click', function(e) {
  if (e.target === this) closeAwardPopover();
});

async function claimTeamItem(itemId, charId, itemName, itemQty) {
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_id: charId })
  });
  const data = await resp.json();
  if (data.ok) {
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to award item');
  }
}

// ── Roll Loot ──
let _currentHoard = null; // {coins: [], gems: [], magic_items: [], total_gp_value: N}

async function rollLoot() {
  if (!_itemsCampId) return alert('Select a campaign first');
  const bracket = document.getElementById('lootCrBracket').value;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/roll-loot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cr_bracket: bracket })
  });
  const data = await resp.json();
  if (data.ok) {
    _currentHoard = data.hoard;
    renderLootStaging(data.hoard, bracket);
    document.getElementById('btnRollLoot').style.display = 'none';
    document.getElementById('btnRerollLoot').style.display = '';
  } else {
    alert(data.error || 'Failed to roll loot');
  }
}

async function rerollLoot() {
  if (!_itemsCampId) return alert('Select a campaign first');
  const bracket = document.getElementById('lootCrBracket').value;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/roll-loot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cr_bracket: bracket })
  });
  const data = await resp.json();
  if (data.ok) {
    _currentHoard = data.hoard;
    renderLootStaging(data.hoard, bracket);
  } else {
    alert(data.error || 'Failed to reroll loot');
  }
}

function renderLootStaging(hoard, bracket) {
  const staging = document.getElementById('lootStaging');
  const items = document.getElementById('lootStagingItems');
  const label = document.getElementById('lootStagingLabel');
  staging.style.display = '';
  label.textContent = `🎲 Loot Staging — CR ${bracket}`;

  let html = '';

  // Coins
  if (hoard.coins && hoard.coins.length) {
    hoard.coins.forEach((c, i) => {
      html += `<div class="item-card" style="border-color:#ffd700">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name">${c.label}</span>
        <span style="font-size:0.55rem;color:#ffd700;margin-left:0.2rem">≈${c.gp_value.toLocaleString()} gp</span>
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('coin', ${i})" title="Keep">📥</button>
        <div class="item-detail"><span class="detail-label">Value</span> ≈${c.gp_value.toLocaleString()} gp (${c.amount} ${c.type})</div>
      </div>`;
    });
  }

  // Gems
  if (hoard.gems && hoard.gems.length) {
    hoard.gems.forEach((g, i) => {
      html += `<div class="item-card" style="border-color:#00bcd4">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name">${g.label}</span>
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('gem', ${i})" title="Keep">📥</button>
        <div class="item-detail"><span class="detail-label">Value</span> ${g.total_value.toLocaleString()} gp (${g.count} × ${g.value_per} gp each)</div>
      </div>`;
    });
  }

  // Magic items
  if (hoard.magic_items && hoard.magic_items.length) {
    hoard.magic_items.forEach((m, i) => {
      const attune = /requires attunement/i.test(m.description || '') ? ' ◆ Attunement required' : '';
      const desc = (m.description || 'No description available.').replace(/'/g, "\\'");
      html += `<div class="item-card" style="border-color:#ce93d8">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name">${m.name}</span>
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        <span style="font-size:0.6rem;color:var(--text-muted);margin-left:0.2rem">${m.rarity}</span>
        ${m.source ? `<span class="src-badge" onclick="event.stopPropagation();openSourceRef('${m.source.replace(/'/g, "\\'")}')" style="font-size:0.55rem;color:var(--text-muted);opacity:0.6;margin-left:0.2rem;cursor:pointer" title="Click to open ${m.source}">📚</span>` : ''}
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('magic', ${i})" title="Keep">📥</button>
        <div class="item-detail">
          <span class="detail-label">Rarity</span> ${m.rarity}${attune ? '<br><span class="detail-label">Attunement</span>' + attune : ''}
          <br><span style="font-size:0.65rem;line-height:1.4;color:var(--text)">${m.description || 'No description available.'}</span>
        </div>
      </div>`;
    });
  }

  if (!html) {
    html = '<span style="color:var(--text-muted);font-size:0.85rem">No treasure found — the hoard was empty!</span>';
  }

  items.innerHTML = html;
  setTimeout(dmTagItemBadges, 50);
}

// ── Expand/collapse staging item cards ──
function toggleItemExpand(btn) {
  const card = btn.closest('.item-card');
  if (!card) return;
  card.classList.toggle('expanded');
}

// ── Expand/collapse team pool item cards (with fetch + qty) ──
const _dmItemDetailCache = {};

async function dmToggleItemExpand(btn) {
  const card = btn.closest('.item-card');
  if (!card) return;
  const isExpanded = card.classList.toggle('expanded');
  if (!isExpanded) return;

  const nameEl = card.querySelector('.item-card-name');
  const name = (nameEl?.value || nameEl?.textContent || '').trim();
  if (!name) return;

  const detail = card.querySelector('.item-detail');
  if (!detail) return;

  // Show qty row immediately from existing badge
  let currentQty = 1;
  const qtyBadge = card.querySelector('.item-qty');
  if (qtyBadge) currentQty = parseInt(qtyBadge.textContent) || 1;

  if (_dmItemDetailCache[name]) {
    detail.innerHTML = _dmItemDetailCache[name] + dmBuildQtyRow(currentQty);
    return;
  }

  // Fetch from API
  try {
    const resp = await fetch(`/api/items/describe?name=${encodeURIComponent(name)}`);
    const data = await resp.json();
    let descHtml = '';
    if (data.description) {
      descHtml = `<p style="margin:0;font-size:0.7rem;line-height:1.4;white-space:normal;overflow:visible">${data.description}</p>`;
      if (data.rarity) descHtml += `<br><span class="detail-label">Rarity</span> ${data.rarity}`;
      if (data.source) descHtml += ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${data.source.replace(/'/g, "\\'")}')" style="font-size:0.6rem;cursor:pointer;opacity:0.7" title="Click to open ${data.source}">📚 ${data.source}</span>`;
    } else {
      descHtml = `<span class="detail-label">No description available</span>`;
    }
    _dmItemDetailCache[name] = descHtml;
    detail.innerHTML = descHtml + dmBuildQtyRow(currentQty);
  } catch {
    detail.innerHTML = `<span class="detail-label">Description unavailable</span>` + dmBuildQtyRow(currentQty);
  }
}

function dmBuildQtyRow(qty) {
  return `<div style="margin-top:0.4rem;display:flex;align-items:center;gap:0.3rem">
    <span class="detail-label">Qty</span>
    <button class="qty-stepper" onclick="event.stopPropagation();dmStepQty(this, -1)">−</button>
    <input type="number" class="qty-detail-input" value="${qty}" min="1" max="999"
      style="width:4ch;padding:0.1rem 0.2rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.65rem;-moz-appearance:textfield;appearance:textfield;text-align:center"
      onchange="dmUpdateItemQty(this)" onclick="event.stopPropagation()">
    <button class="qty-stepper" onclick="event.stopPropagation();dmStepQty(this, 1)">+</button>
  </div>`;
}

function dmStepQty(btn, delta) {
  const input = btn.parentElement.querySelector('.qty-detail-input');
  if (!input) return;
  let val = parseInt(input.value) || 1;
  val = Math.max(1, Math.min(999, val + delta));
  input.value = val;
  dmUpdateItemQty(input);
}

async function dmUpdateItemQty(input) {
  const card = input.closest('.item-card');
  if (!card) return;
  const qty = Math.max(1, parseInt(input.value) || 1);
  input.value = qty;

  // Update badge
  let badge = card.querySelector('.item-qty');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'item-qty';
    badge.style.cursor = 'pointer';
    badge.title = 'Click to expand';
    badge.onclick = function(e) { e.stopPropagation(); dmToggleItemExpand(card.querySelector('.item-expand-btn')); };
    const btn = card.querySelector('.item-card-btn');
    card.insertBefore(badge, btn);
  }
  badge.textContent = qty;

  // Save to server
  const itemId = card.getAttribute('data-item-id');
  if (!itemId || !_itemsCampId) return;
  try {
    await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ qty })
    });
  } catch (e) { /* silently fail */ }
}

async function keepLootItem(type, index) {
  if (!_currentHoard) return;

  let name, gpValue = 0;

  if (type === 'coin') {
    const c = _currentHoard.coins[index];
    // Standardize currency to GP
    name = `${c.gp_value.toLocaleString()} gp`;
    gpValue = c.gp_value;
    // Remove from hoard
    _currentHoard.coins.splice(index, 1);
  } else if (type === 'gem') {
    const g = _currentHoard.gems[index];
    name = g.label;
    _currentHoard.gems.splice(index, 1);
  } else if (type === 'magic') {
    const m = _currentHoard.magic_items[index];
    name = m.name;
    _currentHoard.magic_items.splice(index, 1);
  }

  // Add to team pool via API
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, qty: 1, gp_value: gpValue })
  });
  const data = await resp.json();
  if (data.ok) {
    // Re-render staging and pool
    renderLootStaging(_currentHoard, document.getElementById('lootCrBracket').value);
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to keep item');
  }
}

async function keepAllLoot() {
  if (!_currentHoard) return;
  if (!confirm('Add ALL staged items to the team pool?')) return;

  const items = [];
  (_currentHoard.coins || []).forEach(c => items.push({ name: `${c.gp_value.toLocaleString()} gp`, gp_value: c.gp_value }));
  (_currentHoard.gems || []).forEach(g => items.push({ name: g.label, gp_value: 0 }));
  (_currentHoard.magic_items || []).forEach(m => items.push({ name: m.name, gp_value: 0 }));

  if (items.length === 0) return;

  let failed = 0;
  for (const item of items) {
    const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: item.name, qty: 1, gp_value: item.gp_value })
    });
    if (!(await resp.json()).ok) failed++;
  }

  // Clear hoard and re-render
  _currentHoard = { coins: [], gems: [], magic_items: [], total_gp_value: 0 };
  renderLootStaging(_currentHoard, document.getElementById('lootCrBracket').value);
  loadItemsPanel();

  if (failed) alert(`${failed} item(s) failed to add.`);
}

// ═══════════════════════════════════════════════════════════
// ⚔️ COMBAT TRACKER
// ═══════════════════════════════════════════════════════════
let _combatEncId = null;
let _combatCampId = null;
let _combatParticipants = [];  // {en_id, name, initiative, hp_current, hp_max, ac, defeated, is_player, ...}
let _combatCreatureCache = [];  // Combined NPC + monster cache for Quick Add search
let _combatRound = 1;
let _combatTurnIdx = 0;        // index into alive participants
let _combatOrder = [];         // en_id order array
let _savedBenchedEnIds = [];   // restored from combat_state on load

// ── Init: populate encounter dropdown when Combat tab opened ──
const combatTabObserver = new MutationObserver(() => {
  if (document.getElementById('panel-combat').classList.contains('active')) {
    initCombatPanel();
  }
});
document.querySelectorAll('.dm-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setTimeout(() => {
      if (document.getElementById('panel-combat').classList.contains('active')) {
        initCombatPanel();
      }
      if (document.getElementById('panel-items').classList.contains('active')) {
        loadItemsPanel();
      }
    }, 50);
  });
});

async function initCombatPanel() {
  const sel = document.getElementById('combatEncounterSelect');
  if (sel.dataset.loaded) return;
  sel.dataset.loaded = '1';
  try {
    const r = await fetch('/api/dm/encounters');
    const d = await r.json();
    (d.encounters || []).forEach(e => {
      const opt = document.createElement('option');
      opt.value = e.id; opt.textContent = e.name;
      sel.appendChild(opt);
    });
  } catch(e) { /* silent */ }

  // Also populate campaign dropdown
  const campSel = document.getElementById('combatCampaignSelect');
  if (campSel.dataset.loaded) return;
  campSel.dataset.loaded = '1';
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    (d.campaigns || []).forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id; opt.textContent = c.name;
      campSel.appendChild(opt);
    });
  } catch(e) {}
}

// ── Load encounter for combat ──
async function loadCombatEncounter() {
  const sel = document.getElementById('combatEncounterSelect');
  _combatEncId = sel.value ? parseInt(sel.value) : null;
  // Persist selection so page reload auto-restores
  if (_combatEncId) {
    try { localStorage.setItem('combatLastEncounterId', _combatEncId); } catch(e) {}
  }
  if (!_combatEncId && !_combatCampId) {
    document.getElementById('combatTracker').style.display = 'none';
    return;
  }

  document.getElementById('combatTracker').style.display = '';
  if (_combatEncId) {
    document.getElementById('btnRollInit').style.display = '';
    document.getElementById('btnRerollInit').style.display = 'none';
    document.getElementById('combatStatus').textContent = '';
  }

  // Load combat state first — needed for campaign link + player restore
  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}/combat-state`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'load'})
    });
    const d = await r.json();
    _combatRound = d.round || 1;
    _combatTurnIdx = d.turn_index || 0;
    _combatOrder = d.initiative_order || [];
    _savedBenchedEnIds = d.benched_en_ids || [];
    _savedPlayerParticipants = d.player_participants || [];
    _savedCampaignId = d.campaign_id || null;
  } catch(e) { _combatRound = 1; _combatTurnIdx = 0; _combatOrder = []; _savedBenchedEnIds = []; _savedPlayerParticipants = []; _savedCampaignId = null; }

  // Auto-load linked campaign (localStorage first, combat_state fallback)
  let linkedCampId = null;
  try { linkedCampId = localStorage.getItem('combatEncounterCampaign_' + _combatEncId); } catch(e) {}
  if (!linkedCampId && _savedCampaignId) {
    linkedCampId = _savedCampaignId;
    try { localStorage.setItem('combatEncounterCampaign_' + _combatEncId, _savedCampaignId); } catch(e) {}
  }
  if (linkedCampId) {
    const campSel = document.getElementById('combatCampaignSelect');
    const opt = campSel && campSel.querySelector('option[value="' + linkedCampId + '"]');
    if (opt) {
      campSel.value = linkedCampId;
      await loadCombatCampaign();
    }
  }

  // Build combined NPC + monster cache for Quick Add search
  _combatCreatureCache = [];
  try {
    const [nr, mr] = await Promise.all([
      fetch('/api/dm/npcs').then(r => r.json()),
      fetch('/api/dm/monsters').then(r => r.json())
    ]);
    // Monsters first (so they show up before NPCs in All filter)
    const _monsterTypes = new Set();
    (mr.monsters || []).forEach(m => {
      if (m.type) _monsterTypes.add(m.type);
      _combatCreatureCache.push({
        id: `m_${m.index || m.name}`, name: m.name,
        _kind: 'monster', _raw: m,
        _type: m.type || '',
        _cr: m.challenge_rating,
        race: m.type || '', hp_current: m.hit_points || 10,
        hp_max: m.hit_points || 10, ac: (m.armor_class && m.armor_class[0] ? m.armor_class[0].value : 10),
        level: m.challenge_rating || '?', is_enemy: 1,
        role: (m.size || '') + ' ' + (m.type || ''),
        xp_reward: m.xp || 0
      });
    });
    // Populate type dropdown from monster types
    const typeSel = document.getElementById('combatCreatureType');
    if (typeSel) {
      // Keep only the "All Types" default
      while (typeSel.options.length > 1) typeSel.remove(1);
      // Get existing CR sort order
      const crSel = document.getElementById('combatCreatureCr');
      const wasCrVisible = crSel && crSel.style.display !== 'none';
      // Add types in alphabetical order
      [..._monsterTypes].sort().forEach(t => {
        const opt = document.createElement('option');
        opt.value = t;
        opt.textContent = t.charAt(0).toUpperCase() + t.slice(1);
        typeSel.appendChild(opt);
      });
      // Show type/CR filters when monsters are loaded
      typeSel.style.display = '';
      if (crSel) crSel.style.display = '';
    }
    (nr.npcs || []).forEach(n => {
      _combatCreatureCache.push({...n, _kind: 'npc'});
    });
  } catch(e) {}
  toggleCombatFilters();
  filterCombatCreatures();

  // Load participants from encounter
  await refreshCombatParticipants();

  // Restore player characters from saved state
  for (const pp of _savedPlayerParticipants) {
    if (!_combatParticipants.find(p => p.en_id === pp.en_id)) {
      _combatParticipants.push({...pp, is_player: true, is_enemy: 0, role: '', roll: pp.initiative});
    }
  }
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  renderInitiativeTrack();
  renderPlayersPanel();  // hide players already in initiative from waiting area

  // Restore button state: show Reroll if combat already started
  if (_combatParticipants.length > 0 && _combatParticipants.some(p => (p.initiative || 0) > 0)) {
    document.getElementById('btnRollInit').style.display = 'none';
    document.getElementById('btnRerollInit').style.display = '';
    document.getElementById('combatStatus').textContent =
      `🎲 ${_combatParticipants.length} in initiative`;
  }

  // Restore benched state: move benched participants out of the track
  if (_savedBenchedEnIds.length > 0) {
    const benchedSet = new Set(_savedBenchedEnIds);
    _benchedNpcs = _combatParticipants.filter(p => benchedSet.has(p.en_id));
    _combatParticipants = _combatParticipants.filter(p => !benchedSet.has(p.en_id));
    _combatOrder = _combatOrder.filter(id => !benchedSet.has(id));
    renderInitiativeTrack();
    renderPlayersPanel();
    renderBenchedNpcs();
  }
  // Save full state now that players are restored and everything is in place
  saveCombatState();
  startCombatPolling();
}

// ── Load campaign player characters ──
let _campaignPlayers = []; // {char_id, name, race, class_name, level, ac, hp_current, hp_max, dex_mod, initiative}
/* SUMMON_TEMPLATES set by template */
let _benchedNpcs = [];     // NPCs dragged out of combat — can be dragged back in

async function loadCombatCampaign() {
  const campSel = document.getElementById('combatCampaignSelect');
  _combatCampId = campSel.value ? parseInt(campSel.value) : null;

  if (!_combatCampId) {
    document.getElementById('playersWaiting').innerHTML =
      '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">Select a campaign to load players</span>';
    _campaignPlayers = [];
    return;
  }

  // Fetch campaign characters
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const camp = (d.campaigns || []).find(c => c.id === _combatCampId);
    _campaignPlayers = [];
    if (camp && camp.characters) {
      for (const ch of camp.characters) {
        _campaignPlayers.push({
          char_id: ch.id,
          name: ch.name,
          race: ch.race || '',
          class_name: ch.class_name || '',
          level: ch.level || 1,
          ac: ch.ac || 10,
          hp_current: ch.hp_current || 1,
          hp_max: ch.hp_max || 1,
          dex_mod: ch.modifiers?.dexterity || 0,
          initiative: 0,
          defeated: 0
        });
      }
    }
  } catch(e) {}

  renderPlayersPanel();
  // Persist encounter→campaign link so encounter auto-loads this campaign
  if (_combatEncId && _combatCampId) {
    try { localStorage.setItem('combatEncounterCampaign_' + _combatEncId, _combatCampId); } catch(e) {}
  }
  // Don't saveCombatState here — player_participants haven't been restored yet.
  // loadCombatEncounter saves the full state at the end after all data is in place.
}

function renderPlayersPanel() {
  const panel = document.getElementById('playersWaiting');
  if (_campaignPlayers.length === 0) {
    panel.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">No players in campaign</span>';
    return;
  }

  // Don't show players already in the initiative track
  const inTrackIds = new Set(_combatParticipants.filter(p => p.is_player).map(p => p.char_id));

  const available = _campaignPlayers.filter(p => !inTrackIds.has(p.char_id));

  if (available.length === 0) {
    panel.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center">All players in initiative ✓</span>';
    return;
  }

  panel.innerHTML = available.map(p => {
    return `<div class="initiative-card player-waiting" draggable="true"
      data-char-id="${p.char_id}"
      ondragstart="playerDragStart(event, ${p.char_id})"
      ondragend="playerDragEnd(event)"
      style="cursor:grab;border-style:dashed;flex-direction:column;align-items:stretch;gap:0.4rem">
      <div style="display:flex;align-items:center;gap:0.4rem">
        <div class="init-turn-marker" style="background:var(--accent2);color:var(--text);flex-shrink:0">👤</div>
        <div class="init-info" style="flex:1;min-width:0">
          <div class="init-name">${p.name} <span class="badge" style="background:var(--accent2);color:var(--text);font-size:0.6rem;flex-shrink:0">PC</span></div>
          <div class="init-meta">L${p.level} ${p.class_name} · AC ${p.ac}</div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
        <span style="font-size:0.65rem;color:var(--text-muted);flex-shrink:0">Init</span>
        <input type="number" class="init-hp-input" value="${p.initiative || ''}" placeholder="?"
          style="width:36px"
          onchange="playerSetInit(${p.char_id}, this.value)" onclick="event.stopPropagation()">
        <div class="init-hp-group" style="min-width:80px">
          <span style="font-size:0.7rem">${p.hp_current}/${p.hp_max}</span>
          <div class="init-hp-bar"><div class="init-hp-bar-fill ok" style="width:${Math.round(p.hp_current/p.hp_max*100)}%"></div></div>
        </div>
        <div style="flex:1"></div>
        <button class="init-btn" onclick="addPlayerToInitiative(${p.char_id})" title="Add to initiative" style="flex-shrink:0;color:var(--accent)">➕</button>
        <button class="init-btn" onclick="previewCharSheet(${p.char_id}, '${p.name}')" title="View sheet" style="flex-shrink:0">📋</button>
        <button class="init-btn" onclick="loadCharacterSummons(${p.char_id}, '${p.name}')" title="Load summons into combat" style="flex-shrink:0;color:var(--warn)">🐾</button>
        <button class="init-btn" onclick="openSummonModalDM(${p.char_id}, '${p.name}', ${p.level || 1})" title="Quick add summon mid-combat" style="flex-shrink:0;color:var(--accent)">⚡</button>
      </div>
    </div>`;
  }).join('');
}

// ── Player drag from panel into initiative track ──
let _playerDragCharId = null;

function playerSetInit(charId, val) {
  const p = _campaignPlayers.find(p => p.char_id === charId);
  if (p) p.initiative = parseInt(val) || 0;
}

function playerDragStart(e, charId) {
  _playerDragCharId = charId;
  e.dataTransfer.effectAllowed = 'move';
  e.target.closest('.initiative-card')?.classList.add('dragging');
}

function playerDragEnd(e) {
  document.querySelectorAll('.initiative-card.dragging').forEach(c => c.classList.remove('dragging'));
  _playerDragCharId = null;
}

// Mobile-friendly button fallback — same logic as the drop handler
function addPlayerToInitiative(charId) {
  const cp = _campaignPlayers.find(p => p.char_id === charId);
  if (!cp) return;
  const init = cp.initiative || 0;
  if (!init && !confirm(`${cp.name} has no initiative set. Add with initiative 0?`)) return;
  _combatParticipants.push({
    en_id: -cp.char_id, char_id: cp.char_id, name: cp.name,
    race: cp.race, class_name: cp.class_name, level: cp.level,
    is_enemy: 0, is_player: true, role: '', ac: cp.ac,
    hp_current: cp.hp_current, hp_max: cp.hp_max,
    defeated: cp.defeated || 0, initiative: init, roll: init, dex_mod: cp.dex_mod || 0
  });
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  renderPlayersPanel();
  saveCombatState();
}

// Make initiative track a drop target for player cards
const initTrackEl = document.getElementById('initiativeTrack');
initTrackEl.addEventListener('dragover', e => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
});
initTrackEl.addEventListener('drop', async e => {
  e.preventDefault();

  // ── Benched NPC dragged back into initiative ──
  if (_benchedDragEnId) {
    const bp = _benchedNpcs.find(p => p.en_id === _benchedDragEnId);
    if (!bp) { _benchedDragEnId = null; return; }
    _benchedNpcs = _benchedNpcs.filter(p => p.en_id !== _benchedDragEnId);
    _combatParticipants.push(bp);
    _combatParticipants.sort((a, b) => {
      if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
      return (b.initiative||0) - (a.initiative||0);
    });
    _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
    renderInitiativeTrack();
    renderBenchedNpcs();
    saveCombatState();
    _benchedDragEnId = null;
    return;
  }

  // ── Player dragged from panel into initiative ──
  if (!_playerDragCharId) return;
  const cp = _campaignPlayers.find(p => p.char_id === _playerDragCharId);
  if (!cp) return;

  const init = cp.initiative || 0;
  if (!init && !confirm(`${cp.name} has no initiative set. Add with initiative 0?`)) return;

  // Add to track participants
  _combatParticipants.push({
    en_id: -cp.char_id,  // negative to avoid collision
    char_id: cp.char_id,
    name: cp.name,
    race: cp.race,
    class_name: cp.class_name,
    level: cp.level,
    is_enemy: 0,
    is_player: true,
    role: '',
    ac: cp.ac,
    hp_current: cp.hp_current,
    hp_max: cp.hp_max,
    defeated: cp.defeated || 0,
    initiative: init,
    roll: init,
    dex_mod: cp.dex_mod || 0
  });

  // Re-sort
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });

  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  renderPlayersPanel();
  saveCombatState();
  _playerDragCharId = null;
});

// ── Roll initiative ──
async function rollInitiative() {
  if (!_combatEncId) return alert('Select an encounter first');

  // Preserve benched en_ids so they stay benched after reroll
  const benchedIds = new Set(_benchedNpcs.map(p => p.en_id));

  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}/roll-initiative`, {method:'POST'});
    const d = await r.json();
    if (!d.ok) return alert(d.error || 'Failed to roll');
    // Keep player characters that were already dragged into the track
    const inTrack = _combatParticipants.filter(p => p.is_player);
    // Preserve benched en_ids so they stay benched after reroll
    _combatParticipants = [];
    for (const npc of d.participants) {
      _combatParticipants.push({...npc, is_player: false, char_id: null});
    }
    // Re-add players that were already in the track
    for (const p of inTrack) {
      _combatParticipants.push(p);
    }
  } catch(e) { alert('Failed to roll NPC initiative'); return; }

  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });

  // Restore benched state: move benched NPCs out of combat participants
  if (benchedIds.size > 0) {
    _benchedNpcs = _combatParticipants.filter(p => benchedIds.has(p.en_id));
    _combatParticipants = _combatParticipants.filter(p => !benchedIds.has(p.en_id));
  }

  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  _combatRound = 1;
  _combatTurnIdx = 0;
  renderInitiativeTrack();
  saveCombatState();
  document.getElementById('btnRollInit').style.display = 'none';
  document.getElementById('btnRerollInit').style.display = '';
  document.getElementById('combatStatus').textContent =
    `🎲 ${_combatParticipants.length} in initiative`;
}

// ── Edit initiative value directly on a card ──
function editInitiative(input) {
  const enId = parseInt(input.dataset.enId);
  const newInit = parseInt(input.value) || 0;
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.initiative = newInit;
  // Re-sort and re-render
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  saveCombatState();
  // Persist to backend
  if (_combatEncId) {
    fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({participants: [{id: enId, initiative: newInit}]})
    }).catch(() => {});
  }
}

// ── Refresh participants from DB (used after HP changes, adding NPCs, etc.) ──
async function refreshCombatParticipants() {
  if (!_combatEncId) return;
  // Preserve player characters already in the track
  const players = _combatParticipants.filter(p => p.is_player);

  try {
    const r = await fetch(`/api/dm/encounter/${_combatEncId}`);
    const d = await r.json();
    const npcs = (d.encounter.participants || []).map(p => ({
      en_id: p.id, npc_id: p.npc_id, name: p.npc_name,
      race: p.race || '', class_name: p.class_name || '',
      level: p.level || 1, is_enemy: p.is_enemy, role: p.role || '',
      ac: p.ac || p.npc_ac || 10, hp_current: p.hp_current,
      hp_max: p.hp_max || p.npc_hp_max || 10,
      defeated: p.defeated || 0, initiative: p.initiative || 0,
      is_player: false, char_id: null,
      creature_data: p.creature_data || null
    }));
    _combatParticipants = [...players, ...npcs];
  } catch(e) {}

  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  if (_combatOrder.length === 0) {
    _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  }
  renderInitiativeTrack();
}

// ── Render initiative track ──
function renderInitiativeTrack() {
  const track = document.getElementById('initiativeTrack');
  const all = [..._combatParticipants];  // include defeated for band grouping
  const alive = all.filter(p => !p.defeated);

  // Clamp turn index
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = alive.length - 1;
  if (_combatTurnIdx < 0) _combatTurnIdx = 0;

  document.getElementById('combatRoundLabel').textContent = `Round ${_combatRound}`;
  document.getElementById('combatTurnLabel').textContent =
    alive.length > 0 ? `Turn ${_combatTurnIdx + 1}/${alive.length}` : 'No active combatants';

  if (all.length === 0) {
    track.innerHTML = '<div style="color:var(--text-muted);padding:1rem;text-align:center">No participants. Roll initiative or add NPCs.</div>';
    return;
  }

  // Build in initiative_order sequence (if set), then by initiative descending
  const ordered = [];
  const used = new Set();
  for (const enId of _combatOrder) {
    const p = all.find(p => p.en_id === enId);
    if (p && !used.has(p.en_id)) { ordered.push(p); used.add(p.en_id); }
  }
  for (const p of all) {
    if (!used.has(p.en_id)) ordered.push(p);
  }

  // Five initiative bands
  const bands = [
    { label: '20+', min: 20, max: 99, members: [] },
    { label: '19–15', min: 15, max: 19, members: [] },
    { label: '14–10', min: 10, max: 14, members: [] },
    { label: '9–5', min: 5, max: 9, members: [] },
    { label: '4–0', min: -99, max: 4, members: [] },
  ];
  for (const p of ordered) {
    const init = p.initiative || 0;
    for (const band of bands) {
      if (init >= band.min && init <= band.max) {
        band.members.push(p);
        break;
      }
    }
  }

  const currentEnId = alive.length > 0 && _combatTurnIdx < alive.length
    ? alive[_combatTurnIdx].en_id : null;

  function cardHTML(p, i) {
    const isDefeated = p.defeated || p.hp_current <= 0;
    const isCurrent = p.en_id === currentEnId;
    const hpPct = p.hp_max > 0 ? Math.max(0, Math.round(p.hp_current / p.hp_max * 100)) : 0;
    const hpClass = hpPct <= 0 ? 'danger' : hpPct < 25 ? 'warn' : hpPct < 50 ? 'warn' : 'ok';
    const badge = p.is_player ? '<span class="badge" style="background:var(--accent2);color:var(--text);font-size:0.6rem">PC</span>'
      : p.is_summon ? '<span class="badge" style="background:rgba(255,165,0,0.15);color:#ffa500;font-size:0.6rem">🐾</span>'
      : p.is_enemy ? '<span class="badge badge-accent" style="font-size:0.6rem">ENEMY</span>'
      : '<span class="badge badge-muted" style="font-size:0.6rem">ALLY</span>';
    const role = p.role ? ` · ${p.role}` : '';
    const cls = p.class_name ? `L${p.level} ${p.class_name}${role}` : `L${p.level}${role}`;
    const rollInfo = p.roll != null ? ` <span style="font-size:0.55rem;opacity:0.6">(${p.roll}${p.dex_mod >= 0 ? '+' : ''}${p.dex_mod || 0})</span>` : '';

    return `<div class="initiative-card${isCurrent ? ' current-turn' : ''}${isDefeated ? ' defeated-card' : ''}"
      draggable="true"
      data-en-id="${p.en_id}"
      ondragstart="initDragStart(event, ${p.en_id})"
      ondragend="initDragEnd(event)"
      ondragover="initDragOver(event)"
      ondrop="initDrop(event, ${p.en_id})">
      <div class="init-turn-marker">${isCurrent ? '▶' : ''}</div>
      <div class="init-roll" title="d20${p.dex_mod >= 0 ? '+' + p.dex_mod : p.dex_mod} (roll: ${p.roll || '—'}${p.dex_mod >= 0 ? '+' : ''}${p.dex_mod || 0})">
        <input type="number" class="init-roll-input" value="${p.initiative}"
          data-en-id="${p.en_id}"
          onchange="editInitiative(this)" onfocus="this.select()"
          style="width:2.5rem;padding:0.1rem 0.2rem;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);font-size:0.75rem;text-align:center;font-family:monospace">
      </div>
      <div class="init-info">
        <div class="init-name">${p.name} ${badge} <button class="init-btn" onclick="event.stopPropagation();${p.is_player || p.char_id ? `previewCharSheet(${p.char_id || p.en_id}, '${p.name}')` : `showCombatantDetails(${p.en_id})`}" title="View details" style="font-size:0.65rem;padding:0 0.2rem">📋</button></div>
        <div class="init-meta">${cls} · AC ${p.ac}</div>
      </div>
      <button class="init-btn" onclick="toggleDefeatedCombat(${p.en_id})" title="Toggle defeated" style="flex-shrink:0">${isDefeated ? '⬆' : '💀'}</button>
      <div class="init-hp-group">
        <span style="font-size:0.85rem;font-weight:600;min-width:2.5em;text-align:right">${p.hp_current}</span>
        <span style="font-size:0.7rem;color:var(--text-muted)">/${p.hp_max}</span>
        <div class="init-hp-bar"><div class="init-hp-bar-fill ${hpClass}" style="width:${hpPct}%"></div></div>
      </div>
      <div class="init-cond-badges" style="display:flex;flex-wrap:wrap;gap:0.15rem;margin-top:0.15rem;align-items:center">
        ${(() => {
          const conds = p.conditions || [];
          if (_combatCondExpanded.has(p.en_id)) {
            return conds.map(c => {
              const style = CONDITION_COLORS[c.name] || '';
              const bg = style ? style.split(';')[0] : 'var(--accent)';
              const fg = style ? (style.split(';')[1]||'color:#fff') : 'color:#fff';
              return `<span class="cond-badge" style="background:${bg};${fg}" onclick="event.stopPropagation();combatToggleCond(event,${p.en_id},'${c.name}','${(c.description||'').replace(/'/g,"\\'")}')">${c.name}<span class="dismiss" onclick="event.stopPropagation();combatDismissCond(${p.en_id},'${c.name}')">✕</span></span>`;
            }).join('');
          }
          return conds.length ? `<span class="cond-badge" style="background:var(--bg);color:var(--text-muted);border:1px dashed var(--border);cursor:pointer" onclick="event.stopPropagation();combatToggleCondExpand(${p.en_id})">${conds.length} condition${conds.length!==1?'s':''}</span>` : '';
        })()}
        <button onclick="event.stopPropagation();combatOpenCondPicker(${p.en_id})" title="Add condition" style="font-size:0.5rem;padding:0 0.25rem;line-height:1.4;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);cursor:pointer;min-width:16px">+</button>
      </div>
      <div class="init-actions">
        <input type="number" class="init-hp-input" id="dmg-input-${p.en_id}" value="" placeholder="dmg"
          style="width:44px"
          onkeydown="if(event.key==='Enter'){event.preventDefault();applyDmgInput(${p.en_id})}"
          onclick="event.stopPropagation()" title="Enter = damage, + = heal">
        <button class="init-btn heal-btn" onclick="applyDmgButton(${p.en_id}, 1)" title="Heal">+</button>
        <button class="init-btn dmg-btn" onclick="applyDmgButton(${p.en_id}, -1)" title="Damage">−</button>
        <button class="init-btn" onclick="combatResetHP(${p.en_id})" title="Reset to full HP">↺</button>
        <button class="init-btn" onclick="benchCombatant(${p.en_id})" title="Bench (remove from combat)" style="color:var(--text-muted)">⏸</button>
      </div>
    </div>`;
  }

  track.innerHTML = bands.map(band => `
    <div class="init-band">
      <div class="init-band-header" onclick="this.parentElement.classList.toggle('collapsed')">
        <span class="band-arrow">▼</span>
        <span class="band-range">${band.label}</span>
        <span class="band-count">${band.members.length}</span>
      </div>
      <div class="init-band-body">
        ${band.members.map((p, i) => cardHTML(p, i)).join('')}
      </div>
    </div>
  `).join('');
}

// ── Turn navigation ──
function nextTurn() {
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (alive.length === 0) return;
  _combatTurnIdx++;
  if (_combatTurnIdx >= alive.length) {
    _combatTurnIdx = 0;
    _combatRound++;
  }
  renderInitiativeTrack();
  saveCombatState();
}

function prevTurn() {
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (alive.length === 0) return;
  _combatTurnIdx--;
  if (_combatTurnIdx < 0) {
    if (_combatRound > 1) {
      _combatRound--;
      _combatTurnIdx = alive.length - 1;
    } else {
      _combatTurnIdx = 0;
    }
  }
  renderInitiativeTrack();
  saveCombatState();
}

// ── HP management ──
async function adjustHp(enId, newHp) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  const hp = Math.max(0, parseInt(newHp) || 0);
  p.hp_current = hp;
  if (hp <= 0) p.defeated = 1;

  await saveCombatHp(p, hp);
  renderInitiativeTrack();
  saveCombatState();
}

async function applyDmgInput(enId) {
  const input = document.getElementById('dmg-input-' + enId);
  if (!input) return;
  const raw = parseInt(input.value);
  if (isNaN(raw) || raw <= 0) { input.value = ''; return; }
  await _applyHpDelta(enId, -raw);
  input.value = '';
}

async function applyDmgButton(enId, sign) {
  const input = document.getElementById('dmg-input-' + enId);
  const raw = input ? (parseInt(input.value) || 1) : 1;
  const delta = sign === -1 ? -raw : raw;
  await _applyHpDelta(enId, delta);
  if (input) input.value = '';
}

async function _applyHpDelta(enId, delta) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p || p.defeated) return;
  const hp = Math.max(0, p.hp_current + delta);
  p.hp_current = hp;
  if (hp <= 0) p.defeated = 1;

  await saveCombatHp(p, hp);
  renderInitiativeTrack();
  saveCombatState();
}

async function combatResetHP(enId) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.hp_current = p.hp_max;
  p.defeated = 0;

  await saveCombatHp(p, p.hp_max);
  // Rebuild order (might have been defeated)
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  saveCombatState();
}

async function saveCombatHp(p, hp) {
  if (p.is_player && p.char_id) {
    // Save to character table
    try {
      await fetch('/api/update', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({character_id: p.char_id, hp_current: hp})
      });
    } catch(e) {}
  } else if (p.is_summon && p.owner_char_id && p.summon_idx !== undefined) {
    // Save summon HP back to character sheet
    try {
      await fetch(`/api/character/${p.owner_char_id}/update-summon-hp`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({summon_idx: p.summon_idx, hp_current: hp})
      });
    } catch(e) {}
  } else if (p.en_id > 0 && _combatEncId) {
    // Save to encounter NPC table
    try {
      await fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({single:{id:p.en_id, hp_current:hp, defeated: p.defeated?1:0}})
      });
    } catch(e) {}
  }
}

// ── Defeated toggle ──
async function toggleDefeatedCombat(enId) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  p.defeated = p.defeated ? 0 : 1;

  if (p.is_player && p.char_id) {
    // Just track locally for players — no server-side defeated state on character table
  } else if (p.en_id > 0 && _combatEncId) {
    try {
      await fetch(`/api/dm/encounter/${_combatEncId}/update-initiative`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({single:{id:p.en_id, defeated: p.defeated}})
      });
    } catch(e) {}
  }

  // Rebuild order excluding defeated
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  const alive = _combatParticipants.filter(p => !p.defeated);
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = Math.max(0, alive.length - 1);
  renderInitiativeTrack();
  saveCombatState();
}

// ── Load character summons into combat ──
async function loadCharacterSummons(charId, charName) {
  try {
    const r = await fetch(`/api/character/${charId}/summons`);
    const data = await r.json();
    if (!data.summons || !data.summons.length) {
      alert(`${charName} has no active summons.`);
      return;
    }
    let added = 0;
    for (let i = 0; i < data.summons.length; i++) {
      const s = data.summons[i];
      const enId = Date.now() + Math.random();
      _combatParticipants.push({
        en_id: enId,
        name: s.name + (s.form ? ` (${s.form})` : ''),
        race: s.size || 'Medium',
        class_name: s.source || 'Summon',
        level: 0,
        is_enemy: 0,
        is_player: false,
        is_summon: true,
        owner_char_id: charId,
        owner_name: charName,
        summon_idx: i,
        ac: s.ac || 10,
        hp_current: s.hp_current || s.hp_max || 1,
        hp_max: s.hp_max || 1,
        initiative: Math.floor(Math.random() * 20) + 1 + Math.floor(((s.stats?.dex || 10) - 10) / 2),
        roll: 0,
        dex_mod: Math.floor(((s.stats?.dex || 10) - 10) / 2),
        defeated: 0,
      });
      added++;
    }
    _combatParticipants.sort((a, b) => {
      if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
      return (b.initiative||0) - (a.initiative||0);
    });
    _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
    renderInitiativeTrack();
    saveCombatState();
  } catch(e) {
      console.error('loadSummons:', e);
      alert('Failed to load summons.');
    }
  }

  // ── Quick Add Summon from Combat Tab ──
  let _summonDMCharId = null;
  let _summonDMCharName = null;
  let _summonDMLevel = 1;
  let _lastMonsterDataDM = null;
  let _lastTemplateKeyDM = null;

  function openSummonModalDM(charId, charName, charLevel) {
    _summonDMCharId = charId;
    _summonDMCharName = charName;
    _summonDMLevel = charLevel || 1;

    let html = `<div class="modal-overlay" id="summonModalDM" onclick="if(event.target===this)closeSummonModalDM()">
      <div class="modal-content" style="max-width:600px">
        <button class="modal-close" onclick="closeSummonModalDM()">✕</button>
        <h2 style="margin:0 0 0.5rem 0">⚡ Summon for ${charName} (Lv${_summonDMLevel})</h2>
        <p style="font-size:0.7rem;color:var(--text-muted);margin-bottom:0.75rem">Adds to sheet + auto-inserts into initiative</p>
        <div style="margin-bottom:0.75rem">
          <label style="font-size:0.75rem;color:var(--text-muted);display:block;margin-bottom:0.25rem">Quick Template</label>
          <select id="summon-template-dm" onchange="fillSummonTemplateDM(this.value)" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
            <option value="">— Custom —</option>`;

    const categories = {
      'pact_chain': '🔗 Pact of the Chain',
      'familiar': '🐾 Find Familiar',
      'druid_wildshape': '🍃 Druid Wild Shape',
      'spell_summon': '⚡ Spell Summons',
      'tashas_summon': '✨ Tasha Summons',
      'class_feature': '🎯 Class Features',
      'mount': '🐴 Mounts',
      'vehicle': '🚢 Vehicles',
      'siege': '🏰 Siege Weapons'
    };
    for (const [cat, label] of Object.entries(categories)) {
      html += `<optgroup label="${label}">`;
      for (const [key, tmpl] of Object.entries(SUMMON_TEMPLATES)) {
        if (tmpl.category === cat) {
          html += `<option value="${key}">${tmpl.name}</option>`;
        }
      }
      html += `</optgroup>`;
    }

    html += `</select>
        </div>
        <div id="summon-spell-level-row-dm" style="display:none;margin-bottom:0.75rem">
          <label style="font-size:0.7rem;color:var(--text-muted)">Spell Level</label>
          <input id="s-spell-level-dm" type="number" min="1" max="9" value="3" style="width:80px;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem;margin-left:0.5rem"
            onchange="fillSummonTemplateDM(document.getElementById('summon-template-dm').value)">
          <span id="summon-hp-calc-dm" style="font-size:0.65rem;color:var(--text-muted);margin-left:0.5rem"></span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem">
          <div><label style="font-size:0.7rem">Name</label><input id="s-name-dm" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="Stolas"></div>
          <div><label style="font-size:0.7rem">Form/Type</label><input id="s-form-dm" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="owl"></div>
          <div><label style="font-size:0.7rem">AC</label><input id="s-ac-dm" type="number" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="10"></div>
          <div><label style="font-size:0.7rem">HP</label><input id="s-hp-dm" type="number" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="1"></div>
          <div><label style="font-size:0.7rem">Size</label><input id="s-size-dm" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="Tiny"></div>
          <div><label style="font-size:0.7rem">Speed</label><input id="s-speed-dm" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="30 ft."></div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.3rem;margin-top:0.5rem">
          ${['STR','DEX','CON','INT','WIS','CHA'].map(a => `<div style="text-align:center"><label style="font-size:0.6rem;color:var(--text-muted)">${a}</label><input id="s-${a.toLowerCase()}-dm" type="number" style="width:100%;padding:0.15rem;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);font-size:0.75rem;text-align:center" value="10"></div>`).join('')}
        </div>
        <div style="margin-top:0.5rem">
          <label style="font-size:0.7rem">Source (spell/feature)</label>
          <input id="s-source-dm" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="find_familiar">
        </div>
        <div style="display:flex;gap:0.5rem;margin-top:1rem">
          <button class="btn btn-primary" onclick="addSummonDM()" style="flex:1">Add to Combat ⚡</button>
          <button class="btn btn-outline" onclick="closeSummonModalDM()">Cancel</button>
        </div>
      </div>
    </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    requestAnimationFrame(() => {
      const modal = document.getElementById('summonModalDM');
      if (modal) modal.classList.add('open');
    });
  }

  function closeSummonModalDM() {
    const modal = document.getElementById('summonModalDM');
    if (modal) {
      modal.classList.remove('open');
      setTimeout(() => modal.remove(), 200);
    }
  }

  // ── Dynamic fill: monster DB or spell calculation ──
  async function fillSummonTemplateDM(key) {
    if (!key || !SUMMON_TEMPLATES[key]) {
      const slRow = document.getElementById('summon-spell-level-row-dm');
      if (slRow) slRow.style.display = 'none';
      return;
    }
    const t = SUMMON_TEMPLATES[key];

    document.getElementById('s-name-dm').value = t.name;
    document.getElementById('s-form-dm').value = '';
    document.getElementById('s-size-dm').value = t.size || 'Medium';
    document.getElementById('s-speed-dm').value = t.speed || '30 ft.';
    document.getElementById('s-source-dm').value = t.source || '';

    for (const ab of ['str','dex','con','int','wis','cha']) {
      const el = document.getElementById('s-' + ab + '-dm');
      if (el) el.value = (t.stats && t.stats[ab]) ? t.stats[ab] : 10;
    }

    // ── Monster DB lookup ──
    if (t.monster_index) {
      try {
        const r = await fetch(`/api/dm/monster/${t.monster_index}`);
        if (r.ok) {
          const m = await r.json();
          let ac = 10;
          if (Array.isArray(m.armor_class) && m.armor_class.length) ac = m.armor_class[0].value || 10;
          else if (typeof m.armor_class === 'number') ac = m.armor_class;
          document.getElementById('s-ac-dm').value = ac;
          document.getElementById('s-hp-dm').value = m.hit_points || 1;
          const statMap = {'strength':'str','dexterity':'dex','constitution':'con',
                           'intelligence':'int','wisdom':'wis','charisma':'cha'};
          for (const [src, dst] of Object.entries(statMap)) {
            const el = document.getElementById('s-' + dst + '-dm');
            if (el && m[src] != null) el.value = m[src];
          }
          if (m.speed && typeof m.speed === 'object') {
            const parts = [];
            for (const [k,v] of Object.entries(m.speed)) parts.push(v.replace(' ft.',' ft'));
            document.getElementById('s-speed-dm').value = parts.join(', ') || t.speed || '30 ft.';
          }
          // Capture full monster data
          _lastMonsterDataDM = {
            actions: (m.actions || []).map(a => ({
              name: a.name, desc: a.desc || '', attack_bonus: a.attack_bonus, damage: a.damage || []
            })),
            special_abilities: (m.special_abilities || []).map(sa => ({
              name: sa.name, desc: sa.desc || ''
            })),
            skills: m.skills || {}, senses: m.senses || {}, languages: m.languages || '',
            damage_resistances: m.damage_resistances || [],
            damage_immunities: m.damage_immunities || [],
            condition_immunities: (m.condition_immunities || []).map(c => typeof c === 'string' ? c : c.name || ''),
            proficiencies: (m.proficiencies || []).map(p => ({
              name: (p.proficiency?.name || '').replace('Skill: ',''), value: p.value
            }))
          };
          _lastTemplateKeyDM = key;
        } else {
          _lastMonsterDataDM = null;
        }
      } catch(e) { console.warn('Monster fetch failed:', e); _lastMonsterDataDM = null; }
      const slRow = document.getElementById('summon-spell-level-row-dm');
      if (slRow) slRow.style.display = 'none';
      return;
    }

    // ── Spell-based summons ──
    if (t.spell_base_level != null) {
      _lastMonsterDataDM = null;
      _lastTemplateKeyDM = key;
      const slRow = document.getElementById('summon-spell-level-row-dm');
      const slInput = document.getElementById('s-spell-level-dm');
      if (slRow) slRow.style.display = 'block';

      let spellLvl = t.spell_base_level;
      if (slInput) {
        const curVal = parseInt(slInput.value) || t.spell_base_level;
        if (curVal < t.spell_base_level) {
          slInput.value = t.spell_base_level;
          spellLvl = t.spell_base_level;
        } else {
          spellLvl = curVal;
        }
      }

      const aboveBase = Math.max(0, spellLvl - t.spell_base_level);
      const ac = (t.ac_base || 10) + (t.ac_scaling || 0) * (spellLvl || 0);
      document.getElementById('s-ac-dm').value = ac;

      let hp;
      if (t.hp_per_level) {
        hp = (t.hp_base || 0) + (t.hp_per_level || 0) * (_summonDMLevel || 1);
      } else {
        hp = (t.hp_base || 0) + (t.hp_scaling || 0) * aboveBase;
      }
      document.getElementById('s-hp-dm').value = hp;

      const hpLabel = document.getElementById('summon-hp-calc-dm');
      if (hpLabel) {
        if (t.hp_per_level) {
          hpLabel.textContent = `${t.hp_base} + ${t.hp_per_level}×Lv${_summonDMLevel} = ${hp} HP`;
        } else if (aboveBase > 0) {
          hpLabel.textContent = `${t.hp_base} + ${t.hp_scaling}×${aboveBase} = ${hp} HP`;
        } else {
          hpLabel.textContent = `${t.hp_base} HP (base)`;
        }
      }
      return;
    }

    // Fallback
    if (t.ac) document.getElementById('s-ac-dm').value = t.ac;
    else if (t.ac_base != null) document.getElementById('s-ac-dm').value = t.ac_base;
    if (t.hp_max) document.getElementById('s-hp-dm').value = t.hp_max;
    else if (t.hp_base != null) document.getElementById('s-hp-dm').value = t.hp_base;
    const slRow = document.getElementById('summon-spell-level-row-dm');
    if (slRow) slRow.style.display = 'none';
  }

  async function addSummonDM() {
    const charId = _summonDMCharId;
    if (!charId) return;

    const s = {
      name: document.getElementById('s-name-dm').value || 'Unnamed',
      form: document.getElementById('s-form-dm').value,
      category: 'custom',
      source: document.getElementById('s-source-dm').value || 'custom',
      ac: parseInt(document.getElementById('s-ac-dm').value) || 10,
      hp_max: parseInt(document.getElementById('s-hp-dm').value) || 1,
      size: document.getElementById('s-size-dm').value || 'Medium',
      speed: document.getElementById('s-speed-dm').value || '30 ft.',
      stats: {},
      features: [],
      attacks: [],
      skills: '',
      senses: '',
      hp_note: ''
    };
    for (const ab of ['str','dex','con','int','wis','cha']) {
      s.stats[ab] = parseInt(document.getElementById('s-' + ab + '-dm').value) || 10;
    }

    const key = document.getElementById('summon-template-dm').value;
    if (key && SUMMON_TEMPLATES[key]) {
      const t = SUMMON_TEMPLATES[key];
      s.category = t.category;

      if (_lastMonsterDataDM && key === _lastTemplateKeyDM) {
        const md = _lastMonsterDataDM;
        s.attacks = md.actions.map(a => ({
          name: a.name, bonus: a.attack_bonus,
          damage: (a.damage || []).map(d => `${d.damage_dice || ''} ${d.damage_type?.name || ''}`.trim()).join(' + '),
          desc: a.desc || ''
        }));
        s.features = md.special_abilities.map(sa => sa.name);
        s.feature_descs = {};
        for (const sa of md.special_abilities) s.feature_descs[sa.name] = sa.desc || '';
        if (md.proficiencies && md.proficiencies.length) {
          s.skills = md.proficiencies.map(p => `${p.name} +${p.value}`).join(', ');
        } else if (md.skills && Object.keys(md.skills).length) {
          s.skills = Object.entries(md.skills).map(([k,v]) => `${k} +${v}`).join(', ');
        }
        if (md.senses && Object.keys(md.senses).length) {
          s.senses = Object.entries(md.senses).map(([k,v]) => `${k.replace('_',' ')} ${v}`).join(', ');
        }
        if (md.languages) s.languages = md.languages;
        if (md.damage_resistances && md.damage_resistances.length) s.resistances = md.damage_resistances.join(', ');
        if (md.damage_immunities && md.damage_immunities.length) s.immunities = md.damage_immunities.join(', ');
        if (md.condition_immunities && md.condition_immunities.length) s.condition_immunities = md.condition_immunities.join(', ');
        s.hp_note = t.hp_note || '';
      } else {
        s.features = [...(t.features || [])];
        s.feature_descs = {...(t.feature_descs || {})};
        s.skills = t.skills || '';
        s.senses = t.senses || '';
        s.hp_note = t.hp_note || '';
        if (t.attacks) {
          s.attacks = t.attacks.map(a => {
            let dmg = a.damage_base || a.damage || '1d4';
            if (t.spell_base_level != null && a.damage_scaling) {
              const slIn = document.getElementById('s-spell-level-dm');
              const spellLvl = slIn ? Math.max(t.spell_base_level, parseInt(slIn.value) || t.spell_base_level) : t.spell_base_level;
              const scaled = parseInt(a.damage_scaling) * Math.max(0, spellLvl - t.spell_base_level);
              if (scaled > 0) dmg += `+${scaled}`;
            }
            let bonus = a.bonus;
            if (t.spell_base_level != null && bonus == null) {
              const slIn = document.getElementById('s-spell-level-dm');
              const spellLvl = slIn ? Math.max(t.spell_base_level, parseInt(slIn.value) || t.spell_base_level) : t.spell_base_level;
              bonus = (t.atk_bonus_base || 0) + (t.atk_bonus_scaling || 0) * (spellLvl || 0);
            }
            return {name: a.name, bonus: bonus, damage: dmg, desc: a.desc || ''};
          });
        }
      }
      _lastMonsterDataDM = null;
      _lastTemplateKeyDM = null;
    }

    try {
      const r = await fetch(`/api/character/${charId}/summons`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(s)
      });
      const data = await r.json();
      if (!r.ok || data.error) {
        alert('Failed: ' + (data.error || r.status));
        return;
      }

      // Auto-insert into combat initiative
      const summon = data.summon;
      const enId = Date.now() + Math.random();
      const dex = summon.stats?.dex || 10;
      _combatParticipants.push({
        en_id: enId,
        name: summon.name + (summon.form ? ` (${summon.form})` : ''),
        race: summon.size || 'Medium',
        class_name: summon.source || 'Summon',
        level: 0,
        is_enemy: 0,
        is_player: false,
        is_summon: true,
        owner_char_id: charId,
        owner_name: _summonDMCharName || '',
        ac: summon.ac || 10,
        hp_current: summon.hp_current || summon.hp_max || 1,
        hp_max: summon.hp_max || 1,
        initiative: Math.floor(Math.random() * 20) + 1 + Math.floor((dex - 10) / 2),
        roll: 0,
        dex_mod: Math.floor((dex - 10) / 2),
        defeated: 0,
      });
      _combatParticipants.sort((a, b) => {
        if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
        return (b.initiative||0) - (a.initiative||0);
      });
      _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
      renderInitiativeTrack();
      saveCombatState();

      closeSummonModalDM();
    } catch(e) {
      console.error('addSummonDM:', e);
      alert('Failed to create summon.');
    }
  }

  // ── Full creature/NPC detail popup for combat cards ──
  async function showCombatantDetails(enId) {
  const p = _combatParticipants.find(p => p.en_id === enId);
  if (!p) return;
  // Redirect player characters to their character sheet
  if (p.is_player || p.char_id) {
    previewCharSheet(p.char_id || p.en_id, p.name);
    return;
  }
  
  // For real NPCs, open the full NPC editor
  if (p.npc_id && p.npc_id > 0) { editNpc(p.npc_id); return; }

  // Try to get the full SRD monster card
  let monsterIndex = null;
  if (p.creature_data) {
    try {
      const cd = typeof p.creature_data === 'string' ? JSON.parse(p.creature_data) : p.creature_data;
      monsterIndex = cd._monster_index || '';
    } catch(e) {}
  }

  // Try fetching full monster card from API (works for both SRD and manual monsters)
  if (monsterIndex) {
    // Strip 'm_' prefix if present (added by creature cache)
    const lookupIndex = monsterIndex.startsWith('m_') ? monsterIndex.slice(2) : monsterIndex;
    try {
      const r = await fetch(`/api/dm/monster/${encodeURIComponent(lookupIndex)}`);
      if (r.ok) {
        const monster = await r.json();
        if (monster && monster.name) {
          showFullMonsterCard(monster, p);
          return;
        }
      }
    } catch(e) {}
  }

  // Fallback: basic stat popup for creature-only entries (manual NPCs, non-SRD)
  
  // Extract full data from creature_data if available
  let cd = {};
  if (p.creature_data) {
    try { cd = typeof p.creature_data === 'string' ? JSON.parse(p.creature_data) : p.creature_data; }
    catch(e) {}
  }
  const scores = cd.ability_scores || {};
  const hasAbilities = scores && Object.values(scores).some(v => v);
  const features = cd.features || [];
  const actions = cd.actions || [];
  const spellcasting = cd.spellcasting;
  const speed = cd.speed || '';
  const alignment = cd.alignment || '';
  const senses = cd.senses || '';
  const languages = (cd.languages || []).join(', ');
  const desc = cd.description || '';
  
  const isEnemy = p.is_enemy ? '<span class="badge badge-accent" style="font-size:0.65rem">ENEMY</span>'
    : '<span class="badge badge-muted" style="font-size:0.65rem">ALLY</span>';
  const hpPct = p.hp_max > 0 ? Math.round(p.hp_current / p.hp_max * 100) : 0;
  const hpColor = hpPct < 25 ? '#e94560' : hpPct < 50 ? '#f0a500' : '#4ecca3';
  
  // Build ability score row
  let abilRow = '';
  if (hasAbilities) {
    const statNames = ['strength','dexterity','constitution','intelligence','wisdom','charisma'];
    abilRow = '<div style="display:flex;gap:0;margin-bottom:0.75rem;background:#16213e;border-radius:6px;padding:0.5rem;justify-content:space-around;flex-wrap:wrap">' +
      statNames.map(s => {
        const val = scores[s] || 10;
        const mod = Math.floor((val - 10) / 2);
        const modStr = mod >= 0 ? '+' + mod : '' + mod;
        return '<div style="text-align:center"><div style="font-size:0.6rem;color:#999;text-transform:uppercase">' + s.slice(0,3) + '</div><div style="font-weight:700">' + val + '</div><div style="font-size:0.7rem;color:' + (mod>=0?'#4ecca3':'#e94560') + '">' + modStr + '</div></div>';
      }).join('') + '</div>';
  }
  
  // Build spellcasting section
  let spellHtml = '';
  if (spellcasting && spellcasting.spells) {
    spellHtml = '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Spellcasting</h3>';
    spellHtml += '<p style="font-size:0.8rem;color:#999;margin:0 0 0.5rem 0">' + (spellcasting.spellcasting_ability || '') + ' · DC ' + (spellcasting.spell_save_dc || '?') + ' · +' + (spellcasting.spell_attack_bonus || '?') + ' to hit</p>';
    var levels = [['cantrips','Cantrips (at will)'],['1st_level','1st Level'],['2nd_level','2nd Level'],['3rd_level','3rd Level'],['4th_level','4th Level'],['5th_level','5th Level']];
    for (var i = 0; i < levels.length; i++) {
      var key = levels[i][0], label = levels[i][1];
      var spells = spellcasting.spells[key];
      if (spells && spells.length) {
        spellHtml += '<p style="margin:0.2rem 0;font-size:0.8rem"><em>' + label + ':</em> ' + spells.join(', ') + '</p>';
      }
    }
  }
  
  // Build features/actions
  let featHtml = '';
  if (features.length) {
    featHtml += '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Features</h3>';
    features.forEach(function(f) {
      featHtml += '<div style="margin-bottom:0.5rem"><strong style="font-style:italic">' + f.name + '.</strong> ' + (f.description || '') + '</div>';
    });
  }
  if (actions.length) {
    featHtml += '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Actions</h3>';
    actions.forEach(function(a) {
      featHtml += '<div style="margin-bottom:0.5rem"><strong style="font-style:italic">' + a.name + '.</strong> ' + (a.description || '') + '</div>';
    });
  }
  
  document.getElementById('charSheetTitle').textContent = p.name;
  document.getElementById('charSheetFrame').srcdoc = [
    '<html><body style="font-family:system-ui;background:#1a1a2e;color:#e0e0e0;padding:1.5rem;margin:0;max-width:650px">',
    '<h2 style="margin:0 0 0.25rem 0">', p.name, ' ', isEnemy, '</h2>',
    '<p style="color:#999;margin:0 0 0.5rem 0">', (p.race || 'Unknown'), ' · L', (p.level || '?'), ' ', (p.class_name || ''), (p.role ? ' · ' + p.role : ''), (alignment ? ' · ' + alignment : ''), '</p>',
    desc ? '<p style="color:#aaa;font-size:0.85rem;font-style:italic;margin:0 0 0.75rem 0">' + desc + '</p>' : '',
    '<div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:center;padding:0.5rem 0;border-top:2px solid #e94560;border-bottom:2px solid #e94560;margin-bottom:0.75rem">',
    '<div style="text-align:center"><div style="font-size:0.6rem;color:#999">Armor Class</div><div style="font-weight:700;font-size:1.1rem">', (p.ac || '?'), '</div></div>',
    '<div style="text-align:center"><div style="font-size:0.6rem;color:#999">Hit Points</div><div style="font-weight:700;font-size:1.1rem">', p.hp_current, '/', p.hp_max, '</div><div style="width:80px;height:5px;border-radius:3px;background:#333;overflow:hidden;margin:0.15rem auto 0"><div style="height:100%;border-radius:3px;background:', hpColor, ';width:', hpPct, '%"></div></div></div>',
    speed ? '<div style="text-align:center"><div style="font-size:0.6rem;color:#999">Speed</div><div style="font-size:0.85rem">' + speed + '</div></div>' : '',
    '<div style="text-align:center"><div style="font-size:0.6rem;color:#999">Initiative</div><div style="font-size:0.85rem">🎲 ', (p.initiative || 0), '</div></div>',
    '<div style="text-align:center"><div style="font-size:0.6rem;color:#999">Status</div><div style="font-size:0.85rem">', (p.defeated ? '💀 Defeated' : '✅ Active'), '</div></div>',
    '</div>',
    abilRow,
    spellHtml,
    featHtml,
    senses ? '<p style="font-size:0.8rem;color:#999;margin:0.25rem 0"><strong>Senses:</strong> ' + senses + '</p>' : '',
    languages ? '<p style="font-size:0.8rem;color:#999;margin:0.25rem 0"><strong>Languages:</strong> ' + languages + '</p>' : '',
    '</body></html>'
  ].join('');
  openModal('charSheetModal');
}

// Full SRD monster stat block rendered in the iframe
function showFullMonsterCard(monster, combatant) {
  const stats = ['strength','dexterity','constitution','intelligence','wisdom','charisma'];
  const statRow = stats.map(s => {
    const val = monster[s] || 10;
    const mod = Math.floor((val - 10) / 2);
    const modStr = mod >= 0 ? '+' + mod : '' + mod;
    return `<div style="text-align:center"><div style="font-size:0.6rem;color:#999;text-transform:uppercase">${s.slice(0,3)}</div><div style="font-weight:700">${val}</div><div style="font-size:0.7rem;color:${mod>=0?'#4ecca3':'#e94560'}">${modStr}</div></div>`;
  }).join('');

  const speeds = monster.speed ? Object.entries(monster.speed).map(([k,v]) => `${k} ${v}`).join(', ') : '?';
  const profBonus = monster.proficiency_bonus ? `+${monster.proficiency_bonus}` : '?';
  const senses = monster.senses ? Object.entries(monster.senses).map(([k,v]) => `${k.replace(/_/g,' ')} ${v}`).join(', ') : '?';
  const damageImm = (monster.damage_immunities || []).join(', ') || '—';
  const damageRes = (monster.damage_resistances || []).join(', ') || '—';
  const damageVuln = (monster.damage_vulnerabilities || []).join(', ') || '—';
  const conditionImm = (monster.condition_immunities || []).map(c => c.replace(/_/g,' ')).join(', ') || '—';

  const hpPct = combatant.hp_max > 0 ? Math.round(combatant.hp_current / combatant.hp_max * 100) : 0;
  const hpColor = hpPct < 25 ? '#e94560' : hpPct < 50 ? '#f0a500' : '#4ecca3';

  let abilitiesHtml = '';
  if (monster.special_abilities) {
    abilitiesHtml += '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Special Abilities</h3>';
    monster.special_abilities.forEach(a => {
      abilitiesHtml += `<div style="margin-bottom:0.5rem"><strong style="font-style:italic">${a.name}.</strong> ${a.desc || ''}</div>`;
    });
  }
  if (monster.actions) {
    abilitiesHtml += '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Actions</h3>';
    monster.actions.forEach(a => {
      abilitiesHtml += `<div style="margin-bottom:0.5rem"><strong style="font-style:italic">${a.name}.</strong> ${a.desc || ''}</div>`;
    });
  }
  if (monster.legendary_actions) {
    abilitiesHtml += '<h3 style="margin:1rem 0 0.5rem 0;border-bottom:1px solid #333;padding-bottom:0.25rem">Legendary Actions</h3>';
    monster.legendary_actions.forEach(a => {
      abilitiesHtml += `<div style="margin-bottom:0.5rem"><strong style="font-style:italic">${a.name}.</strong> ${a.desc || ''}</div>`;
    });
  }

  document.getElementById('charSheetTitle').textContent = monster.name;
  document.getElementById('charSheetFrame').srcdoc = `
    <html><body style="font-family:system-ui;background:#1a1a2e;color:#e0e0e0;padding:1.5rem;margin:0;max-width:650px">
      <h2 style="margin:0">${monster.name}</h2>
      <p style="color:#999;margin:0.25rem 0 0.5rem 0">${monster.size || '?'} ${monster.type || '?'}, ${monster.alignment || 'unaligned'}</p>
      <div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:center;padding:0.5rem 0;border-top:2px solid #e94560;border-bottom:2px solid #e94560;margin-bottom:0.75rem">
        <div style="text-align:center"><div style="font-size:0.6rem;color:#999">Armor Class</div><div style="font-weight:700;font-size:1.1rem">${combatant.ac || '?'}</div></div>
        <div style="text-align:center"><div style="font-size:0.6rem;color:#999">Hit Points</div><div style="font-weight:700;font-size:1.1rem">${combatant.hp_current}/${combatant.hp_max}</div><div style="width:80px;height:5px;border-radius:3px;background:#333;overflow:hidden;margin:0.15rem auto 0"><div style="height:100%;border-radius:3px;background:${hpColor};width:${hpPct}%"></div></div></div>
        <div style="text-align:center"><div style="font-size:0.6rem;color:#999">Speed</div><div style="font-size:0.85rem">${speeds}</div></div>
      </div>
      <div style="display:flex;gap:0;margin-bottom:0.75rem;background:#16213e;border-radius:6px;padding:0.5rem;justify-content:space-around;flex-wrap:wrap">${statRow}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.4rem 1rem;font-size:0.8rem;margin-bottom:0.75rem">
        <div><span style="color:#999">Senses</span> ${senses}</div>
        <div><span style="color:#999">Languages</span> ${monster.languages || '—'}</div>
        <div><span style="color:#999">CR</span> ${monster.challenge_rating} (${monster.xp} XP) · PB +${monster.proficiency_bonus||'?'}</div>
        <div><span style="color:#999">Init</span> 🎲 ${combatant.initiative || 0}</div>
        <div><span style="color:#999">Dmg Imm</span> ${damageImm}</div>
        <div><span style="color:#999">Dmg Res</span> ${damageRes}</div>
        <div><span style="color:#999">Dmg Vuln</span> ${damageVuln}</div>
        <div><span style="color:#999">Cond Imm</span> ${conditionImm}</div>
      </div>
      ${abilitiesHtml}
    </body></html>`;
  openModal('charSheetModal');
}

// ── Bench / unbench buttons (mobile-friendly, preserves initiative) ──
function benchCombatant(enId) {
  const idx = _combatParticipants.findIndex(p => p.en_id === enId);
  if (idx === -1) return;
  const [p] = _combatParticipants.splice(idx, 1);
  _benchedNpcs.push(p);
  _combatOrder = _combatOrder.filter(id => id !== enId);
  // Fix turn index
  const alive = _combatParticipants.filter(x => !x.defeated);
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = Math.max(0, alive.length - 1);
  renderInitiativeTrack();
  renderPlayersPanel();
  renderBenchedNpcs();
  saveCombatState();
}

function unbenchCombatant(enId) {
  const idx = _benchedNpcs.findIndex(p => p.en_id === enId);
  if (idx === -1) return;
  const [p] = _benchedNpcs.splice(idx, 1);
  _combatParticipants.push(p);
  _combatParticipants.sort((a, b) => {
    if ((a.defeated||0) !== (b.defeated||0)) return (a.defeated ? 1 : -1);
    return (b.initiative||0) - (a.initiative||0);
  });
  // Restore to initiative order in the correct spot (by initiative)
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  renderInitiativeTrack();
  renderPlayersPanel();
  renderBenchedNpcs();
  saveCombatState();
}

// ── Drop on benched area: remove NPC from combat ──
function dropOnBenched(e) {
  e.preventDefault();
  if (!_initDragEnId) return;
  const p = _combatParticipants.find(p => p.en_id === _initDragEnId);
  if (!p || p.is_player) return; // players go to players panel, not benched

  // Remove from participants and order
  _combatParticipants = _combatParticipants.filter(x => x.en_id !== _initDragEnId);
  _combatOrder = _combatOrder.filter(id => id !== _initDragEnId);

  // Add to benched
  _benchedNpcs.push(p);

  // Fix turn index
  const alive = _combatParticipants.filter(x => !x.defeated);
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = Math.max(0, alive.length - 1);

  renderInitiativeTrack();
  renderBenchedNpcs();
  saveCombatState();
}

// ── Drop on players panel: remove player from combat, return to waiting ──
function dropOnPlayersPanel(e) {
  e.preventDefault();
  if (!_initDragEnId) return;
  const p = _combatParticipants.find(p => p.en_id === _initDragEnId);
  if (!p || !p.is_player) return;

  // Remove from participants and order
  _combatParticipants = _combatParticipants.filter(x => x.en_id !== _initDragEnId);
  _combatOrder = _combatOrder.filter(id => id !== _initDragEnId);

  // Fix turn index
  const alive = _combatParticipants.filter(x => !x.defeated);
  if (_combatTurnIdx >= alive.length) _combatTurnIdx = Math.max(0, alive.length - 1);

  renderInitiativeTrack();
  renderPlayersPanel();
  saveCombatState();
}

// ── Render benched NPCs ──
function renderBenchedNpcs() {
  const container = document.getElementById('benchedNpcs');
  if (_benchedNpcs.length === 0) {
    container.innerHTML = '<span style="color:var(--text-muted);font-size:0.7rem;text-align:center">Drag NPCs here to remove from combat</span>';
    return;
  }
  container.innerHTML = _benchedNpcs.map(p => {
    const badge = p.is_enemy ? '<span class="badge badge-accent" style="font-size:0.6rem">ENEMY</span>'
      : '<span class="badge badge-muted" style="font-size:0.6rem">ALLY</span>';
    return `<div class="initiative-card benched-card" draggable="true"
      data-en-id="${p.en_id}"
      ondragstart="benchedDragStart(event, ${p.en_id})"
      ondragend="benchedDragEnd(event)"
      style="cursor:grab;opacity:0.7;border-style:dashed">
      <div class="init-turn-marker" style="background:var(--text-muted);color:var(--bg)">⏸</div>
      <div class="init-info" style="flex:1">
        <div class="init-name">${p.name} ${badge} <button class="init-btn" onclick="event.stopPropagation();showCombatantDetails(${p.en_id})" title="View details" style="font-size:0.65rem;padding:0 0.2rem">📋</button></div>
        <div class="init-meta">AC ${p.ac} · HP ${p.hp_current}/${p.hp_max}</div>
      </div>
      <button class="init-btn" onclick="event.stopPropagation();unbenchCombatant(${p.en_id})" title="Return to combat" style="color:var(--success)">▶</button>
    </div>`;
  }).join('');
}

// ── Benched NPC drag back into initiative track ──
let _benchedDragEnId = null;

function benchedDragStart(e, enId) {
  _benchedDragEnId = enId;
  e.dataTransfer.effectAllowed = 'move';
  e.target.closest('.initiative-card')?.classList.add('dragging');
}

function benchedDragEnd(e) {
  document.querySelectorAll('.initiative-card.dragging').forEach(c => c.classList.remove('dragging'));
  _benchedDragEnId = null;
}

// ── Drag-and-drop reorder ──
let _initDragEnId = null;

function initDragStart(e, enId) {
  _initDragEnId = enId;
  e.dataTransfer.effectAllowed = 'move';
  e.target.closest('.initiative-card')?.classList.add('dragging');
}

function initDragEnd(e) {
  document.querySelectorAll('.initiative-card.dragging').forEach(c => c.classList.remove('dragging'));
  _initDragEnId = null;
}

function initDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function initDrop(e, targetEnId) {
  e.preventDefault();
  if (!_initDragEnId || _initDragEnId === targetEnId) return;

  // Reorder in _combatOrder
  const dragIdx = _combatOrder.indexOf(_initDragEnId);
  const targetIdx = _combatOrder.indexOf(targetEnId);
  if (dragIdx === -1 || targetIdx === -1) return;

  _combatOrder.splice(dragIdx, 1);
  _combatOrder.splice(targetIdx, 0, _initDragEnId);

  // If current turn was affected, adjust
  const alive = _combatParticipants.filter(p => !p.defeated);
  const currentEnId = alive[_combatTurnIdx]?.en_id;
  if (currentEnId) {
    _combatTurnIdx = _combatOrder.indexOf(currentEnId);
    if (_combatTurnIdx < 0) _combatTurnIdx = 0;
  }

  renderInitiativeTrack();
  saveCombatState();
}

// ── Quick Add: toggle type/CR filters based on kind selector ──
function toggleCombatFilters() {
  const kind = document.getElementById('combatCreatureKind')?.value || 'all';
  const typeSel = document.getElementById('combatCreatureType');
  const crSel = document.getElementById('combatCreatureCr');
  const showMonsterFilters = kind === 'all' || kind === 'monster';
  if (typeSel) typeSel.style.display = showMonsterFilters ? '' : 'none';
  if (crSel) crSel.style.display = showMonsterFilters ? '' : 'none';
}

// ── Quick Add: filter creature search results ──
function filterCombatCreatures() {
  const q = (document.getElementById('combatCreatureSearch')?.value || '').toLowerCase();
  const kind = document.getElementById('combatCreatureKind')?.value || 'all';
  const typeFilter = document.getElementById('combatCreatureType')?.value || 'all';
  const crFilter = document.getElementById('combatCreatureCr')?.value || 'all';
  const results = document.getElementById('combatCreatureResults');
  if (!results) return;

  let filtered = _combatCreatureCache;
  if (q) filtered = filtered.filter(c => c.name.toLowerCase().includes(q));
  if (kind !== 'all') filtered = filtered.filter(c => c._kind === kind);
  if (typeFilter !== 'all') filtered = filtered.filter(c => c._type === typeFilter);
  if (crFilter !== 'all') {
    filtered = filtered.filter(c => {
      if (c._kind !== 'monster') return true;  // NPCs pass through
      const cr = parseFloat(c._cr);
      if (isNaN(cr)) return false;
      if (crFilter === '0') return cr === 0;
      if (crFilter === '0.125') return cr === 0.125;
      if (crFilter === '0.25') return cr === 0.25;
      if (crFilter === '0.5') return cr === 0.5;
      if (crFilter === '25+') return cr >= 25;
      const [lo, hi] = crFilter.split('-').map(Number);
      return cr >= lo && cr <= hi;
    });
  }
  if (_coreOnlyDM) filtered = filtered.filter(c => c._kind === 'npc' || isCoreSourceDM((c._raw && c._raw.source) || ''));

  if (filtered.length === 0) {
    results.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;text-align:center;padding:0.5rem">No matches</span>';
    return;
  }

  // Show up to 20 results
  const shown = filtered.slice(0, 20);
  let html = '';
  shown.forEach((c, ci) => {
    const kindBadge = c._kind === 'monster'
      ? '<span class="badge badge-accent" style="font-size:0.55rem">MON</span>'
      : c.id < 0 ? '<span class="badge badge-muted" style="font-size:0.55rem">📖</span>' : '';
    const crDisplay = c._kind === 'monster' ? `CR ${c.level} · ` : '';
    const typeDisplay = c._kind === 'monster' && c._type ? `${c._type.charAt(0).toUpperCase() + c._type.slice(1)} · ` : '';
    const detailDisplay = c._kind === 'monster'
      ? `${crDisplay}${typeDisplay}AC ${c.ac} · ${c.race || '?'}`
      : `${c.race || ''}${c.class_name ? ' L' + c.level + ' ' + c.class_name : ''} · AC ${c.ac}`;
    const hpDisplay = c._kind === 'monster'
      ? `HP ${c.hp_current}`
      : `HP ${c.hp_current}/${c.hp_max}`;
    const sourceHtml = c._kind === 'monster' && c._raw?.source
      ? ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${c._raw.source.replace(/'/g, "\\'")}')" style="font-size:0.55rem;cursor:pointer;opacity:0.7" title="Click to open ${c._raw.source}">📚</span>`
      : '';
    html += `<div style="display:flex;align-items:center;gap:0.3rem;padding:0.3rem 0.5rem;background:var(--bg);border-radius:4px"
      data-idx="${_combatCreatureCache.indexOf(c)}">
      ${c._kind === 'monster' && c._raw?.index ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showMonster('${c._raw.index}')" title="Monster details" style="font-size:0.6rem;padding:0.1rem 0.3rem;flex-shrink:0">ℹ️</button>` : ''}
      ${c._kind === 'npc' && c.id > 0 ? `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();showNpcInfo(${c.id}, '${c.name.replace(/'/g, "\\'")}')" title="NPC details" style="font-size:0.6rem;padding:0.1rem 0.3rem;flex-shrink:0">ℹ️</button>` : ''}
      <span style="font-size:0.78rem;flex:1 1 auto;min-width:0;overflow-wrap:break-word;word-break:break-word">${kindBadge} <strong>${c.name}</strong> <span style="color:var(--text-muted)">${detailDisplay} · ${hpDisplay}</span>${sourceHtml}</span>
      <button class="btn btn-primary btn-sm" style="flex-shrink:0;font-size:0.7rem" onclick="addCombatCreature(${_combatCreatureCache.indexOf(c)})">+ Add</button>
    </div>`;
  });
  if (filtered.length > 20) {
    html += `<span style="color:var(--text-muted);font-size:0.7rem;text-align:center;padding:0.25rem">+ ${filtered.length - 20} more — refine search</span>`;
  }
  results.innerHTML = html;
}

// ── Add searched creature to combat ──
async function addCombatCreature(cacheIndex) {
  if (!_combatEncId) return alert('Select an encounter first');
  const c = _combatCreatureCache[cacheIndex];
  if (!c) return;

  if (c._kind === 'npc' && c.id > 0) {
    // DB NPC — use add-npc
    try {
      const r = await fetch(`/api/dm/encounter/${_combatEncId}/add-npc`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({npc_id: c.id, initiative: 0})
      });
      const d = await r.json();
      if (!d.ok) return alert(d.error || 'Failed to add');
      const newEnId = d.id;
      await refreshCombatParticipants();
      if (newEnId && !_combatOrder.includes(newEnId)) {
        _combatOrder.push(newEnId);
      }
      saveCombatState();
    } catch(e) { alert('Failed to add NPC'); }
  } else {
    // Monster or manual NPC — use add-creature
    try {
      const body = {
        name: c.name, race: c.race || '', class_name: c.class_name || '',
        level: c._kind === 'monster' ? 1 : (c.level || 1),
        hp: c.hp_current || 10, hp_max: c.hp_max || 10, ac: c.ac || 10,
        is_enemy: c.is_enemy || 0, role: c.role || '',
        xp_reward: c.xp_reward || 0,
        _monster_index: c._kind === 'monster' ? (c._raw?.index || '') : '',
      };
      // Include full stat block for monsters
      if (c._kind === 'monster' && c._raw) {
        const m = c._raw;
        body.ability_scores = {strength: m.strength, dexterity: m.dexterity, constitution: m.constitution, intelligence: m.intelligence, wisdom: m.wisdom, charisma: m.charisma};
        body.speed = typeof m.speed === 'string' ? m.speed : Object.values(m.speed || {}).join(', ');
        body.alignment = m.alignment || '';
        body.senses = typeof m.senses === 'string' ? m.senses : (m.senses ? Object.entries(m.senses).map(([k,v]) => `${k} ${v}`).join(', ') : '');
        body.languages = typeof m.languages === 'string' ? m.languages : '';
        body.challenge_rating = m.challenge_rating || null;
        body.skills = m.skills || {};
        body.saving_throws = m.saving_throws || {};
        body.damage_resistances = m.damage_resistances || [];
        body.damage_immunities = m.damage_immunities || [];
        body.condition_immunities = m.condition_immunities || [];
        body.features = m.special_abilities || [];
        body.actions = m.actions || [];
        body.spellcasting = m.spellcasting || null;
      }
      const r = await fetch(`/api/dm/encounter/${_combatEncId}/add-creature`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const d = await r.json();
      if (!d.ok) return alert(d.error || 'Failed to add');
      const newEnId = d.id;
      await refreshCombatParticipants();
      if (newEnId && !_combatOrder.includes(newEnId)) {
        _combatOrder.push(newEnId);
      }
      saveCombatState();
    } catch(e) { alert('Failed to add creature'); }
  }
}

// ── Save combat state ──
function saveCombatState() {
  if (!_combatEncId) return;
  const players = _combatParticipants.filter(p => p.is_player).map(p => ({
    en_id: p.en_id, char_id: p.char_id, name: p.name, race: p.race || '',
    class_name: p.class_name || '', level: p.level || 1, ac: p.ac || 10,
    hp_current: p.hp_current, hp_max: p.hp_max, defeated: p.defeated || 0,
    initiative: p.initiative || 0, dex_mod: p.dex_mod || 0
  }));
  fetch(`/api/dm/encounter/${_combatEncId}/combat-state`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      action: 'save', round: _combatRound, turn_index: _combatTurnIdx,
      initiative_order: _combatOrder,
      benched_en_ids: _benchedNpcs.map(p => p.en_id),
      player_participants: players,
      campaign_id: _combatCampId || null
    })
  }).catch(() => {});
}

// ── End combat ──
async function endCombat() {
  if (!confirm('End this combat? State will be saved for later.')) return;
  stopCombatPolling();
  _combatRound = 1;
  _combatTurnIdx = 0;
  _combatOrder = _combatParticipants.filter(p => !p.defeated).map(p => p.en_id);
  _benchedNpcs = [];
  renderInitiativeTrack();
  renderPlayersPanel();
  renderBenchedNpcs();
  saveCombatState();
  document.getElementById('combatStatus').textContent = 'Combat ended — initiative saved';
}

// ── Combat Conditions ────────────────────────────────────────────────────
const _combatCondExpanded = new Set();
const CONDITION_COLORS = {
  Blinded:'#6a0dad;color:#fff',Charmed:'#e75480;color:#fff',Deafened:'#5c6bc0;color:#fff',
  Frightened:'#7b1fa2;color:#fff',Grappled:'#bf360c;color:#fff',Incapacitated:'#b71c1c;color:#fff',
  Invisible:'#1e88e5;color:#fff',Paralyzed:'#880e4f;color:#fff',Petrified:'#4e342e;color:#fff',
  Poisoned:'#2e7d32;color:#fff',Prone:'#e65100;color:#fff',Restrained:'#c62828;color:#fff',
  Stunned:'#d84315;color:#fff',Unconscious:'#37474f;color:#fff',Exhaustion:'#827717;color:#fff',
  Concentration:'#6a1b9a;color:#fff',Blessed:'#f9a825;color:#333',Haste:'#00acc1;color:#fff',
  Baned:'#8b0000;color:#fff',
  Raging:'#e8590c;color:#fff',
};
const STANDARD_CONDITIONS = [
  {name:'Blinded',desc:"Can't see. Auto-fails ability checks requiring sight. Attack rolls against you have advantage. Your attacks have disadvantage."},
  {name:'Charmed',desc:"Can't attack the charmer or target them with harmful abilities. Charmer has advantage on social checks against you."},
  {name:'Deafened',desc:"Can't hear. Auto-fails ability checks requiring hearing."},
  {name:'Frightened',desc:"Disadvantage on ability checks and attack rolls while source of fear is in sight. Can't willingly move closer to the source."},
  {name:'Grappled',desc:"Speed becomes 0. Grappler can release at any time. Ends if grappler is incapacitated or you're moved out of reach."},
  {name:'Incapacitated',desc:"Can't take actions or reactions."},
  {name:'Invisible',desc:"Impossible to see without magic. Attack rolls against you have disadvantage. Your attacks have advantage."},
  {name:'Paralyzed',desc:"Can't move or speak. Auto-fails STR/DEX saves. Attackers have advantage; hits within 5 ft are critical."},
  {name:'Petrified',desc:"Transformed to solid stone. Incapacitated, can't move or speak. Resistant to all damage. Immune to poison and disease."},
  {name:'Poisoned',desc:"Disadvantage on attack rolls and ability checks."},
  {name:'Prone',desc:"Crawling unless you stand up. Disadvantage on attacks. Attackers within 5 ft have advantage; farther have disadvantage."},
  {name:'Restrained',desc:"Speed becomes 0. Disadvantage on attack rolls and DEX saves. Attackers have advantage on attacks against you."},
  {name:'Stunned',desc:"Incapacitated, can't move, speak falteringly. Auto-fails STR/DEX saves. Attackers have advantage."},
  {name:'Unconscious',desc:"Incapacitated, can't move or speak, unaware. Auto-fails STR/DEX saves. Attackers have advantage; hits within 5 ft are critical."},
  {name:'Exhaustion',desc:"6 levels. 1: disadv ability checks. 2: speed halved. 3: disadv atk/saves. 4: HP max halved. 5: speed 0. 6: death."},
  {name:'Concentration',desc:"Maintaining a spell. CON save on damage (DC 10 or half damage, whichever higher) or lose spell."},
  {name:'Blessed',desc:"+1d4 to attack rolls and saving throws (Bless spell)."},
  {name:'Haste',desc:"Speed doubled, +2 AC, adv DEX saves, extra action (Attack/Dash/Disengage/Hide/Use Object)."},
  {name:'Baned',desc:"Subtract 1d4 from attack rolls and saving throws (Bane spell)."},
  {name:'Raging',desc:"Adv on STR checks/saves. +melee damage. Resistant to B/P/S. Can't cast or concentrate. Ends if no attack or damage taken in a round."},
];
const EXHAUSTION_LEVELS = [
  {level:1, desc:'Disadvantage on ability checks.'},
  {level:2, desc:'Speed halved.'},
  {level:3, desc:'Disadvantage on attack rolls and saving throws.'},
  {level:4, desc:'Hit point maximum halved.'},
  {level:5, desc:'Speed reduced to 0.'},
  {level:6, desc:'Death.'},
];

function combatAddCondition(enId, name, desc) {
  if (!name) return;
  const p = _combatParticipants.find(x => x.en_id === enId);
  if (!p) return;
  if (!p.conditions) p.conditions = [];
  if (p.conditions.some(c => c.name.toLowerCase() === name.toLowerCase())) return;
  p.conditions.push({name:name, description:desc||'', source:''});
  // If it's a PC, also save to character DB
  if (p.char_id && p.is_player) {
    fetch('/api/character/'+p.char_id+'/conditions', {
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name,description:desc||'',source:''})
    }).catch(()=>{});
  }
  renderInitiativeTrack();
  saveCombatState();
}

async function combatDismissCond(enId, name) {
  const p = _combatParticipants.find(x => x.en_id === enId);
  if (!p) return;
  p.conditions = (p.conditions||[]).filter(c => c.name.toLowerCase() !== name.toLowerCase());
  if (p.char_id && p.is_player) {
    fetch('/api/character/'+p.char_id+'/conditions', {
      method:'DELETE',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name})
    }).catch(()=>{});
  }
  renderInitiativeTrack();
  saveCombatState();
}

function combatToggleCondExpand(enId) {
  if (_combatCondExpanded.has(enId)) {
    _combatCondExpanded.delete(enId);
  } else {
    _combatCondExpanded.add(enId);
  }
  renderInitiativeTrack();
}

function combatToggleCond(event, enId, name, desc) {
  document.querySelectorAll('.cond-detail-popout').forEach(el => el.remove());
  const badge = event.currentTarget;
  const popout = document.createElement('div');
  popout.className = 'cond-popout cond-detail-popout';
  const style = CONDITION_COLORS[name] || 'var(--accent)';
  popout.innerHTML = '<strong style="color:'+style.split(';')[0]+'">'+name+'</strong><div style="font-size:0.65rem;color:var(--text-muted);margin:0.3rem 0;line-height:1.3">'+desc+'</div><button style="display:block;margin-top:0.4rem;width:100%;padding:0.2rem;border:1px solid var(--danger);border-radius:4px;background:transparent;color:var(--danger);cursor:pointer;font-size:0.6rem" onclick="combatDismissCond('+enId+',\''+name+'\');this.parentElement.remove()">Remove</button>';
  popout.style.cssText = 'position:absolute;z-index:1001;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.6rem;min-width:180px;max-width:250px;box-shadow:0 4px 12px rgba(0,0,0,0.3);font-size:0.7rem';
  badge.parentElement.style.position = 'relative';
  badge.parentElement.appendChild(popout);
  const rect = badge.getBoundingClientRect();
  const parentRect = badge.parentElement.getBoundingClientRect();
  popout.style.left = '0';
  popout.style.top = (rect.bottom - parentRect.top + 4) + 'px';
}

function combatOpenCondPicker(enId) {
  document.querySelectorAll('.cond-detail-popout').forEach(el => el.remove());
  let html = '<div class="cond-popout" id="cond-picker-combat" style="position:fixed;top:15%;left:50%;transform:translateX(-50%);z-index:2000;width:380px;max-height:70vh;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.8rem;box-shadow:0 4px 12px rgba(0,0,0,0.4)">';
  html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><strong style="font-size:0.75rem">Add Condition</strong><span onclick="document.getElementById(\'cond-picker-combat\').remove()" style="cursor:pointer;font-size:0.75rem">✕</span></div>';
  for (const c of STANDARD_CONDITIONS) {
    const style = CONDITION_COLORS[c.name] || 'var(--accent)';
    if (c.name === 'Exhaustion') {
      html += '<div onclick="combatOpenExhaustionPicker('+enId+')" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem"><span class="cond-badge" style="flex-shrink:0;background:'+style.split(';')[0]+';'+style.split(';')[1]+'">'+c.name+'</span><span style="color:var(--text-muted);font-size:0.6rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">Pick level 1–6…</span></div>';
    } else {
    html += '<div onclick="combatAddCondition('+enId+',\''+c.name+'\',\''+c.desc.replace(/'/g,"\\'")+'\');document.getElementById(\'cond-picker-combat\').remove()" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;flex-wrap:wrap;align-items:flex-start;gap:0.3rem"><span class="cond-badge" style="flex-shrink:0;background:'+style.split(';')[0]+';'+style.split(';')[1]+'">'+c.name+'</span><span style="color:var(--text-muted);font-size:0.6rem;flex:1;min-width:180px;word-wrap:break-word;line-height:1.3">'+c.desc+'</span></div>';
    }
  }
  html += '<div style="margin-top:0.4rem;border-top:1px solid var(--border);padding-top:0.4rem"><input id="custom-cond-name-combat" placeholder="Custom condition…" style="width:100%;padding:0.25rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.65rem;margin-bottom:0.2rem"><button onclick="combatAddCondition('+enId+',document.getElementById(\'custom-cond-name-combat\').value,\'\');document.getElementById(\'cond-picker-combat\').remove()" style="width:100%;padding:0.2rem;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:0.65rem">Add Custom</button></div>';
  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);
}

function combatOpenExhaustionPicker(enId) {
  const el = document.getElementById('cond-picker-combat');
  if (!el) return;
  el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem"><a href="#" onclick="event.preventDefault();document.getElementById(\'cond-picker-combat\').remove();combatOpenCondPicker('+enId+')" style="font-size:0.7rem;color:var(--text-muted)">← Back</a><strong style="font-size:0.75rem">Exhaustion Level</strong><span onclick="document.getElementById(\'cond-picker-combat\').remove()" style="cursor:pointer;font-size:0.75rem">✕</span></div>';
  for (const e of EXHAUSTION_LEVELS) {
    const label = 'Exhaustion '+e.level;
    const color = CONDITION_COLORS['Exhaustion'] || '#827717;color:#fff';
    el.innerHTML += '<div onclick="combatAddCondition('+enId+',\''+label+'\', \'Level '+e.level+': '+e.desc.replace(/'/g,"\\'")+'\');document.getElementById(\'cond-picker-combat\').remove()" style="padding:0.25rem 0.4rem;cursor:pointer;border-radius:4px;margin-bottom:0.1rem;font-size:0.65rem;display:flex;align-items:center;gap:0.4rem"><span class="cond-badge" style="flex-shrink:0;background:'+color.split(';')[0]+';'+color.split(';')[1]+'">'+label+'</span><span style="color:var(--text-muted);font-size:0.6rem;flex:1">'+e.desc+'</span></div>';
  }
}

// ── Live HP Polling ──────────────────────────────────────────────────────

let _combatPollTimer = null;

async function syncPlayerAndSummonHP() {
  // Collect unique character IDs from players and summon owners
  const charIds = new Set();
  for (const p of _campaignPlayers) {
    if (p.char_id) charIds.add(p.char_id);
  }
  for (const p of _combatParticipants) {
    if (p.is_summon && p.owner_char_id) charIds.add(p.owner_char_id);
    if (p.is_player && p.char_id) charIds.add(p.char_id);
  }
  if (charIds.size === 0) return;

  try {
    const r = await fetch('/api/sync-combat-hp', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({char_ids: Array.from(charIds)})
    });
    const data = await r.json();
    const chars = data.characters || {};

    // Update campaign players HP
    for (const p of _campaignPlayers) {
      const c = chars[String(p.char_id)];
      if (c && c.hp_current !== undefined) {
        p.hp_current = c.hp_current;
        p.hp_max = c.hp_max;
      }
    }

    // Update combat participants HP (players + summons)
    let changed = false;
    for (const p of _combatParticipants) {
      if (p.is_player && p.char_id) {
        const c = chars[String(p.char_id)];
        if (c && c.hp_current !== undefined && c.hp_current !== p.hp_current) {
          p.hp_current = c.hp_current;
          p.hp_max = c.hp_max;
          changed = true;
        }
        // Sync conditions from sheet
        if (c && c.conditions) {
          const srvConds = JSON.stringify(c.conditions);
          const localConds = JSON.stringify(p.conditions || []);
          if (srvConds !== localConds) {
            p.conditions = c.conditions;
            changed = true;
          }
        }
      } else if (p.is_summon && p.owner_char_id && p.summon_idx !== undefined) {
        const c = chars[String(p.owner_char_id)];
        if (c && c.summons && c.summons[p.summon_idx]) {
          const s = c.summons[p.summon_idx];
          if (s.hp_current !== undefined && s.hp_current !== p.hp_current) {
            p.hp_current = s.hp_current;
            p.hp_max = s.hp_max;
            changed = true;
          }
        }
      }
    }

    // Re-render if anything changed
    if (changed) {
      renderInitiativeTrack();
      renderPlayersPanel();
    }
  } catch(e) { /* silent */ }
}

function startCombatPolling() {
  stopCombatPolling();
  _combatPollTimer = setInterval(syncPlayerAndSummonHP, 2000);
}

function stopCombatPolling() {
  if (_combatPollTimer) {
    clearInterval(_combatPollTimer);
    _combatPollTimer = null;
  }
}


// ── Page load: auto-restore last combat encounter + campaign ──
document.addEventListener('DOMContentLoaded', async function() {
  // Populate combat + items dropdowns regardless of active tab (each has its own guard)
  initCombatPanel();
  loadItemsPanel();

  let lastEncId = null;
  try { lastEncId = localStorage.getItem('combatLastEncounterId'); } catch(e) {}
  if (lastEncId) {
    const sel = document.getElementById('combatEncounterSelect');
    if (sel) {
      // Check if the option still exists
      const opt = sel.querySelector(`option[value="${lastEncId}"]`);
      if (opt) {
        sel.value = lastEncId;
        await loadCombatEncounter();
      }
    }
  }
});
