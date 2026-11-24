/* Character sheet behaviour — extracted from templates/_sheet_scripts.html.
 *
 * Per-character values live in the page-scoped SHEETCFG object that
 * _sheet_scripts.html renders inline (data must not be cached); this file is
 * the code and is served as a cacheable static asset.
 *
 * Regenerate with the extraction steps in SKILL.md Pitfall 25 — do NOT
 * hand-edit the config object's expectation: every value referenced here must
 * be rendered by the template's config block.
 */

const CHAR_ID = SHEETCFG.charId;
const CHAR_LEVEL = SHEETCFG.charLevel;
const FEAT_SETUP = {
  elemental_adept: {label:'Choose a damage type',field:'type',choices:['Acid','Cold','Fire','Lightning','Thunder']},
  ley_initiate: {label:'Choose an ability to increase',field:'ability',choices:['Intelligence','Wisdom']},
  martial_adept: {label:'Choose 2 Battle Master maneuvers',field:'maneuvers',kind:'maneuvers',picks:2},
};
const MANEUVER_OPTIONS = SHEETREF.maneuverOptions;
const CHAR_ASI_HISTORY = SHEETCFG.charAsiHistory;
const KNOWN_FEATS = SHEETREF.knownFeats;
const FEAT_DETAILS = SHEETREF.featDetails;
const HP_MAX = SHEETCFG.hpMax;
const HD_TOTAL = SHEETCFG.hdTotal;
const ATTUNED_ITEMS = SHEETCFG.attunedItems;
const ATTUNEMENT_SLOTS_MAX = 3;
const ITEM_NEEDS_ATTUNEMENT = SHEETCFG.itemNeedsAttunement;
const CAMPAIGN_ID = SHEETCFG.campaignId;
const HD_DIE = SHEETCFG.hdDie;
const HD_USED = SHEETCFG.hdUsed;
const CON_MOD = SHEETCFG.conMod;
const PREPARED_MAX = SHEETCFG.preparedMax;
let _sheetPollTimer = null;
let _lastConditions = null;
const CASTER_TYPE = SHEETCFG.casterType;
let CHARACTER_GP = SHEETCFG.charGp;

// ── Source reference: click 📚 badge → open PDF ──
const SOURCE_SLUG_MAP = SHEETREF.sourceSlugMap;
function _srcBadge(src) {
  if (!src) return '';
  // Keep the full source text including page number for accuracy
  const short = src.length > 30 ? src.replace(/\s+\d{4}$/, '').trim() : src;
  return `<span class="src-badge" onclick="openSourceRef(this.dataset.src)" data-src="${escapeAttr(src)}" title="Click to open ${escapeAttr(src)}">📚 ${escapeHtml(short)}</span>`;
}
// openSourceRef() lives in static/ui/source_ref.js; the slug map comes from SHEETREF.


// ── Ability score recalculation data ───────────────────────────────────
const SKILL_AB_MAP = SHEETREF.skillAbMap;
const CHAR_SAVE_PROFS = SHEETCFG.charSaveProfs;
const CHAR_SKILLS = SHEETCFG.charSkills;
const CHAR_EXPERTISE = SHEETCFG.charExpertise;
const CHAR_CLASS_NAME = SHEETCFG.charClassName;
const CHAR_SUBCLASS = SHEETCFG.charSubclass;
const EXPERTISE_OPTIONS = SHEETCFG.expertiseOptions;
const EXPERTISE_COUNT = SHEETCFG.expertiseCount;
const CHAR_PROF_BONUS = SHEETCFG.charProfBonusOrDefault;
// Keep a client-side cache of ability scores for recalculation
const ABILITY_SCORES = {
  strength: SHEETCFG.strScore,
  dexterity: SHEETCFG.dexScore,
  constitution: SHEETCFG.conScore,
  intelligence: SHEETCFG.intScore,
  wisdom: SHEETCFG.wisScore,
  charisma: SHEETCFG.chaScore
};

