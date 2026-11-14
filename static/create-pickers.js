/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
// ── Expertise Picker ──────────────────────────────────────────────

function hasL1Expertise() {
  if (!state.class_name) return false;
  // Check class
  var exp = EXPERTISE_LEVELS[state.class_name];
  if (!exp && state.subclass) exp = EXPERTISE_LEVELS[state.subclass];
  if (!exp) return false;
  var lvls = exp.levels || exp;  // support old list format
  return Array.isArray(lvls) && lvls.includes(1);
}

function getExpertiseOpts() {
  // Returns array of valid expertise options for current class/subclass
  var exp = EXPERTISE_LEVELS[state.subclass] || EXPERTISE_LEVELS[state.class_name];
  if (!exp) return state.skills.slice();
  var opts = exp.options;
  if (Array.isArray(opts)) return opts.slice();
  if (opts === 'skills_and_thieves_tools') {
    var arr = state.skills.slice();
    if (!arr.includes("Thieves' Tools")) arr.push("Thieves' Tools");
    return arr;
  }
  return state.skills.slice();
}

function showExpertisePicker() {
  state._expertiseShown = true;
  var options = getExpertiseOpts();
  var count = 2;
  var c = CLASSES[state.class_name];
  var label = state.subclass ? state.subclass + ' L1' : c.name + ' L1';
  
  var info = document.getElementById('expertise-picker-info');
  info.textContent = 'Choose ' + count + ' skills or tools to double your proficiency bonus. (' + label + ')';
  
  var container = document.getElementById('expertise-picker-options');
  container.innerHTML = '';
  state._expPicks = state.expertise_skills.slice();
  
  options.forEach(function(opt) {
    var chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = opt;
    if (state._expPicks.includes(opt)) chip.classList.add('selected');
    chip.onclick = function() {
      if (state._expPicks.includes(opt)) {
        state._expPicks = state._expPicks.filter(function(x) { return x !== opt; });
      } else {
        if (state._expPicks.length >= count) { alert('Only ' + count + ' expertise picks allowed'); return; }
        state._expPicks.push(opt);
      }
      updateExpChips();
    };
    container.appendChild(chip);
  });
  updateExpChips();
  document.getElementById('expertise-picker-overlay').style.display = 'flex';
}

function updateExpChips() {
  var chips = document.querySelectorAll('#expertise-picker-options .chip');
  chips.forEach(function(c) {
    c.classList.toggle('selected', state._expPicks.includes(c.textContent));
  });
  document.getElementById('expertise-picker-count').textContent = 
    state._expPicks.length + ' / 2 selected';
}

function skipExpertisePicker() {
  state.expertise_skills = [];
  document.getElementById('expertise-picker-overlay').style.display = 'none';
  showStep(4);
}

function confirmExpertisePicker() {
  state.expertise_skills = state._expPicks.slice();
  document.getElementById('expertise-picker-overlay').style.display = 'none';
  showStep(4);
}

// ── Fighting Style Picker ─────────────────────────────────────────
function hasL1FightingStyle() {
  return state.class_name && FIGHTING_STYLE_OPTIONS[state.class_name] && FIGHTING_STYLE_OPTIONS[state.class_name].length > 0;
}

function showFightingStylePicker() {
  state._fightingStyleShown = true;
  var opts = FIGHTING_STYLE_OPTIONS[state.class_name] || [];
  var container = document.getElementById('fighting-style-picker-options');
  container.innerHTML = '';
  
  var info = document.getElementById('fighting-style-picker-info');
  info.textContent = 'Choose your Fighting Style (' + state.class_name + ' L1)';
  
  opts.forEach(function(key) {
    var style = FIGHTING_STYLES[key];
    var card = document.createElement('div');
    card.className = 'card' + (state.fighting_style === key ? ' selected' : '');
    card.style.cssText = 'cursor:pointer;padding:0.6rem 0.8rem;margin-bottom:0.4rem;border:1px solid var(--border);border-radius:6px;transition:border-color 0.15s';
    card.onclick = function() {
      state.fighting_style = (state.fighting_style === key) ? '' : key;
      document.querySelectorAll('#fighting-style-picker-options .card').forEach(function(c) {
        c.classList.remove('selected');
        c.style.borderColor = 'var(--border)';
      });
      if (state.fighting_style === key) {
        card.classList.add('selected');
        card.style.borderColor = 'var(--accent)';
      }
    };
    card.innerHTML = '<strong>' + style.name + '</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">' + style.desc + '</span>';
    container.appendChild(card);
  });
  document.getElementById('fighting-style-picker-overlay').style.display = 'flex';
}

