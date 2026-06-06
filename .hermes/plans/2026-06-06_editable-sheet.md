# Editable Character Sheet — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make every section of the character sheet editable inline — click a value to edit it, changes persist via API. Add proficiency toggles on skills and saves.

**Architecture:** Extend the existing `/api/character/{id}/update` endpoint with new allowed fields. Add a generic `editable` CSS/JS pattern: click-to-edit inline inputs that auto-save on blur/enter. Proficiency toggles are clickable dots.

**Tech Stack:** FastAPI (Python), Jinja2 templates, vanilla JS, SQLite. Existing app at `~/dnd-character-manager/`.

**What's already editable:** HP, death saves, notes, spells.

**What's already in the update `allowed` set:** hp_current, hp_max, temp_hp, ac, notes, death_saves_success, death_saves_fail, hit_dice_used, strength, dexterity, constitution, intelligence, wisdom, charisma, level, proficiency_bonus.

**What's missing from `allowed`:** speed, hit_dice, skills, tool_proficiencies, weapon_proficiencies, armor_proficiencies, languages, features, inventory, equipped, resistances, immunities, vulnerabilities, condition_immunities, name, race, class_name, subclass, background, alignment, personality, backstory, inspiration, exhaustion, passive_perception.

---

## Task 1: Expand update endpoint `allowed` fields

**Objective:** Add all remaining character fields to the update endpoint's allowed set so the frontend can persist edits.

**Files:**
- Modify: `~/dnd-character-manager/main.py:556-558`

**Step 1: Update the `allowed` set**

Current:
```python
allowed = {"hp_current","hp_max","temp_hp","ac","notes","death_saves_success","death_saves_fail",
           "hit_dice_used","strength","dexterity","constitution","intelligence","wisdom","charisma",
           "level","proficiency_bonus"}
```

Change to:
```python
allowed = {
    # Core stats
    "hp_current","hp_max","temp_hp","ac","notes","death_saves_success","death_saves_fail",
    "hit_dice_used","strength","dexterity","constitution","intelligence","wisdom","charisma",
    "level","proficiency_bonus","speed","hit_dice","inspiration","exhaustion","passive_perception",
    # Identity
    "name","race","subrace","class_name","subclass","background","alignment",
    "personality","backstory",
    # JSON arrays (sent as JSON strings from frontend)
    "skills","tool_proficiencies","weapon_proficiencies","armor_proficiencies","languages",
    "features","inventory","equipped",
}
```

**Step 2: Verify** — restart server, check no syntax errors. No test file needed (existing curl tests cover the update path).

---

## Task 2: Add editable CSS and JS pattern to sheet.html

**Objective:** Add a reusable click-to-edit pattern. Click any editable value → inline input appears → blur/enter saves via API.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (CSS section + scripts block)

**Step 1: Add CSS for editable fields**

Add to the `<style>` block:
```css
.editable { cursor: pointer; border-bottom: 1px dashed transparent; transition: border-color 0.15s; }
.editable:hover { border-bottom-color: var(--text-muted); }
.editable-input { background: transparent; border: none; border-bottom: 1px solid var(--accent); color: var(--text); font: inherit; padding: 0; margin: 0; outline: none; min-width: 2ch; }
.editable-input.number { width: 3.5ch; text-align: center; }
.prof-toggle { cursor: pointer; opacity: 0.3; transition: opacity 0.15s; }
.prof-toggle.active { opacity: 1; color: var(--accent); }
.prof-toggle:hover { opacity: 0.7; }
```

**Step 2: Add JS utility functions**

Add before the closing `</script>`:
```javascript
// ── Editable fields ──
function makeEditable(el, field, isNum) {
  el.classList.add('editable');
  el.addEventListener('click', () => {
    const current = el.textContent.trim();
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'editable-input' + (isNum ? ' number' : '');
    input.value = isNum ? current.replace(/[^0-9-]/g,'') : current;
    input.style.width = (Math.max(current.length, 3) + 1) + 'ch';
    el.textContent = '';
    el.appendChild(input);
    input.focus();
    input.select();

    const save = async () => {
      let val = input.value.trim();
      if (isNum) val = parseInt(val) || 0;
      el.textContent = isNum ? val : val || '(empty)';
      try {
        const body = {}; body[field] = isNum ? val : val;
        await api('/update', body);
      } catch(e) { console.error('Save failed:', e); }
    };
    input.addEventListener('blur', save);
    input.addEventListener('keydown', (e) => { if (e.key==='Enter') { input.blur(); } });
  });
}
```

**Step 3: Verify** — restart server, load a character sheet, no visual changes yet (no elements have the `editable` class yet).

---

## Task 3: Make ability scores editable

**Objective:** Click a score number to edit it inline. The modifier auto-updates via page reload (acceptable for now) or we can add a simple DOM update.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (ability scores section)

**Step 1: Add editable class and data attributes to score divs**