// ── Ability Score Editor Popover ───────────────────────────────────────
function showAbilityEditor(statKey, statLabel, currentScore) {
  if (!document.body.classList.contains('edit-mode')) return;

  // Remove any existing editor
  document.querySelectorAll('.ability-editor-overlay').forEach(o => o.remove());

  let score = parseInt(currentScore) || 10;

  const overlay = document.createElement('div');
  overlay.className = 'ability-editor-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const card = document.createElement('div');
  card.className = 'ability-editor-card';

  function render() {
    const mod = Math.floor((score - 10) / 2);
    const modStr = mod >= 0 ? '+' + mod : '' + mod;
    let modClass = 'zero';
    if (mod > 0) modClass = 'positive';
    else if (mod < 0) modClass = 'negative';
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

    // Score display — click to type directly
    card.querySelector('#ae-score-text').addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'number';
      input.min = 1; input.max = 30;
      input.className = 'ae-score-input';
      input.value = score;
      const display = card.querySelector('#ae-score-text');
      display.replaceWith(input);
      input.focus();
      input.select();
      input.addEventListener('blur', () => {
        score = Math.max(1, Math.min(30, parseInt(input.value) || score));
        render();
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
      });
    });

    card.querySelector('#ae-minus').onclick = () => { score = Math.max(1, score - 1); render(); };
    card.querySelector('#ae-plus').onclick = () => { score = Math.min(30, score + 1); render(); };
    card.querySelector('.ae-cancel').onclick = () => overlay.remove();
    card.querySelector('.ae-save').onclick = async () => {
      // Update DOM
      const abEl = document.querySelector(`.ab-score[data-ability="${statKey}"]`);
      if (abEl) abEl.textContent = score;
      // Update modifier display in ability box
      const abBox = abEl ? abEl.closest('.ability-box') : null;
      if (abBox) {
        const modEl = abBox.querySelector('.ab-mod');
        const newMod = Math.floor((score - 10) / 2);
        if (modEl) modEl.textContent = (newMod >= 0 ? '+' : '') + newMod;
      }
      // Update client cache + recalc
      ABILITY_SCORES[statKey] = score;
      recalcAllDerived();
      // Persist to server
      try { await sheetApi('/update', { [statKey]: score }); } catch(e) { console.error(e); }
      overlay.remove();
    };
  }

  render();
  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

// ── Recalculate all values that depend on ability scores ────────────────
function recalcAllDerived() {
  const scores = ABILITY_SCORES;
  const abMod = (ab) => Math.floor((scores[ab] - 10) / 2);
  const prof = CHAR_PROF_BONUS;

  // Update saving throws in sidebar
  document.querySelectorAll('.save-row').forEach(row => {
    const statDot = row.querySelector('.save-dot');
    if (!statDot) return;
    const stat = statDot.dataset.stat;
    if (!stat || !(stat in scores)) return;
    const mod = abMod(stat);
    const hasProf = statDot.classList.contains('active');
    const val = mod + (hasProf ? prof : 0);
    const valEl = row.querySelector('.stat-val');
    if (valEl) {
      valEl.textContent = (val >= 0 ? '+' : '') + val;
      valEl.style.color = hasProf ? 'var(--accent)' : 'var(--text-muted)';
    }
  });

  // Update skills in sidebar
  document.querySelectorAll('.stat-row .skill-dot').forEach(dot => {
    const skillName = dot.dataset.skill;
    const ability = SKILL_AB_MAP[skillName];
    if (!ability || !(ability in scores)) return;
    const mod = abMod(ability);
    const hasProf = dot.classList.contains('active');
    const val = mod + (hasProf ? prof : 0);
    const row = dot.closest('.stat-row');
    if (row) {
      const valEl = row.querySelector('.stat-val');
      if (valEl) {
        valEl.textContent = (val >= 0 ? '+' : '') + val;
        valEl.style.color = hasProf ? 'var(--accent)' : 'var(--text-muted)';
      }
    }
  });

  // Update passive perception
  const ppEl = document.querySelector('.sense-val.editable[data-field="passive_perception"]');
  if (ppEl) {
    const wisMod = abMod('wisdom');
    const hasPercProf = CHAR_SKILLS.includes('Perception');
    ppEl.textContent = 10 + wisMod + (hasPercProf ? prof : 0);
  }

  // Update passive investigation (non-editable span)
  const piRow = [...document.querySelectorAll('.sense-row')].find(r => r.textContent.includes('Passive Investigation'));
  if (piRow) {
    const piVal = piRow.querySelector('.sense-val');
    if (piVal) {
      const intMod = abMod('intelligence');
      const hasInvProf = CHAR_SKILLS.includes('Investigation');
      piVal.textContent = 10 + intMod + (hasInvProf ? prof : 0);
    }
  }

  // Update passive insight
  const pInsRow = [...document.querySelectorAll('.sense-row')].find(r => r.textContent.includes('Passive Insight'));
  if (pInsRow) {
    const pInsVal = pInsRow.querySelector('.sense-val');
    if (pInsVal) {
      const wisMod = abMod('wisdom');
      const hasInsProf = CHAR_SKILLS.includes('Insight');
      pInsVal.textContent = 10 + wisMod + (hasInsProf ? prof : 0);
    }
  }
}

