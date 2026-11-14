/* dm/combat-ui.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
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
          ${['STR','DEX','CON','INT','WIS','CHA'].map(a => `<div style="text-align:center"><label style="font-size:0.72rem;color:var(--text-muted)">${a}</label><input id="s-${a.toLowerCase()}-dm" type="number" style="width:100%;padding:0.15rem;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);font-size:0.75rem;text-align:center" value="10"></div>`).join('')}
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
        return '<div style="text-align:center"><div style="font-size:0.72rem;color:#999;text-transform:uppercase">' + s.slice(0,3) + '</div><div style="font-weight:700">' + val + '</div><div style="font-size:0.7rem;color:' + (mod>=0?'#4ecca3':'#e94560') + '">' + modStr + '</div></div>';
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
    '<div style="text-align:center"><div style="font-size:0.72rem;color:#999">Armor Class</div><div style="font-weight:700;font-size:1.1rem">', (p.ac || '?'), '</div></div>',
    '<div style="text-align:center"><div style="font-size:0.72rem;color:#999">Hit Points</div><div style="font-weight:700;font-size:1.1rem">', p.hp_current, '/', p.hp_max, '</div><div style="width:80px;height:5px;border-radius:3px;background:#333;overflow:hidden;margin:0.15rem auto 0"><div style="height:100%;border-radius:3px;background:', hpColor, ';width:', hpPct, '%"></div></div></div>',
    speed ? '<div style="text-align:center"><div style="font-size:0.72rem;color:#999">Speed</div><div style="font-size:0.85rem">' + speed + '</div></div>' : '',
    '<div style="text-align:center"><div style="font-size:0.72rem;color:#999">Initiative</div><div style="font-size:0.85rem">🎲 ', (p.initiative || 0), '</div></div>',
    '<div style="text-align:center"><div style="font-size:0.72rem;color:#999">Status</div><div style="font-size:0.85rem">', (p.defeated ? '💀 Defeated' : '✅ Active'), '</div></div>',
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
    return `<div style="text-align:center"><div style="font-size:0.72rem;color:#999;text-transform:uppercase">${s.slice(0,3)}</div><div style="font-weight:700">${val}</div><div style="font-size:0.7rem;color:${mod>=0?'#4ecca3':'#e94560'}">${modStr}</div></div>`;
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
        <div style="text-align:center"><div style="font-size:0.72rem;color:#999">Armor Class</div><div style="font-weight:700;font-size:1.1rem">${combatant.ac || '?'}</div></div>
        <div style="text-align:center"><div style="font-size:0.72rem;color:#999">Hit Points</div><div style="font-weight:700;font-size:1.1rem">${combatant.hp_current}/${combatant.hp_max}</div><div style="width:80px;height:5px;border-radius:3px;background:#333;overflow:hidden;margin:0.15rem auto 0"><div style="height:100%;border-radius:3px;background:${hpColor};width:${hpPct}%"></div></div></div>
        <div style="text-align:center"><div style="font-size:0.72rem;color:#999">Speed</div><div style="font-size:0.85rem">${speeds}</div></div>
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

function removeBenchedCombatant(enId) {
  const idx = _benchedNpcs.findIndex(p => p.en_id === enId);
  if (idx === -1) return;
  _benchedNpcs.splice(idx, 1);
  // Delete from DB
  fetch(`/api/dm/encounter/${_combatEncId}/remove-npc`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({en_id: enId})
  }).catch(() => {});
  _combatOrder = _combatOrder.filter(id => id !== enId);
  renderBenchedNpcs();
  renderInitiativeTrack();
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
    const badge = p.is_enemy ? '<span class="badge badge-accent" style="font-size:0.72rem">ENEMY</span>'
      : '<span class="badge badge-muted" style="font-size:0.72rem">ALLY</span>';
    return `<div class="initiative-card benched-card" draggable="true"
      data-en-id="${p.en_id}"
      ondragstart="benchedDragStart(event, ${p.en_id})"
      ondragend="benchedDragEnd(event)"
      style="cursor:grab;opacity:0.7;border-style:dashed">
      <div class="init-turn-marker" style="background:var(--text-muted);color:var(--bg)">⏸</div>
      <div class="init-info" style="flex:1">
        <div class="init-name">${dmEsc(p.name || '')} ${badge} <button class="init-btn" data-en-id="${Number(p.en_id)}" title="View details" style="font-size:0.65rem;padding:0 0.2rem">📋</button></div>
        <div class="init-meta">AC ${p.ac} · HP ${p.hp_current}/${p.hp_max}</div>
      </div>
      <button class="init-btn" onclick="event.stopPropagation();unbenchCombatant(${p.en_id})" title="Return to combat" style="color:var(--success)">▶</button>
      <button class="init-btn" onclick="event.stopPropagation();removeBenchedCombatant(${p.en_id})" title="Remove permanently" style="color:var(--danger);font-size:0.7rem">✕</button>
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
  const showCharacterFilters = kind === 'all' || kind === 'character';
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
  if (window.SourceFilter) {
    const src = SourceFilter.slugs('dm-combat');
    if (src.length) {
      filtered = filtered.filter(c => c._kind !== 'monster'
        || SourceFilter.matches((c._raw && c._raw.source) || c.source || '', src));
    }
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
      ? '<span class="badge badge-accent" style="font-size:0.68rem">MON</span>'
      : c._kind === 'character' ? '<span class="badge badge-success" style="font-size:0.68rem">PC</span>'
      : c.id < 0 ? '<span class="badge badge-muted" style="font-size:0.68rem">📖</span>' : '';
    let detailDisplay, crDisplay, typeDisplay, sourceHtml, hpDisplay, infoBtn;

    if (c._kind === 'character') {
      const subStr = c.subclass ? ` ${c.subclass}` : '';
      detailDisplay = `${c.race || ''} L${c.level} ${c.class_name || ''}${subStr} · AC ${c.ac}`;
      hpDisplay = `HP ${c.hp_current}/${c.hp_max}`;
      sourceHtml = '';
      infoBtn = `<button class="btn btn-outline btn-sm" data-char-action="previewCharSheet" data-char-id="${Number(c.char_id)}" data-char-name="${dmEsc(c.name || '')}" title="View character sheet" style="font-size:0.72rem;padding:0.1rem 0.3rem;flex-shrink:0">ℹ️</button>`;
    } else if (c._kind === 'monster') {
      crDisplay = `CR ${c.level} · `;
      typeDisplay = c._type ? `${c._type.charAt(0).toUpperCase() + c._type.slice(1)} · ` : '';
      detailDisplay = `${crDisplay}${typeDisplay}AC ${c.ac} · ${c.race || '?'}`;
      hpDisplay = `HP ${c.hp_current}`;
      sourceHtml = c._raw?.source
        ? ` <span class="src-badge" data-src="${dmEsc(c._raw.source)}" style="font-size:0.68rem;cursor:pointer;opacity:0.7" title="Click to open ${dmEsc(c._raw.source)}">📚</span>`
        : '';
      infoBtn = c._raw?.index
        ? `<button class="btn btn-outline btn-sm" data-monster-action="showMonster" data-monster-index="${dmEsc(c._raw.index)}" title="Monster details" style="font-size:0.72rem;padding:0.1rem 0.3rem;flex-shrink:0">ℹ️</button>`
        : '';
    } else {
      // NPC
      detailDisplay = `${c.race || ''}${c.class_name ? ' L' + c.level + ' ' + c.class_name : ''} · AC ${c.ac}`;
      hpDisplay = `HP ${c.hp_current}/${c.hp_max}`;
      sourceHtml = '';
      infoBtn = c.id > 0
        ? `<button class="btn btn-outline btn-sm" data-npc-action="showNpcInfo" data-npc-id="${Number(c.id)}" data-npc-name="${dmEsc(c.name || '')}" title="NPC details" style="font-size:0.72rem;padding:0.1rem 0.3rem;flex-shrink:0">ℹ️</button>`
        : '';
    }
    html += `<div style="display:flex;align-items:center;gap:0.3rem;padding:0.3rem 0.5rem;background:var(--bg);border-radius:4px"
      data-idx="${_combatCreatureCache.indexOf(c)}">
      ${c._kind === 'character' ? charPortraitTile(c.char_id, c.name, {size: 26, hasPortrait: c.has_portrait}) : ''}
      ${c._kind === 'npc' ? charPortraitTile(c.id, c.name, {size: 26, kind: 'npc', hasPortrait: c.has_portrait}) : ''}
      ${c._kind === 'monster' ? charPortraitTile(null, c.name, {size: 26, src: '/api/ref-image/creature/' + encodeURIComponent(c.name) + '?size=52'}) : ''}
      ${infoBtn}
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
    // Monster, manual NPC, or Character — use add-creature
    const isCharacter = c._kind === 'character';
    try {
      const body = {
        name: c.name, race: c.race || '', class_name: c.class_name || '',
        level: isCharacter ? c.level : (c._kind === 'monster' ? 1 : (c.level || 1)),
        hp: c.hp_current || 10, hp_max: c.hp_max || 10, ac: c.ac || 10,
        is_enemy: isCharacter ? 0 : (c.is_enemy || 0),
        role: isCharacter ? `L${c.level} ${c.class_name}${c.subclass ? ' ('+c.subclass+')' : ''}` : (c.role || ''),
        xp_reward: c.xp_reward || 0,
        _monster_index: c._kind === 'monster' ? (c._raw?.index || '') : '',
        char_id: isCharacter ? c.char_id : undefined,
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
  // Save conditions for ALL participants (incl. benched) so they persist on reload
  const conditions = {};
  _combatParticipants.forEach(p => {
    if (p.conditions && p.conditions.length > 0) conditions[p.en_id] = p.conditions;
  });
  _benchedNpcs.forEach(p => {
    if (p.conditions && p.conditions.length > 0) conditions[p.en_id] = p.conditions;
  });
  fetch(`/api/dm/encounter/${_combatEncId}/combat-state`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      action: 'save', round: _combatRound, turn_index: _combatTurnIdx,
      initiative_order: _combatOrder,
      benched_en_ids: _benchedNpcs.map(p => p.en_id),
      player_participants: players,
      campaign_id: _combatCampId || null,
      participant_conditions: conditions
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