function skipFightingStylePicker() {
  state.fighting_style = '';
  document.getElementById('fighting-style-picker-overlay').style.display = 'none';
  nextStep();  // Chain into FE/FT pickers for Ranger
}

function confirmFightingStylePicker() {
  document.getElementById('fighting-style-picker-overlay').style.display = 'none';
  nextStep();  // Chain into FE/FT pickers for Ranger
}

// ── Metamagic Picker (Sorcerer L1 — not gained at L1, so skip) ────
// ── Eldritch Invocation Picker (Warlock L2+ — not L1) ──────────────
// ── Pact Boon Picker (Warlock L3 — not L1) ─────────────────────────
// ── Battle Master Maneuvers Picker (L3 — not L1) ──────────────────
// ── Magical Secrets Picker (Bard L6+/10+/14+/18+ — not L1) ────────
// ── Totem Spirit Picker (L3/6/14 — not L1) ─────────────────────────
// ── Hunter's Prey Picker (Ranger Hunter L3 — not L1) ──────────────
// ── Artificer Infusion Picker (L2) ─────────────────────────────────
function hasL1Infusions() {
  return state.class_name === 'Artificer';
}

function showInfusionPicker() {
  state._infusionShown = true;
  var opts = Object.entries(INFUSION_OPTIONS);
  var count = 4;
  var container = document.getElementById('infusion-picker-options');
  container.innerHTML = '';
  
  var info = document.getElementById('infusion-picker-info');
  info.textContent = 'Choose ' + count + ' infusions (Artificer L2). You can change these each long rest.';
  
  state._infPicks = state.infusions.slice();
  
  opts.forEach(function(e) {
    var key = e[0], v = e[1];
    var chip = document.createElement('div');
    chip.className = 'card' + (state._infPicks.includes(key) ? ' selected' : '');
    chip.style.cssText = 'cursor:pointer;padding:0.5rem 0.7rem;margin-bottom:0.3rem;border:1px solid var(--border);border-radius:6px;';
    chip.onclick = function() {
      if (state._infPicks.includes(key)) {
        state._infPicks = state._infPicks.filter(function(x) { return x !== key; });
      } else {
        if (state._infPicks.length >= count) { alert('Only ' + count + ' infusions allowed'); return; }
        state._infPicks.push(key);
      }
      updateInfChips();
    };
    chip.innerHTML = '<strong>' + v.name + '</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">' + v.desc + '</span>';
    container.appendChild(chip);
  });
  updateInfChips();
  document.getElementById('infusion-picker-overlay').style.display = 'flex';
}

function updateInfChips() {
  document.querySelectorAll('#infusion-picker-options .card').forEach(function(c, i) {
    var key = Object.keys(INFUSION_OPTIONS)[i];
    c.classList.toggle('selected', state._infPicks.includes(key));
    c.style.borderColor = state._infPicks.includes(key) ? 'var(--accent)' : 'var(--border)';
  });
  document.getElementById('infusion-picker-count').textContent = state._infPicks.length + ' / 4 selected';
}

function skipInfusionPicker() {
  state.infusions = [];
  document.getElementById('infusion-picker-overlay').style.display = 'none';
  showStep(2);
}

function confirmInfusionPicker() {
  state.infusions = state._infPicks.slice();
  document.getElementById('infusion-picker-overlay').style.display = 'none';
  showStep(2);
}

