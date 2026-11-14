# Stage 5: Front-end Module Structure + Inline Handler Removal + type=module

## Scope
- Split three monolithic JS files into ES module packages
- Remove 539 inline handlers across 14 templates → `data-action` + delegated listeners
- Convert 28+ `<script src>` tags to `type="module"` with cache-busting hash bump
- **Do NOT touch**: `static/vtt.js`, `static/grid.js`, `routes/maps.py`, `services/map_grid.py`

---

## 1. Module Splits

### A. `static/sheet.js` (6747 lines) → `static/sheet/`
**Target structure:**
```
static/sheet/
├── __init__.js          # Re-exports all public names, single entry point
├── hp.js                # HP, temp HP, hit dice, death saves, concentration
├── abilities.js         # Ability scores, saves, skills, proficiencies
├── defenses.js          # Resistances, immunities, vulnerabilities, condition immunities
├── summons.js           # Summon management
├── equipment.js         # Item properties, attunement, compute_item_effects
├── conditions.js        # Condition tracking, picker, dismissals
├── level.js             # Level up / de-level logic
├── portrait.js          # Portrait upload, modal, fallback
├── share.js             # Character sharing, export, PDF
├── combat.js            # Combat quick items (AC, initiative, speed, prof, inspiration)
├── senses.js            # Senses, blurb toggles
└── sheet.js             # Original file becomes a 50-line re-export shim
```

**Export surface (must stay identical):**
All `window.*` assignments in original must be reachable from `static/sheet.js` shim. Key exports:
- `openPortraitModal`, `closePortraitModal`, `charPortraitFallback`, `openPortraitFull`
- `toggleEditMode`, `toggleCharacterShare`, `exportCharacter`
- `openLevelUp`, `openDeLevel`, `nextStep`, `prevStep`, `randomizeRace`
- `applyHP`, `resetHP`, `setConcentration`, `clearConcentration`, `rollConcentrationCheck`, `resolveConcentration`
- `toggleConditions`, `openConditionPicker`, `dismissCondition`
- `toggleHitDice`, `spendHitDice`, `rollMax`
- `toggleDeathSaves`, `toggleDS`, `resetDS`
- `showCombatPopup`, `showAbilityPopup`, `toggleSensesBlurb`
- `longRestConfirm`, `shortRestConfirm`
- `toggleSection`, `toggleSavesBlurb`, `toggleSaveProf`, `toggleSkillProf`
- `toggleDefensesBlurb`, `showDefensePicker`
- `toggleProfsBlurb`, `showProfPicker`
- `toggleSection('summons')`, `openSummonModal`, `toggleSummonBody`, `saveSummonName`
- `compute_item_effects`, `ITEM_PROPERTIES`, `ATTUNEMENT_RULES`

### B. `static/dm_tools.js` (6046 lines) → `static/dm_tools/`
**Target structure:**
```
static/dm_tools/
├── __init__.js          # Re-exports all public names
├── monsters.js          # Monster list, search, filters, detail modal
├── spells.js            # Spell list, search, filters, detail modal
├── encounters.js        # Encounter CRUD, AI builder, party profile, habitat weighting
├── npcs.js              # NPC CRUD, AI build, portrait, build-to-character
├── campaigns.js         # Campaign CRUD, roster, notes, XP
├── traps.js             # Trap builder modal
├── widgets.js           # Widget types, limits, clean/save/load
├── ui.js                # Modal management, tab switching, core toggles
├── search.js            # Shared filter/search utilities
└── dm_tools.js          # 50-line re-export shim
```

**Export surface (must stay identical):**
- `toggleCoreDM`, `createMap`
- `filterMonsters`, `filterSpells`, `filterEncounters`, `filterNpcs`
- `showCreateEncounter`, `showAiEncounterBuilder`, `generateAiEncounter`, `onAiCampaignChange`
- `toggleShareEncounter`, `copyEncounter`, `openEncounter`, `deleteEncounter`
- `showCreateNpc`, `showAiNpcWizard`, `editNpc`, `deleteNpc`, `buildNpcToCharacter`
- `openSourceRef`, `showCreateCampaign`, `importCampaignPack`, `saveCampNotes`, `deleteCampaign`
- `previewCharSheet`, `closeModal` (all modals), `cbPrevStep`, `cbNextStep`
- `saveTrap`, `onTrapDangerChange`

