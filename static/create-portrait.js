/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
// (genderize() then leaves the prompt alone rather than guessing).
function guessGender(name) {
  const data = RACE_NAMES[state.race];
  const first = String(name || '').trim().split(/\s+/)[0].toLowerCase();
  if (!data || !first) return '';
  if ((data.male || []).some(n => String(n).toLowerCase() === first)) return 'male';
  if ((data.female || []).some(n => String(n).toLowerCase() === first)) return 'female';
  return '';
}

function randomName() {
  const data = RACE_NAMES[state.race];
  const gender = Math.random() < 0.5 ? 'male' : 'female';

  if (data) {
    const first = data[gender][Math.floor(Math.random() * data[gender].length)];
    // Skip clan if empty or 30% random chance
    if (!data.clan || !data.clan.length || Math.random() < 0.3) {
      document.getElementById('char-name').value = first;
      state.name = first;
      return;
    }
    const clan = data.clan[Math.floor(Math.random() * data.clan.length)];
    const name = first + ' ' + clan;
    document.getElementById('char-name').value = name;
    state.name = name;
    return;
  }

  // Fallback: syllable-based generator for races not in RACE_NAMES
  const syl = {
    pre: ["Al","Ar","Bal","Bel","Cal","Cel","Dar","Dor","El","Er","Far","Gal","Gar","Hel","Il","Kal","Kel","Kor","Lan","Lor","Mal","Mar","Mel","Mir","Mor","Nal","Nor","Or","Pal","Per","Quel","Ral","Rel","Ril","Sal","Sar","Sel","Sil","Tal","Tar","Tel","Thal","Ther","Tor","Ul","Val","Var","Vel","Ver","Vil","Vor","Wal","Wil","Xal","Yel","Yor","Zal","Zel","Zor"],
    mid: ["a","ae","ai","an","ar","e","ea","ei","en","er","i","ia","ian","ien","il","in","ir","o","oa","on","or","u","ua","un","ur","y","yr"],
    suf: ["a","ac","ad","aer","al","an","ar","as","ath","en","er","es","eth","ian","ien","il","in","ion","ir","is","ith","on","or","os","uin","um","us","ya","yr"]
  };
  function build(n) {
    let s = syl.pre[Math.floor(Math.random() * syl.pre.length)];
    for (let i = 0; i < n - 2; i++) s += syl.mid[Math.floor(Math.random() * syl.mid.length)];
    s += syl.suf[Math.floor(Math.random() * syl.suf.length)];
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  const first = build(Math.random() < 0.5 ? 2 : 3);
  if (Math.random() > 0.4) {
    const clan = build(Math.random() < 0.5 ? 2 : 3);
    document.getElementById('char-name').value = first + ' ' + clan;
    state.name = first + ' ' + clan;
  } else {
    document.getElementById('char-name').value = first;
    state.name = first;
  }
}

// ── Randomize buttons ────────────────────────────────────────────────

function randomizeRace() {
  const races = Object.keys(RACES);
  const race = races[Math.floor(Math.random() * races.length)];
  selectRace(race);
  const subs = RACES[race].subraces;
  if (subs && subs.length) {
    const sub = subs[Math.floor(Math.random() * subs.length)];
    selectSubrace(sub);
  }
}

function randomizeClass() {
  const classes = Object.keys(CLASSES);
  const cls = classes[Math.floor(Math.random() * classes.length)];
  selectClass(cls);
  const subs = CLASSES[cls].subclasses;
  if (subs && subs.length) {
    const sub = subs[Math.floor(Math.random() * subs.length)];
    selectSubclass(sub);
  }
}

function randomizeAbilities() {
  // Fisher-Yates shuffle of standard array
  const arr = [...STD_ARRAY];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  ABILITIES.forEach((a, i) => {
    state.abilities[a] = arr[i];
    const valEl = document.getElementById('ab-val-' + a);
    const modEl = document.getElementById('ab-mod-' + a);
    if (valEl) valEl.textContent = arr[i];
    if (modEl) modEl.textContent = (Math.floor((arr[i] - 10) / 2) >= 0 ? '+' : '') + Math.floor((arr[i] - 10) / 2);
  });
  state.asi_picks = [];
  updateAsiPreview();
}

function idealAbilities() {
  if (!state.class_name) { alert('Choose a class first (go back to Step 1)'); return; }
  const priority = CLASS_PRIORITY[state.class_name] || CLASS_PRIORITY["Fighter"];
  ABILITIES.forEach((a, i) => {
    const rank = priority.indexOf(a);  // 0 = highest priority
    const val = STD_ARRAY[rank];
    state.abilities[a] = val;
    const valEl = document.getElementById('ab-val-' + a);
    const modEl = document.getElementById('ab-mod-' + a);
    if (valEl) valEl.textContent = val;
    if (modEl) modEl.textContent = (Math.floor((val - 10) / 2) >= 0 ? '+' : '') + Math.floor((val - 10) / 2);
  });
  state.asi_picks = [];
  updateAsiPreview();
}

function randomizeSkills() {
  const c = CLASSES[state.class_name];
  if (!c) return;
  const available = Array.isArray(c.skills) ? [...c.skills] : [...ALL_SKILLS];
  // Shuffle and pick
  for (let i = available.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [available[i], available[j]] = [available[j], available[i]];
  }
  state.skills = available.slice(0, c.skill_count);
  document.querySelectorAll('#skill-grid .option-card').forEach(card => {
    card.classList.toggle('selected', state.skills.includes(card.querySelector('.name').textContent));
  });
}

function idealSkills() {
  const c = CLASSES[state.class_name];
  if (!c) { alert('Choose a class first (go back to Step 1)'); return; }
  const available = new Set(Array.isArray(c.skills) ? c.skills : ALL_SKILLS);
  const ideal = CLASS_IDEAL_SKILLS[state.class_name] || CLASS_IDEAL_SKILLS["Fighter"];
  // Pick first N ideal skills that are in the class's available list
  state.skills = ideal.filter(s => available.has(s)).slice(0, c.skill_count);
  document.querySelectorAll('#skill-grid .option-card').forEach(card => {
    card.classList.toggle('selected', state.skills.includes(card.querySelector('.name').textContent));
  });
}

function randomizeDetails() {
  randomName();
  const bgs = BACKGROUNDS.filter(b => b !== 'Custom');
  document.getElementById('char-bg').value = bgs[Math.floor(Math.random() * bgs.length)];
  showBgInfo(document.getElementById('char-bg').value);
  document.getElementById('char-align').value = ALIGNMENTS[Math.floor(Math.random() * ALIGNMENTS.length)];
}

async function autoFillDetails() {
  const btn = event.target;
  btn.textContent = '⏳...'; btn.disabled = true;
  const name = document.getElementById('char-name').value || state.name || '';
  try {
    const r = await fetch('/api/ai/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        race: state.race, subrace: state.subrace,
        class_name: state.class_name, subclass: state.subclass,
        abilities: state.abilities || {}, skills: state.skills || [],
        alignment: document.getElementById('char-align').value,
        name
      })
    });
    const data = await r.json();
    if (data.name) { document.getElementById('char-name').value = data.name; state.name = data.name; }
    if (data.background && [...document.getElementById('char-bg').options].some(o => o.value === data.background)) {
      document.getElementById('char-bg').value = data.background;
      showBgInfo(data.background);
    }
    if (data.alignment) document.getElementById('char-align').value = data.alignment;
    state.personality = data.personality || '';
    state.backstory = data.backstory || '';
    if (data.ai_generated === false) {
      // The endpoint answers with deterministic text when every tier is down, and this used to be
      // indistinguishable from a generated character: the fields simply filled in.
      alert('The AI could not be reached — these are generic placeholder details. Try again later.');
    }
  } catch(e) {
    console.error(e);
    alert('Auto-fill failed: ' + (e.message || e) + ' — nothing was changed.');
  }
  btn.textContent = '🤖 Auto-Fill'; btn.disabled = false;
}

