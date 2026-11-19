/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
async function createCharacter() {
  // One sheet per press: a second dispatch (held Enter, a double click, a
  // retried request) used to POST again and leave two identical characters on
  // the roster. The disabled button is not enough — the delegated keydown
  // handler still ran.
  if (createCharacter.busy) return;
  createCharacter.busy = true;
  try {
    buildReview();

    // Validate Dragonborn ancestry
    if ((state.race === 'Dragonborn' || state.race === 'Dragonblood') && !state.dragonborn_ancestry) {
      alert('Please select a Draconic Ancestry (Step 0 → Race). This determines your damage resistance and breath weapon.');
      showStep(0);
      createCharacter.busy = false;
      return;
    }

    const btn = document.getElementById('create-btn');
    btn.textContent = 'Creating...'; btn.disabled = true;

    const payload = {
      name: state.name, race: state.race, subrace: state.subrace,
      class_name: state.class_name, subclass: state.subclass,
      abilities: state.abilities, skills: state.skills,
      expertise_skills: state.expertise_skills,
      fighting_style: state.fighting_style,
      metamagic: state.metamagic,
      invocations: state.invocations,
      pact_boon: state.pact_boon,
      maneuvers: state.maneuvers,
      magical_secrets: state.magical_secrets,
      totem_spirits: state.totem_spirits,
      hunters_prey: state.hunters_prey,
      favored_enemies: state.favored_enemies,
      favored_terrains: state.favored_terrains,
      infusions: state.infusions,
      asi_picks: isMpmmRace() ? mpmmPicks().map(x => x[0]) : state.asi_picks,
      asi_mode: isMpmmRace() ? state.mpmm_mode : "",
      background: state.background,
      background_data: state.background_data || '',
      alignment: state.alignment, personality: state.personality, backstory: state.backstory,
      level: 1,  // Always create at L1 — level-up wizard handles higher levels
      spells: state.spells || [],  // Starting spells from picker
      equipment: state.equipment,
      dragonborn_ancestry: state.dragonborn_ancestry || '',
      subclass_bonus: state.subclass_bonus || [],
      portrait_url: state.portrait_url || '',
      portrait_prompt: state.portrait_prompt || ''
    };

    const r = await fetch('/api/character/create', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    const data = await r.json();
    if (data.error) { alert(data.error); btn.textContent = '✓ Create Character'; btn.disabled = false; createCharacter.busy = false; return; }
    // A campaign that sent us here gets the sheet back (it becomes a party member
    // and starts syncing); otherwise the player lands on their new sheet.
    const back = (window.CREATE_NEXT || '').replace('{id}', encodeURIComponent(data.id));
    if (back) { window.location.href = back; return; }
    // If target level > 1, redirect to sheet with auto-level-up
    const targetLevel = parseInt(document.getElementById('char-level').value) || 1;
    if (targetLevel > 1) {
      window.location.href = '/character/' + data.id + '?level-up=' + targetLevel;
    } else {
      window.location.href = '/character/' + data.id;
    }
  } catch(e) {
    createCharacter.busy = false;
    alert('Failed to create character: ' + (e.message || e));
    const btn = document.getElementById('create-btn');
    if (btn) { btn.textContent = '✓ Create Character'; btn.disabled = false; }
    console.error(e);
  }
}

// Init
buildRaces();
buildClasses();
buildAbilities();
// Sync core toggle UI from localStorage
if (_coreOnlyCreate) {
  document.querySelectorAll('.core-create-box').forEach(b => b.textContent = '☑');
  document.querySelectorAll('label').forEach(l => { if (l.querySelector('.core-create-box')) l.style.color = 'var(--accent)'; });
}

// ── Auto-fill functions ─────────────────────────────────────────────

// The name lists are gendered, so the wizard already knows this — it just never
// told the portrait generator, which is why a female character could come back
// bearded. Only names from those lists are trusted; anything else returns ''