### C. `static/create.js` (1760 lines) → `static/create/`
**Target structure:**
```
static/create/
├── __init__.js          # Re-exports
├── race.js              # Race/subrace selection, filtering, core toggle
├── class.js             # Class/subclass selection
├── ability.js           # Ability score allocation (standard array, point buy, roll)
├── equipment.js         # Starting equipment selection
├── wizard.js            # Step navigation, randomization
└── create.js            # 30-line re-export shim
```

**Export surface:**
- `filterOptions`, `toggleCoreCreate`
- `randomizeRace`, `nextStep`, `prevStep`
- All wizard step handlers

---

## 2. Inline Handler Removal (539 handlers across 14 templates)

**Pattern:** Replace `onclick="fn(...)"` / `onchange="fn(...)"` / `onsubmit="return fn(event)"` / `oninput="fn(...)"` / `onkeydown="..."` with:
```html
<button data-action="fn-name" data-arg1="value" data-arg2="value">...</button>
<select data-action="fn-name" data-arg1="value">...</select>
<form data-action="fn-name">...</form>
```

**Delegated listener per page** (in each page's module entry point):
```js
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  const args = Object.entries(btn.dataset).filter(([k]) => k !== 'action').map(([,v]) => v);
  if (window[action]) window[action](...args);
});
```

**Special handling:**
- `onsubmit="return fn(event)"` → `data-action` + `event.preventDefault()` in handler
- `event.stopPropagation()` → handler checks `e.defaultPrevented` or use `data-stop-prop="true"`
- `onmouseover`/`onmouseout` → CSS `:hover` where possible, else `data-hover-action`

**Templates to process (by handler count):**
1. `templates/sheet.html` — ~120 handlers
2. `templates/dm_tools.html` — ~100 handlers
3. `templates/_sheet_top.html` — ~45 handlers
4. `templates/_sheet_header.html` — ~35 handlers
5. `templates/campaign_detail.html` — ~30 handlers
5. `templates/create.html` — ~25 handlers
6. `templates/dashboard.html` — ~20 handlers
7. `templates/layout.html` — ~15 handlers
8. `templates/map_player.html` — ~10 handlers
9. `templates/_char_portrait.html` — ~5 handlers
10. `templates/_sheet_modals.html` — ~40 handlers
11. `templates/_sheet_sidebar.html` — ~30 handlers
12. `templates/map.html` — ~15 handlers
13. `templates/_map_toolbar.html` — ~10 handlers
14. `templates/table.html` — ~10 handlers

---

## 3. `<script src>` → `type="module"` Conversion (28+ tags)

**Files with script tags:**
- `templates/layout.html` — 10 tags (global)
- `templates/create.html` — 3 tags
- `templates/_sheet_scripts.html` — 2 tags
- `templates/map_player.html` — 2 tags
- `templates/map.html` — 2 tags
- `templates/table.html` — 3 tags
- `templates/dm_tools.html` — 5 tags

**Conversion:**
```html
<!-- Before -->
<script src="/static/sheet.js?v={{ sheet_js_version }}"></script>

<!-- After -->
<script type="module" src="/static/sheet/sheet.js?v={{ sheet_js_version }}"></script>
```

**Cache-busting:**
- Update `static_asset_version()` calls for all moved files
- New version constants for each new module entry point
- Ensure `sheet_js_version`, `dm_tools_js_version`, `create_js_version` are bumped

---

## 4. Verification Checklist (each sub-task must pass)

- [ ] `node --check` passes on every new `.js` file
- [ ] `pytest tests/ -q -k "sheet or dm or create or static"` passes
- [ ] No `onclick`/`onchange`/`onsubmit`/`oninput`/`onkeydown`/`onkeyup` remains in templates (except `onerror` on `<img>`)
- [ ] All 28+ script tags are `type="module"` with correct paths
- [ ] Service starts: `systemctl restart dnd-character-manager` → `/` 200, `/login` 200
- [ ] Cloudflare endpoint `https://dnd.jamlarnet.stream` returns 200
- [ ] Character sheet loads, all interactive elements work (HP, conditions, level up, share, PDF)
- [ ] DM tools page loads, all modals/filters/CRUD work
- [ ] Character creation wizard works end-to-end

---

## 5. Dispatch Strategy

**Single subagent** (deep model) handles all three splits + template conversion to maintain consistency of `data-action` naming and delegated listener pattern across all pages.

**Parallel verification:** Run targeted `pytest` subsets after each major split to catch regressions early.

---

## 6. Constraints (from refactor plan)

- Each stage ends with green `pytest` and live health check
- Each stage committed separately
- Surgical `patch`-style edits over full rewrites
- `env -u PYTHONPATH` if imports break
- No `git commit`, `git push`, or service restarts by subagent