// ── Favored Enemy Picker (Ranger L1) ─────────────────────────────
function hasL1FavoredEnemy() {
  return state.class_name === 'Ranger' && !subclassReplaces('favored_enemy');
}
function subclassReplaces(feature) {
  return state.subclass && SUBCLASS_FEATURE_REPLACEMENTS[state.subclass]?.includes(feature);
}
function showFavoredEnemyPicker() {
  var opts = Object.entries(FAVORED_ENEMY_OPTIONS);
  var container = document.getElementById('favored-enemy-picker-options');
  container.innerHTML = '';
  var info = document.getElementById('favored-enemy-picker-info');
  info.textContent = 'Choose your Favored Enemy (Ranger L1 — PHB p.91)';
  var selected = false;
  opts.forEach(function(entry) {
    var key = entry[0], opt = entry[1];
    var card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'cursor:pointer;padding:0.6rem 0.8rem;margin-bottom:0.4rem;border:1px solid var(--border);border-radius:6px;transition:border-color 0.15s';
    card.onclick = function() {
      document.querySelectorAll('#favored-enemy-picker-options .card').forEach(function(c) {
        c.classList.remove('selected');
        c.style.borderColor = 'var(--border)';
      });
      card.classList.add('selected');
      card.style.borderColor = 'var(--accent)';
      state._favoredEnemyPick = key;
    };
    card.innerHTML = '<strong>' + opt.name + '</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">' + opt.desc + '</span>';
    container.appendChild(card);
  });
  document.getElementById('favored-enemy-picker-overlay').style.display = 'flex';
}
function skipFavoredEnemyPicker() {
  state.favored_enemies = [];
  state._favoredEnemyShown = true;
  state._favoredEnemyPick = null;
  document.getElementById('favored-enemy-picker-overlay').style.display = 'none';
  nextStep();
}
function confirmFavoredEnemyPicker() {
  if (!state._favoredEnemyPick) {
    alert('Please select a Favored Enemy');
    return;
  }
  state.favored_enemies = [state._favoredEnemyPick];
  state._favoredEnemyShown = true;
  document.getElementById('favored-enemy-picker-overlay').style.display = 'none';
  nextStep();
}

// ── Favored Terrain Picker (Ranger L1) ────────────────────────────
function hasL1FavoredTerrain() {
  return state.class_name === 'Ranger' && !subclassReplaces('favored_terrain');
}
function showFavoredTerrainPicker() {
  var opts = Object.entries(FAVORED_TERRAIN_OPTIONS);
  var container = document.getElementById('favored-terrain-picker-options');
  container.innerHTML = '';
  var info = document.getElementById('favored-terrain-picker-info');
  info.textContent = 'Choose your Favored Terrain (Ranger L1 — PHB p.91)';
  opts.forEach(function(entry) {
    var key = entry[0], opt = entry[1];
    var card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = 'cursor:pointer;padding:0.6rem 0.8rem;margin-bottom:0.4rem;border:1px solid var(--border);border-radius:6px;transition:border-color 0.15s';
    card.onclick = function() {
      document.querySelectorAll('#favored-terrain-picker-options .card').forEach(function(c) {
        c.classList.remove('selected');
        c.style.borderColor = 'var(--border)';
      });
      card.classList.add('selected');
      card.style.borderColor = 'var(--accent)';
      state._favoredTerrainPick = key;
    };
    card.innerHTML = '<strong>' + opt.name + '</strong><br><span style="font-size:0.8rem;color:var(--text-muted)">' + opt.desc + '</span>';
    container.appendChild(card);
  });
  document.getElementById('favored-terrain-picker-overlay').style.display = 'flex';
}
function skipFavoredTerrainPicker() {
  state.favored_terrains = [];
  state._favoredTerrainShown = true;
  state._favoredTerrainPick = null;
  document.getElementById('favored-terrain-picker-overlay').style.display = 'none';
  nextStep();
}
function confirmFavoredTerrainPicker() {
  if (!state._favoredTerrainPick) {
    alert('Please select a Favored Terrain');
    return;
  }
  state.favored_terrains = [state._favoredTerrainPick];
  state._favoredTerrainShown = true;
  document.getElementById('favored-terrain-picker-overlay').style.display = 'none';
  nextStep();
}

// ── Create ──
