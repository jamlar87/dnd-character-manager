/* sheet/summons.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
const SUMMON_TEMPLATES = SHEETREF.summonTemplates;
let characterSummons = SHEETCFG.charSummons;
let _lastMonsterData = null;   // captured from monster API during fill
let _lastTemplateKey = null;   // template key for addSummon to reference

function toggleSummonBody(chip) {
  const body = chip.querySelector('.summon-body');
  if (body) body.style.display = body.style.display === 'none' ? 'block' : 'none';
}

async function saveSummons() {
  await fetch(`/api/character/${CHAR_ID}/update`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({summons: JSON.stringify(characterSummons)}),
  });
}

async function applySummonHP(idx, sign) {
  const deltaEl = document.getElementById('summon-hp-delta-' + idx);
  const raw = parseInt(deltaEl.value) || 1;
  const delta = sign === -1 ? -raw : raw;
  let hp = characterSummons[idx].hp_current + delta;
  hp = Math.max(0, hp);  // floor at 0, allow over max
  characterSummons[idx].hp_current = hp;

  // Update DOM — bar fill + hp text on the collapsed chip
  const chip = document.querySelector(`.summon-chip[data-summon-id="${idx}"]`);
  if (chip) {
    const barFill = chip.querySelector('.summon-bar-fill');
    const hpText = chip.querySelector('.summon-hp-text');
    if (barFill && characterSummons[idx].hp_max) {
      const pct = hp / characterSummons[idx].hp_max * 100;
      barFill.style.width = pct + '%';
      barFill.style.background = pct > 50 ? 'var(--success)' : pct > 25 ? 'var(--warn)' : 'var(--danger)';
    }
    if (hpText) hpText.textContent = hp + '/' + characterSummons[idx].hp_max;
  }

  await saveSummons();
}

async function resetSummonHP(idx) {
  const hp = characterSummons[idx].hp_max;
  characterSummons[idx].hp_current = hp;

  const chip = document.querySelector(`.summon-chip[data-summon-id="${idx}"]`);
  if (chip) {
    const barFill = chip.querySelector('.summon-bar-fill');
    const hpText = chip.querySelector('.summon-hp-text');
    if (barFill) { barFill.style.width = '100%'; barFill.style.background = 'var(--success)'; }
    if (hpText) hpText.textContent = hp + '/' + hp;
  }

  await saveSummons();
}

async function saveSummonName(idx) {
  const chip = document.querySelector(`.summon-chip[data-summon-id="${idx}"]`);
  if (!chip) return;
  const mainInput = chip.querySelector('.summon-edit-main-name');
  const nameInput = chip.querySelector('.summon-edit-name');
  if (!mainInput || !nameInput) return;

  characterSummons[idx].main_name = mainInput.value;
  characterSummons[idx].name = nameInput.value || 'Unnamed';

  // Update chip header display
  const headerSpan = chip.querySelector('div:first-child > span:first-child');
  if (headerSpan) {
    const mn = characterSummons[idx].main_name;
    const nm = characterSummons[idx].name;
    if (mn && nm) {
      headerSpan.innerHTML = mn + ` <span style="color:var(--text-muted);font-weight:400;font-size:0.65rem">(` + nm + `)</span>`;
    } else {
      headerSpan.textContent = mn || nm;
    }
  }

  await saveSummons();
}

async function dismissSummon(idx) {
  const label = characterSummons[idx].main_name || characterSummons[idx].name;
  if (!confirm(`Dismiss ${label}?`)) return;
  characterSummons.splice(idx, 1);
  await saveSummons();
  location.reload();
}

function openSummonModal() {
  let html = `<div class="modal-overlay" id="summonModal" onclick="if(event.target===this)closeSummonModal()">
    <div class="modal-content" style="max-width:600px">
      <button class="modal-close" onclick="closeSummonModal()">✕</button>
      <h2 style="margin:0 0 1rem 0">Add Summon</h2>
      <div style="margin-bottom:0.75rem">
        <label style="font-size:0.75rem;color:var(--text-muted);display:block;margin-bottom:0.25rem">Quick Template</label>
        <select id="summon-template" onchange="fillSummonTemplate(this.value)" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem">
          <option value="">— Custom —</option>`;

  const categories = {
    'pact_chain': '🔗 Pact of the Chain',
    'familiar': '🐾 Find Familiar',
    'druid_wildshape': '🍃 Druid Wild Shape',
    'spell_summon': '⚡ Spell Summons',
    'tashas_summon': '✨ Tasha Summons',
    'class_feature': '🎯 Class Features',
    'mount': '🐴 Mounts',
    'ranger_companion': '🏹 Beast Master Companions',
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
      <div id="summon-spell-level-row" style="display:none;margin-bottom:0.75rem">
        <label style="font-size:0.7rem;color:var(--text-muted)">Spell Level</label>
        <input id="s-spell-level" type="number" min="1" max="9" value="3" style="width:80px;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem;margin-left:0.5rem"
          onchange="fillSummonTemplate(document.getElementById('summon-template').value)">
        <span id="summon-hp-calc" style="font-size:0.65rem;color:var(--text-muted);margin-left:0.5rem"></span>
      </div>
      <div style="margin-bottom:0.5rem">
        <label style="font-size:0.7rem;color:var(--text-muted);display:block;margin-bottom:0.15rem">Main Name <span style="font-size:0.72rem">(e.g. "Stolas")</span></label>
        <input id="s-main-name" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="">
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem">
        <div><label style="font-size:0.7rem">Name <span style="color:var(--text-muted)">(creature type)</span></label><input id="s-name" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="Imp"></div>
        <div><label style="font-size:0.7rem">Form/Type</label><input id="s-form" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="owl"></div>
        <div><label style="font-size:0.7rem">AC</label><input id="s-ac" type="number" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="10"></div>
        <div><label style="font-size:0.7rem">HP</label><input id="s-hp" type="number" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="1"></div>
        <div><label style="font-size:0.7rem">Size</label><input id="s-size" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="Tiny"></div>
        <div><label style="font-size:0.7rem">Speed</label><input id="s-speed" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" value="30 ft."></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:0.3rem;margin-top:0.5rem">
        ${['STR','DEX','CON','INT','WIS','CHA'].map(a => `<div style="text-align:center"><label style="font-size:0.72rem;color:var(--text-muted)">${a}</label><input id="s-${a.toLowerCase()}" type="number" style="width:100%;padding:0.15rem;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);font-size:0.75rem;text-align:center" value="10"></div>`).join('')}
      </div>
      <div style="margin-top:0.5rem">
        <label style="font-size:0.7rem">Source (spell/feature)</label>
        <input id="s-source" style="width:100%;padding:0.3rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.8rem" placeholder="find_familiar">
      </div>
      <div style="display:flex;gap:0.5rem;margin-top:1rem">
        <button class="btn btn-primary" onclick="addSummon()" style="flex:1">Add Summon</button>
        <button class="btn btn-outline" onclick="closeSummonModal()">Cancel</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  requestAnimationFrame(() => {
    const modal = document.getElementById('summonModal');
    if (modal) modal.classList.add('open');
  });
}

function closeSummonModal() {
  const modal = document.getElementById('summonModal');
  if (modal) {
    modal.classList.remove('open');
    setTimeout(() => modal.remove(), 200);
  }
}

// ── Dynamic summon stat fill — fetches from monster DB or calculates ──
async function fillSummonTemplate(key) {
  if (!key || !SUMMON_TEMPLATES[key]) {
    // Custom — hide spell level, clear fields
    const slRow = document.getElementById('summon-spell-level-row');
    if (slRow) slRow.style.display = 'none';
    return;
  }
  const t = SUMMON_TEMPLATES[key];

  // Name + form — preserve main_name if already set
  if (!document.getElementById('s-main-name').value) {
    document.getElementById('s-main-name').value = t.name;
  }
  document.getElementById('s-name').value = t.name;
  document.getElementById('s-form').value = '';
  document.getElementById('s-size').value = t.size || 'Medium';
  document.getElementById('s-speed').value = t.speed || '30 ft.';
  document.getElementById('s-source').value = t.source || '';

  // Stats — default
  for (const ab of ['str','dex','con','int','wis','cha']) {
    const el = document.getElementById('s-' + ab);
    if (el) el.value = (t.stats && t.stats[ab]) ? t.stats[ab] : 10;
  }

  // ── Monster DB lookup (familiars + chain) ──
  if (t.monster_index) {
    try {
      const r = await fetch(`/api/dm/monster/${t.monster_index}`);
      if (r.ok) {
        const m = await r.json();
        // AC
        let ac = 10;
        if (Array.isArray(m.armor_class) && m.armor_class.length) ac = m.armor_class[0].value || 10;
        else if (typeof m.armor_class === 'number') ac = m.armor_class;
        document.getElementById('s-ac').value = ac;
        // HP
        document.getElementById('s-hp').value = m.hit_points || 1;
        // Stats
        const statMap = {'strength':'str','dexterity':'dex','constitution':'con',
                         'intelligence':'int','wisdom':'wis','charisma':'cha'};
        for (const [src, dst] of Object.entries(statMap)) {
          const el = document.getElementById('s-' + dst);
          if (el && m[src] != null) el.value = m[src];
        }
        // Speed
        if (m.speed && typeof m.speed === 'object') {
          const parts = [];
          for (const [k,v] of Object.entries(m.speed)) {
            parts.push(v.replace(' ft.',' ft'));
          }
          document.getElementById('s-speed').value = parts.join(', ') || t.speed || '30 ft.';
        }
        // ── Capture full monster data for rich summon display ──
        _lastMonsterData = {
          actions: (m.actions || []).map(a => ({
            name: a.name,
            desc: a.desc || '',
            attack_bonus: a.attack_bonus,
            damage: a.damage || []
          })),
          special_abilities: (m.special_abilities || []).map(sa => ({
            name: sa.name,
            desc: sa.desc || ''
          })),
          skills: m.skills || {},
          senses: m.senses || {},
          languages: m.languages || '',
          damage_resistances: m.damage_resistances || [],
          damage_immunities: m.damage_immunities || [],
          condition_immunities: (m.condition_immunities || []).map(c => typeof c === 'string' ? c : c.name || ''),
          proficiencies: (m.proficiencies || []).map(p => ({
            name: (p.proficiency?.name || '').replace('Skill: ',''),
            value: p.value
          }))
        };
        _lastTemplateKey = key;
      } else {
        _lastMonsterData = null;
      }
    } catch(e) {
      console.warn('Monster fetch failed, using template defaults:', e);
      _lastMonsterData = null;
    }
    const slRow = document.getElementById('summon-spell-level-row');
    if (slRow) slRow.style.display = 'none';
    return;
  }

  // ── Spell-based summons — clear monster data, set template key ──
  if (t.spell_base_level != null) {
    _lastMonsterData = null;
    _lastTemplateKey = key;
    const slRow = document.getElementById('summon-spell-level-row');
    const slInput = document.getElementById('s-spell-level');
    if (slRow) slRow.style.display = 'block';

    // Determine effective spell level
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

    // AC
    const ac = (t.ac_base || 10) + (t.ac_scaling || 0) * (spellLvl || 0);
    document.getElementById('s-ac').value = ac;

    // HP (class features use hp_per_level × character level)
    let hp;
    if (t.hp_per_level) {
      hp = (t.hp_base || 0) + (t.hp_per_level || 0) * CHAR_LEVEL;
    } else {
      hp = (t.hp_base || 0) + (t.hp_scaling || 0) * aboveBase;
    }
    document.getElementById('s-hp').value = hp;

    // HP calculation label
    const hpLabel = document.getElementById('summon-hp-calc');
    if (hpLabel) {
      if (t.hp_per_level) {
        hpLabel.textContent = `${t.hp_base} + ${t.hp_per_level}×Lv${CHAR_LEVEL} = ${hp} HP`;
      } else if (aboveBase > 0) {
        hpLabel.textContent = `${t.hp_base} + ${t.hp_scaling}×${aboveBase} = ${hp} HP`;
      } else {
        hpLabel.textContent = `${t.hp_base} HP (base)`;
      }
    }

    // If spell level is higher than base, don't show extra field — integrated in calculation
    return;
  }

  // Fallback: template has hardcoded ac/hp (or ac_base/hp_base — vehicles etc)
  if (t.ac) document.getElementById('s-ac').value = t.ac;
  else if (t.ac_base != null) document.getElementById('s-ac').value = t.ac_base;
  if (t.hp_max) document.getElementById('s-hp').value = t.hp_max;
  else if (t.hp_base != null) document.getElementById('s-hp').value = t.hp_base;
  if (t.hp_note) document.getElementById('s-hp').dataset.note = t.hp_note;
  const slRow = document.getElementById('summon-spell-level-row');
  if (slRow) slRow.style.display = 'none';
}

function addSummon() {
  const s = {
    id: 'summon_' + Date.now(),
    main_name: document.getElementById('s-main-name').value,
    name: document.getElementById('s-name').value || 'Unnamed',
    form: document.getElementById('s-form').value,
    category: 'custom',
    source: document.getElementById('s-source').value || 'custom',
    ac: parseInt(document.getElementById('s-ac').value) || 10,
    hp_max: parseInt(document.getElementById('s-hp').value) || 1,
    hp_current: parseInt(document.getElementById('s-hp').value) || 1,
    size: document.getElementById('s-size').value || 'Medium',
    speed: document.getElementById('s-speed').value || '30 ft.',
    stats: {},
    features: [],
    attacks: []
  };
  for (const ab of ['str','dex','con','int','wis','cha']) {
    s.stats[ab] = parseInt(document.getElementById('s-' + ab).value) || 10;
  }

  const key = document.getElementById('summon-template').value;
  if (key && SUMMON_TEMPLATES[key]) {
    const t = SUMMON_TEMPLATES[key];
    s.category = t.category;

    // ── Monster-based: build rich attacks + features from API data ──
    if (_lastMonsterData && key === _lastTemplateKey) {
      const md = _lastMonsterData;

      // Build attacks with full descriptions
      s.attacks = md.actions.map(a => {
        let dmg = '';
        if (a.damage && a.damage.length) {
          dmg = a.damage.map(d => `${d.damage_dice || ''} ${d.damage_type?.name || ''}`.trim()).join(' + ');
        }
        return {
          name: a.name,
          bonus: a.attack_bonus,
          damage: dmg,
          desc: a.desc || ''
        };
      });

      // Build features list: names for chip display, plus full feature_descs object
      s.features = md.special_abilities.map(sa => sa.name);
      s.feature_descs = {};
      for (const sa of md.special_abilities) {
        s.feature_descs[sa.name] = sa.desc || '';
      }

      // Skills from proficiencies
      if (md.proficiencies && md.proficiencies.length) {
        s.skills = md.proficiencies.map(p => `${p.name} +${p.value}`).join(', ');
      } else if (md.skills && Object.keys(md.skills).length) {
        s.skills = Object.entries(md.skills).map(([k,v]) => `${k} +${v}`).join(', ');
      }

      // Senses
      if (md.senses && Object.keys(md.senses).length) {
        const senseParts = [];
        for (const [k,v] of Object.entries(md.senses)) {
          senseParts.push(`${k.replace('_',' ')} ${v}`);
        }
        s.senses = senseParts.join(', ');
      }

      // Languages
      if (md.languages) s.languages = md.languages;

      // Resistances / immunities
      if (md.damage_resistances && md.damage_resistances.length) {
        s.resistances = md.damage_resistances.join(', ');
      }
      if (md.damage_immunities && md.damage_immunities.length) {
        s.immunities = md.damage_immunities.join(', ');
      }
      if (md.condition_immunities && md.condition_immunities.length) {
        s.condition_immunities = md.condition_immunities.join(', ');
      }

      s.hp_note = t.hp_note || '';

    } else {
      // ── Template-based (Tasha summons / class features) ──
      s.features = [...(t.features || [])];
      s.feature_descs = {...(t.feature_descs || {})};
      s.skills = t.skills || '';
      s.senses = t.senses || '';
      s.hp_note = t.hp_note || '';

      if (t.attacks) {
        s.attacks = t.attacks.map(a => {
          let dmg = a.damage_base || a.damage || '1d4';
          let desc = a.desc || '';
          if (t.spell_base_level != null && a.damage_scaling) {
            const slInput = document.getElementById('s-spell-level');
            const spellLvl = slInput ? Math.max(t.spell_base_level, parseInt(slInput.value) || t.spell_base_level) : t.spell_base_level;
            const aboveBase = Math.max(0, spellLvl - t.spell_base_level);
            const scaled = parseInt(a.damage_scaling) * aboveBase;
            if (scaled > 0) dmg += `+${scaled}`;
          }
          let bonus = a.bonus;
          if (t.spell_base_level != null && bonus == null) {
            const slInput = document.getElementById('s-spell-level');
            const spellLvl = slInput ? Math.max(t.spell_base_level, parseInt(slInput.value) || t.spell_base_level) : t.spell_base_level;
            bonus = (t.atk_bonus_base || 0) + (t.atk_bonus_scaling || 0) * (spellLvl || 0);
          }
          return {name: a.name, bonus: bonus, damage: dmg, desc: desc};
        });
      }
    }
    s.form = s.form || t.form || '';
  }

  // Clear captured data
  _lastMonsterData = null;
  _lastTemplateKey = null;

  characterSummons.push(s);
  saveSummons().then(() => location.reload());
}