Current line ~164:
```html
<div class="ab-score">{{ score }}</div>
```
Change to:
```html
<div class="ab-score editable" data-field="{{ stat }}" data-num="1">{{ score }}</div>
```

**Step 2: Initialize editable on page load**

Add to the existing page init JS:
```javascript
document.querySelectorAll('.editable[data-num]').forEach(el => {
  makeEditable(el, el.dataset.field, true);
});
```

**Step 3: Verify** — click a score (e.g., 16 next to STR), type 18, press Enter. Check DB updated. Reload page to see new modifier.

---

## Task 4: Make combat stats editable (AC, speed, initiative)

**Objective:** AC and speed become click-to-edit. Initiative stays derived but can be overridden.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (combat section)

**Step 1: AC**
```html
<div class="cq-value editable" data-field="ac" data-num="1">{{ character.ac or 10 }}</div>
```

**Step 2: Speed**
```html
<div class="cq-value editable" data-field="speed" data-num="1">{{ character.speed or 30 }}</div>
```

**Step 3: Initiative** — leave as derived for now (calculated from DEX). Add small note "(from DEX)".

**Step 4: Verify** — click AC, change, verify DB update.

---

## Task 5: HP section — already interactive, skip

The HP section already has + / - buttons and works. No changes needed.

---

## Task 6: Senses — make passive perception editable

**Objective:** Passive perception can be overridden directly.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (senses section)

**Step 1: Passive Perception**
```html
<span class="stat-val editable" data-field="passive_perception" data-num="1">{{ 10 + wis_mod + (prof if 'Perception' in char_skills else 0) }}</span>
```

**Step 2: Passive Investigation and Insight** — leave as derived (no DB columns for them).

**Step 3: Verify**.

---

## Task 7: Proficiency toggles — saving throws

**Objective:** Click the ● dot (or the row) to toggle saving throw proficiency. Add a `saving_throw_proficiencies` JSON column to DB and update the endpoint.

**Files:**
- Modify: `~/dnd-character-manager/main.py` (DB migration + update endpoint + character route)
- Modify: `~/dnd-character-manager/templates/sheet.html` (saves section)

**Step 1: Add DB column**

In the migration block (near line 208), add:
```python
try:
    db.execute("ALTER TABLE characters ADD COLUMN save_proficiencies TEXT DEFAULT '[]'")
except sqlite3.OperationalError:
    pass
```

**Step 2: Expose in character route**

In the character detail route (`/character/{char_id}`), load `save_proficiencies` from the row and pass to template:
```python
save_profs = json.loads(row["save_proficiencies"] or "[]")
# In the template context, merge with class-derived saves:
# saves_class = set(class_saves) | set(save_profs)
```

**Step 3: Add to update `allowed` set**
```python
"save_proficiencies",
```

**Step 4: Update sheet.html — saves section**

Change the saving throw rows to be clickable toggles:
```html
{% set prof_save = stat in saves_class %}
{% set user_prof = stat in save_profs %}
```
The dot becomes a toggle — clicking the row calls `toggleSaveProf(stat)`.

**Step 5: Add JS toggle function**
```javascript
async function toggleSaveProf(stat) {
  const dot = document.querySelector(`.save-dot[data-stat="${stat}"]`);
  const isProf = dot.classList.contains('active');
  dot.classList.toggle('active');
  // Rebuild the save_proficiencies list and send to API
  const profs = [];
  document.querySelectorAll('.save-dot.active').forEach(d => profs.push(d.dataset.stat));
  await api('/update', { save_proficiencies: JSON.stringify(profs) });
  // Update the displayed save value
  location.reload(); // Simplest — or recalculate locally
}
```

**Step 6: Verify** — toggle STR save proficiency on/off, check DB, reload page.

---

## Task 8: Proficiency toggles — skills

**Objective:** Click the ● dot next to a skill name to toggle proficiency. Updates the `skills` JSON array.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (skills section)

**Step 1: Make skill dots toggleable**

Current line ~335:
```html
<span class="stat-name">{% if has_prof %}<span class="prof-dot">●</span>{% endif %}{{ skill_name }}</span>
```
Change to:
```html
<span class="stat-name" onclick="toggleSkillProf('{{ skill_name }}')" style="cursor:pointer">
  <span class="prof-dot{% if has_prof %} active{% endif %}" data-skill="{{ skill_name }}">●</span>{{ skill_name }}
</span>
```

**Step 2: Add JS toggle**
```javascript
async function toggleSkillProf(skillName) {
  const dot = document.querySelector(`.prof-dot[data-skill="${skillName}"]`);
  dot.classList.toggle('active');
  const profs = [];
  document.querySelectorAll('.prof-dot.active').forEach(d => profs.push(d.dataset.skill));
  await api('/update', { skills: JSON.stringify(profs) });
  location.reload();
}
```

**Step 3: Verify** — toggle skills, check they persist.