async function aiGenerateBackground() {
  const btn = event.target;
  btn.textContent = '⏳...'; btn.disabled = true;
  try {
    const r = await fetch('/api/ai/generate-background', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        race: state.race, subrace: state.subrace,
        class_name: state.class_name, subclass: state.subclass,
        abilities: state.abilities, skills: state.skills,
        alignment: document.getElementById('char-align').value
      })
    });
    const data = await r.json();
    // Set custom background
    document.getElementById('char-bg').value = 'Custom';
    document.getElementById('custom-bg-name').value = data.name || '';
    state.custom_background = data.name || '';
    // Store full background data for persistence
    state.background_data = {
      name: data.name || '',
      description: data.description || '',
      items: data.items || [],
      gp: data.gp || 15
    };
    // Show description
    const el = document.getElementById('bg-info');
    el.style.display = 'block';
    el.innerHTML = `<strong>${data.name || 'Generated Background'}</strong><br>${data.description || ''}<br><span style="color:var(--success)">Items: ${(data.items||[]).join(', ')} · ${data.gp || 15} gp</span>`;
    document.getElementById('custom-bg-row').style.display = 'block';
    // Store equipment
    state.bg_items = data.items || [];
    state.bg_gp = data.gp || 15;
    if (data.ai_generated === false) {
      alert('The AI could not be reached — this background is a generic placeholder.');
    }
  } catch(e) {
    console.error(e);
    alert('Background generation failed: ' + (e.message || e) + ' — nothing was changed.');
  }
  btn.textContent = '🤖 AI Generate'; btn.disabled = false;
}