// ── Wire up ability score clicks to the popover editor ──────────────────
function initAbilityEditors() {
  document.querySelectorAll('.ab-score[data-ability]').forEach(el => {
    el.addEventListener('click', () => {
      if (!document.body.classList.contains('edit-mode')) return;
      showAbilityEditor(el.dataset.ability, el.dataset.label, parseInt(el.textContent) || 10);
    });
    // Visual cue: ability scores are clickable in edit mode
    el.style.cursor = 'default';
    el.title = 'Click to edit (unlock with 🔒 Edit first)';
  });
  // Update cursor in edit mode
  const observer = new MutationObserver(() => {
    const isEdit = document.body.classList.contains('edit-mode');
    document.querySelectorAll('.ab-score[data-ability]').forEach(el => {
      el.style.cursor = isEdit ? 'pointer' : 'default';
    });
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}

document.addEventListener('DOMContentLoaded', () => { initAbilityEditors(); initEditable(); tagWeapons(); startSheetPolling(); initSheetRollFeed(); refreshSheetUndo(); if (!_conditionsExpanded) { document.getElementById('condition-badges').style.display = 'none'; }
  // Init core toggle UI if persistent state is on
  if (_coreOnly) {
    const spellBox = document.getElementById('core-toggle-box');
    const spellLabel = document.getElementById('core-toggle-label');
    if (spellBox) spellBox.textContent = '☑';
    if (spellLabel) spellLabel.style.color = 'var(--accent)';
    // Inventory toggle
    document.querySelectorAll('#tab-inventory label[onclick] span').forEach(s => s.textContent = '☑');
    document.querySelectorAll('#tab-inventory label[onclick]').forEach(l => l.style.color = 'var(--accent)');
  }
});

// Auto-open level-up wizard if ?level-up=N in URL (from character creation)
(function() {
  const params = new URLSearchParams(window.location.search);
  const luTarget = parseInt(params.get('level-up'));
  if (luTarget && luTarget > CHAR_LEVEL && luTarget <= 20) {
    // Clean URL
    const url = new URL(window.location);
    url.searchParams.delete('level-up');
    window.history.replaceState({}, '', url);
    // Wait for DOM then open wizard
    setTimeout(() => openLevelUpDirect(luTarget), 500);
  }
})();

// ── Proficiency Master Lists (PHB 2014) ──
const ALL_ARMOR_PROFS = [
  'Light armor', 'Medium armor', 'Heavy armor', 'Shields'
];
const ALL_WEAPON_PROFS = [
  'Simple weapons', 'Martial weapons',
  'Renaissance firearms', 'Modern firearms', 'Futuristic firearms',
  'Clubs', 'Daggers', 'Greatclubs', 'Handaxes', 'Javelins', 'Light hammers',
  'Maces', 'Quarterstaffs', 'Sickles', 'Spears', 'Light crossbows', 'Darts',
  'Shortbows', 'Slings',
  'Battleaxes', 'Flails', 'Glaives', 'Greataxes', 'Greatswords', 'Halberds',
  'Lances', 'Longswords', 'Mauls', 'Morningstars', 'Pikes', 'Rapiers',
  'Scimitars', 'Shortswords', 'Tridents', 'War picks', 'Warhammers', 'Whips',
  'Blowguns', 'Hand crossbows', 'Heavy crossbows', 'Longbows', 'Nets'
];
const ALL_TOOL_PROFS = [
  "Alchemist's supplies", "Brewer's supplies", "Calligrapher's supplies",
  "Carpenter's tools", "Cartographer's tools", "Cobbler's tools",
  "Cook's utensils", "Glassblower's tools", "Jeweler's tools",
  "Leatherworker's tools", "Mason's tools", "Painter's supplies",
  "Potter's tools", "Smith's tools", "Tinker's tools", "Weaver's tools",
  "Woodcarver's tools", "Navigator's tools", "Thieves' tools",
  "Disguise kit", "Forgery kit", "Herbalism kit", "Poisoner's kit",
  "Bagpipes", "Drum", "Dulcimer", "Flute", "Horn", "Lute", "Lyre",
  "Pan flute", "Shawm", "Viol",
  "Dice set", "Dragonchess set", "Playing card set", "Three-Dragon Ante set",
  "Land vehicles", "Water vehicles"
];
const ALL_LANGUAGES = [
  'Common', 'Dwarvish', 'Elvish', 'Giant', 'Gnomish', 'Goblin', 'Halfling',
  'Orc', 'Abyssal', 'Celestial', 'Draconic', 'Deep Speech', 'Infernal',
  'Primordial', 'Sylvan', 'Undercommon', 'Druidic', "Thieves' cant"
];

// ── Defense Master Lists (PHB 2014 p.196, p.290-292) ──
const ALL_DAMAGE_TYPES = [
  'Acid', 'Bludgeoning', 'Cold', 'Fire', 'Force',
  'Lightning', 'Necrotic', 'Piercing', 'Poison', 'Psychic',
  'Radiant', 'Slashing', 'Thunder'
];
const ALL_CONDITIONS = window.DND_CONDITIONS.names;

/* Every sheet write goes POST /api/character/<id>/<path>. This keeps that URL
   shape in one place and hands the request to the shared api() helper in
   static/ui/api.js (CSRF header, JSON body, throws with the server message). */
function sheetApi(path, data) {
  return api('/api/character/' + CHAR_ID + path, { method: 'POST', body: data });
}

async function saveAncestry(ancestry) {
  await sheetApi('/update', { dragonborn_ancestry: ancestry });
  location.reload();
}

// ── Tabs ──
function switchTab(name) {
  console.log('switchTab called:', name, 'at', Date.now());
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  // Use data-tab for robust matching (onclick attribute selector fragile)
  const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
  if (btn) btn.classList.add('active');
  const content = document.getElementById('tab-' + name);
  if (content) {
    content.classList.add('active');
    console.log('tab-' + name, 'display:', window.getComputedStyle(content).display);
  } else {
    console.warn('tab-content not found: tab-' + name);
  }
  localStorage.setItem('char-tab-' + CHAR_ID, name);
}
const savedTab = localStorage.getItem('char-tab-' + CHAR_ID);
console.log('savedTab:', savedTab, 'char-tab-' + CHAR_ID);
if (savedTab && ['actions','features','spells','inventory','history'].includes(savedTab)) {
  console.log('restoring saved tab:', savedTab);
  switchTab(savedTab);
}

// ── HP (updates both sidebar mini and bar) ──
async function applyHP(sign) {
  const deltaEl = document.getElementById('hp-delta');
  const raw = parseInt(deltaEl.value) || 1;
  const delta = sign === -1 ? -raw : raw;
  const curEl = document.getElementById('hp-cur');
  let hp = parseInt(curEl.textContent) + delta;
  // Can go above max, but floor at 0
  hp = Math.max(0, hp);
  curEl.textContent = hp;
  const pct = Math.min(hp, HP_MAX) / HP_MAX * 100;
  ['hp-bar-side'].forEach(id => {
    const bar = document.getElementById(id);
    if (bar) {
      bar.style.width = pct + '%';
      bar.classList.toggle('danger', hp > 0 && pct < 25);
      bar.classList.toggle('warn', hp > 0 && pct >= 25 && pct < 50);
    }
  });
  // Death Saves live inside the HP block and only matter at 0 HP
  syncDeathSaves(hp);
  await sheetApi('/update', { hp_current: hp });
}

// Show the death-save tracker only while the character is at 0 HP, and open it
// on the way down so the player can tick successes/failures immediately.
function syncDeathSaves(hp) {
  const block = document.getElementById('death-saves-block');
  if (!block) return;
  const down = hp <= 0;
  block.style.display = down ? '' : 'none';
  if (down) {
    const body = document.getElementById('ds-body');
    if (body) body.style.display = 'block';
    const tog = document.getElementById('ds-toggle');
    if (tog) tog.textContent = '▾';
  }
}

async function resetHP() {
  const curEl = document.getElementById('hp-cur');
  const hp = HP_MAX;
  curEl.textContent = hp;
  ['hp-bar-side'].forEach(id => {
    const bar = document.getElementById(id);
    if (bar) {
      bar.style.width = '100%';
      bar.classList.remove('danger', 'warn');
    }
  });
  syncDeathSaves(hp);
  await sheetApi('/update', { hp_current: hp });
}

// ── Export / Import JSON (backup + transfer) ──
async function exportCharacter() {
  try {
    const r = await fetch('/api/character/' + CHAR_ID + '/export');
    if (!r.ok) { alert('Export failed (' + r.status + ')'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="?([^";]+)"?/);
    a.download = m ? m[1] : 'character.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    alert('Export error: ' + e.message);
  }
}
async function importCharacterFile(file) {
  if (!file) return;
  const text = await file.text();
  let data;
  try { data = JSON.parse(text); } catch (e) { alert('Not valid JSON'); return; }
  if (!data.character) { alert('Not a character export file'); return; }
  if (!confirm('Import "' + (data.character.name || 'character') + '" as a new character?')) return;
  const r = await fetch('/api/character/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  const res = await r.json().catch(() => ({}));
  if (r.ok && res.id) {
    alert('Imported "' + res.name + '"');
    location.href = '/character/' + res.id;
  } else {
    alert('Import failed: ' + (res.error || r.status));
  }
}

// ── HP Sync Polling (sheet ↔ combat cross-sync) ──
// The table channel carries the character's own HP changes, so when its stream is open the
// timer is only insurance; without a stream (no campaign, or the feed is down) it goes back to
// being the mechanism. 2s and 30s here are a real trade: the fast poll costs a request every
// two seconds forever, which is what the stream exists to stop paying.
const SHEET_POLL_FAST_MS = 2000;
const SHEET_POLL_SAFE_MS = 30000;

async function syncSheetHP() {
  // Hidden tab: nothing to update on screen. The visibilitychange wire below refreshes on return.
  if (document.hidden) return;
  try {
    const resp = await fetch('/api/sync-combat-hp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({char_ids: [CHAR_ID]})
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const info = data.characters && data.characters[String(CHAR_ID)];
    if (!info) return;

    // ── Player HP ──
    const curEl = document.getElementById('hp-cur');
    if (curEl && info.hp_current !== parseInt(curEl.textContent)) {
      const hp = info.hp_current;
      curEl.textContent = hp;
      const pct = Math.min(hp, HP_MAX) / HP_MAX * 100;
      ['hp-bar-side'].forEach(id => {
        const bar = document.getElementById(id);
        if (bar) {
          bar.style.width = pct + '%';
          bar.classList.toggle('danger', hp > 0 && pct < 25);
          bar.classList.toggle('warn', hp > 0 && pct >= 25 && pct < 50);
        }
      });
    }

    // ── XP ── (server-rendered at load, so an award needs this to appear without a reload)
    _renderXpRow(info);

    // ── Summons HP ──
    if (characterSummons && info.summons) {
      for (let i = 0; i < characterSummons.length; i++) {
        const srv = info.summons[i];
        const local = characterSummons[i];
        if (!srv || !local) continue;
        if (srv.hp_current !== local.hp_current) {
          local.hp_current = srv.hp_current;
          // Update DOM
          const chip = document.querySelector(`.summon-chip[data-summon-id="${i}"]`);
          if (chip) {
            const barFill = chip.querySelector('.summon-bar-fill');
            const hpText = chip.querySelector('.summon-hp-text');
            if (barFill && local.hp_max) {
              const pct = srv.hp_current / local.hp_max * 100;
              barFill.style.width = pct + '%';
              barFill.style.background = pct > 50 ? 'var(--success)' : pct > 25 ? 'var(--warn)' : 'var(--danger)';
            }
            if (hpText) hpText.textContent = srv.hp_current + '/' + local.hp_max;
          }
        }
      }
    }
    // ── Conditions ──
    if (info.conditions) {
      const srvConds = JSON.stringify(info.conditions);
      const localConds = JSON.stringify(_lastConditions || []);
      if (srvConds !== localConds) {
        _lastConditions = info.conditions;
        renderConditions(info.conditions);
      }
    }
  } catch(e) { /* silent */ }
}

function startSheetPolling(interval) {
  stopSheetPolling();
  _sheetPollTimer = setInterval(syncSheetHP, interval || SHEET_POLL_FAST_MS);
}

// A tab that regains focus syncs at once, so the "hidden" skip above cannot leave a stale HP.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) syncSheetHP();
});

function stopSheetPolling() {
  if (_sheetPollTimer) {
    clearInterval(_sheetPollTimer);
    _sheetPollTimer = null;
  }
}