---

## Task 9: Editable text fields — name, race, class, background, etc.

**Objective:** Character identity fields (name, race, class, background, alignment, personality, backstory) become click-to-edit inline.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (header/sidebar areas)

**Step 1: Name** — in the sheet header:
```html
<h2 class="editable" data-field="name">{{ character.name }}</h2>
```

**Step 2: Race/Class/Level** — in the header subtitle:
```html
<span class="editable" data-field="race">{{ character.race }}</span>
<span class="editable" data-field="class_name">{{ character.class_name }}</span>
Level <span class="editable" data-field="level" data-num="1">{{ character.level }}</span>
```

**Step 3: Background/Alignment** — in the sidebar or a details section:
```html
<div class="stat-row">
  <span class="stat-name">Background</span>
  <span class="stat-val editable" data-field="background">{{ character.background or '—' }}</span>
</div>
```

**Step 4: Init JS** — the existing `makeEditable` init already handles `data-num` and text fields. For non-numeric fields, omit `data-num`.

**Step 5: Verify**.

---

## Task 10: Editable lists — proficiencies, languages, inventory

**Objective:** Click a proficiency or language to edit it as comma-separated text. Inventory items become editable.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (defenses/proficiencies section)

**Step 1: Armor/Weapon/Tool proficiencies**

Each becomes an editable comma-separated field:
```html
<span class="stat-val editable" data-field="armor_proficiencies" data-list="1">{{ character.armor_proficiencies|join(', ') or 'None' }}</span>
```

**Step 2: Update makeEditable for list fields**

When `data-list="1"`, parse the value as comma-separated on save:
```javascript
if (el.dataset.list) {
  val = val.split(',').map(s => s.trim()).filter(Boolean);
  body[field] = JSON.stringify(val);
}
```

**Step 3: Languages** — same pattern:
```html
<span class="stat-val editable" data-field="languages" data-list="1">{{ character.languages|join(', ') or 'Common' }}</span>
```

**Step 4: Verify**.

---

## Task 11: Defenses — resistances, immunities, vulnerabilities

**Objective:** Add editable defense fields. Need DB columns first.

**Files:**
- Modify: `~/dnd-character-manager/main.py` (migration + update allowed + template context)
- Modify: `~/dnd-character-manager/templates/sheet.html` (defenses section)

**Step 1: Add DB columns**

```python
for col in ["damage_resistances","damage_immunities","damage_vulnerabilities","condition_immunities"]:
    try:
        db.execute(f"ALTER TABLE characters ADD COLUMN {col} TEXT DEFAULT '[]'")
    except: pass
```

**Step 2: Add to update `allowed`**

**Step 3: Load in character route** and pass to template.

**Step 4: Add to sheet** — same list-editable pattern as proficiencies.

**Step 5: Verify**.

---

## Task 12: Features and attacks — editable descriptions

**Objective:** Features and attacks become editable. These live in `feature_data` and `attacks_data` JSON columns.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (features tab, actions tab)

**Step 1: Feature descriptions** — add an edit button next to each feature that opens a textarea inline.

**Step 2: Attack stats** — to-hit, damage, range become small numeric editable fields.

**Step 3: Verify**.

---

## Task 13: Inventory management

**Objective:** Add item rows that are editable (name, quantity, weight) with add/remove buttons.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (inventory tab)

**Step 1: Inventory rows** — each item as `<div>` with editable name/qty. Add "+" button to add rows. "✕" to remove.

**Step 2: JS** — `addInventoryItem()`, `removeInventoryItem(idx)`, `updateInventory()`. Sends the full array to API on change.

**Step 3: Verify**.

---

## Task 14: Final verification and polish

**Objective:** Full walkthrough of every editable element. Edge cases.

**Files:**
- Modify: `~/dnd-character-manager/templates/sheet.html` (any bugs)

**Steps:**
1. Create a fresh test character
2. Edit every field: scores, name, race, class, skills, saves, AC, speed, HP, profs, languages, equipment, features
3. Reload — verify all changes persisted
4. Check DB directly for correct values
5. Test on mobile viewport
6. Commit

---

## Summary

| Task | Section | Effort |
|------|---------|--------|
| 1 | Expand update allowed fields | 5 min |
| 2 | CSS + JS editable pattern | 10 min |
| 3 | Ability scores editable | 5 min |
| 4 | Combat stats editable | 5 min |
| 5 | HP (skip, already done) | 0 min |
| 6 | Senses editable | 5 min |
| 7 | Save proficiency toggles | 15 min |
| 8 | Skill proficiency toggles | 10 min |
| 9 | Identity fields editable | 10 min |
| 10 | Proficiency lists editable | 10 min |
| 11 | Defenses editable | 15 min |
| 12 | Features/attacks editable | 15 min |
| 13 | Inventory management | 15 min |
| 14 | Polish & verify | 10 min |

**Total: ~2 hours**