function autoEquip() {
  if (state.class_name && STARTING_EQUIPMENT[state.class_name]) {
    state.equipment = STARTING_EQUIPMENT[state.class_name];
    buildReview();
  }
}

async function generatePortrait() {
  const section = document.getElementById('portrait-section');
  const img = document.getElementById('portrait-img');
  const loading = document.getElementById('portrait-loading');
  const custom = document.getElementById('portrait-custom').value.trim();
  section.style.display = 'block';
  img.style.display = 'none';
  loading.style.display = 'block';
  loading.innerHTML = '🎨 Generating portrait (may take ~60s)...';

  try {
    const r = await fetch('/api/ai/portrait', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        race: state.race, subrace: state.subrace,
        class_name: state.class_name, subclass: state.subclass,
        abilities: state.abilities, background: state.background,
        alignment: state.alignment, skills: state.skills,
        gender: guessGender(state.name || (document.getElementById('char-name') || {}).value),
        custom_prompt: custom
      })
    });
    const data = await r.json();
    state.portrait_prompt = data.prompt;
    state.portrait_url = data.image_url || '';

    if (state.portrait_url) {
      img.src = state.portrait_url;
      loading.style.display = 'none';
      img.style.display = 'block';
    } else {
      const why = data.error || 'the image service did not return a picture';
      loading.innerHTML = '<div style="font-size:0.75rem;color:var(--text-muted);text-align:center;line-height:1.4">'
        + '<span>⚠️ Portrait not generated</span><br>'
        + '<span style="font-size:0.65rem">' + why + '</span>'
        + '<br><button onclick="generatePortrait()" style="margin-top:6px;padding:2px 10px;font-size:0.7rem;cursor:pointer;background:var(--accent);color:#fff;border:none;border-radius:4px">🔄 Retry</button>'
        + '<br><span style="font-size:0.65rem">You can also add a portrait any time from the character sheet.</span>'
        + '</div>';
    }
  } catch(e) {
    loading.innerHTML = '<p style="font-size:0.8rem">⚠️ Portrait failed: ' + (e.message || e) + '</p>';
    console.error(e);
  }
}

function regeneratePortrait() {
  document.getElementById('portrait-img').src = '';
  document.getElementById('portrait-img').style.display = 'none';
  generatePortrait();
}
