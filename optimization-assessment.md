# D&D Character Manager — Optimization Assessment

**Date:** 2026-07-06  
**Analyzed files:** main.py (5195L), routes/characters/all.py (8504L), data.py (883L), pdf_generator.py (2177L), templates/sheet.html (9098L, 530KB)  
**Total Python:** 32,718 lines across 62 files

---

## 🔴 Critical Issues (Highest Impact)

### 1. Startup Performance — Loads 61+ JSON Files Synchronously

**Problem:** Every boot loads:
- **26 SRD cache JSON files** (4.0MB total) — classes, spells, monsters, items, features, etc.
- **~11 manual_data JSON files** (11MB total) — races, spells, monsters, magic_items, npcs, etc.
- **~9 page_map JSON files**
- **Various export JSON files**

Many files are loaded **multiple times**:
- `_meta.json` loaded **4 times** (L324, L4493, L4891, L4949)
- `races.json` loaded **2 times** (L361, L1263)
- `spells.json` loaded at L850 (inside 1035-line `load_manual_data()`)

**Impact:** Each restart takes 3-8 seconds of sync I/O. No async loading, no lazy loading, no caching. All done at module import time + startup event.

**Fix:**
- `functools.lru_cache` on `_load_manual_json()` to avoid re-reading the same file
- Move monster loading out of startup and into lazy `_load_monster_cache()` (already partially done)
- Move NPC/race enrichment to lazy lookup, not eager loading
- Consider caching the merged/processed data structures to a single `data.cache.json`

---

### 2. `load_manual_data()` — 1035-Line Monolith (main.py L320–1355)

**Problem:** One function does everything at startup:
- Loads and validates manual sources
- Processes races (ASI injection, subrace injection, trait descriptions, effects, limited-use traits)
- Handles race renames, subrace aliases, duplicate detection
- Injects manual spells + monsters + items + feats + backgrounds + subclasses + traps
- Enriches everything with SRD data
- Normalizes recharge strings
- Resolves item keys

This is **~20% of all code in main.py** in a single function with 14 levels of nesting.

**Impact:** Impossible to test, maintain, or understand. Any change to data loading risks breaking everything.

**Fix:**
- Split into separate loader modules: `data_loaders/races.py`, `data_loaders/spells.py`, etc.
- Each loader is a single-purpose function that merges one data type
- `load_manual_data()` becomes 8-10 calls to focused loaders

---

### 3. `routes/characters/all.py` at 8504 Lines — One File Holds Everything

**Problem:** This single file contains 105 functions covering:
- Character creation wizard (330L `_build_character`)
- Character sheet rendering (682L `character_sheet`)
- Level-up logic (525L `apply_level_up`, 454L `level_up_info`)
- De-level logic (307L `apply_de_level`)
- Spell management, AI spell selection, combat, conditions
- Item properties, attunement, equipment
- Feat prerequisites, feat configuration
- Treasure hoard engine, relationships
- Monster helpers from DM tools
- **ALL** subclass feature descriptions (dozens of dicts)
- **ALL** domain/oath spell lists
- Fighting styles, metamagic, invocations, maneuvers

**Impact:** Impossible to navigate. Circular import hell — it imports **50+ symbols from main.py**, which in turn imports data.py.

**Fix (by priority):**
1. Extract subclass feature descriptions into `data/subclass_features.py` (adds ~2000 lines but cleans up the route file)
2. Extract item properties/attunement into `data/item_properties.py`
3. Extract spell enrichment into `routes/characters/spells.py`
4. Extract combat/conditions into `routes/characters/combat.py`
5. Extract feat logic into `data/feat_prereqs.py`

---

### 4. `main.py` at 5195 Lines — God File

**Problem:** main.py conflates:
- Config/constants (L1–63)
- Startup data loading (L65–1355)
- Item/weapon/armor logic (L1356–2000)
- DB schema + auth + session management (L1967–2472)
- D&D data constants + descriptions (L2474–3560)
- Weapons, armor, routes (L3560–4200)
- Monster helpers, feature enrichment (L4200–4860)
- Reference manual API (L4860–5195)
- App bootstrap (L4860–4871)
- AND all routes in the same file before they were partially extracted

**Impact:** ~50-symbol import dependency from `all.py` creates tight coupling. Can't easily test individual subsystems.

**Fix:**
- Extract DB layer to `db.py` (schema + auth + sessions)
- Extract item logic to `data/items.py`
- Extract weapon/armor to `data/combat_gear.py`
- Extract monster helpers to `data/monsters.py`
- Extract source slug map to `data/references.py`
- Keep main.py as bootstrap + FastAPI app definition only (~200 lines)

---

### 5. Database — Zero User-Defined Indexes

**Problem:** SQLite database (23MB) has **no indexes** beyond PRIMARY KEY auto-indexes:
- `character_spells` lookup by `character_id` → **SCAN** (full table)
- `characters` lookup by `user_id` → **SCAN** (full table)
- `sessions` lookup → **SCAN**

**Impact:** Currently fine with only 24 characters and 644 spells. But every page load does a `SELECT * FROM character_spells WHERE character_id = ?` which scans all 644 rows. With 24 users this is instant, but will degrade linearly as the app grows.

**Fix:**
```sql
CREATE INDEX idx_char_spells_char ON character_spells(character_id);
CREATE INDEX idx_characters_user ON characters(user_id);
CREATE INDEX idx_sessions_char ON sessions(character_id);
```

---

## 🟠 High Priority

### 6. `sheet.html` — 530KB Single Template File

**Problem:** The character sheet is a single 9098-line Jinja2 template containing:
- ~526KB of mostly inline CSS (the `<style>` block at the top has hundreds of CSS class definitions)
- 604 CSS classes, 264 HTML IDs
- All D&D rules spelled out in HTML/CSS (spell tables, feature descriptions, equipment lists)
- Zero template partials (`{% include %}` count = 0)

**Impact:** 
- Any edit to the sheet requires parsing the entire 530KB file
- Browser must download and parse 530KB for every sheet view
- Poor mobile responsiveness (the CSS is one giant block)
- Cannot reuse layout components

**Fix:**
- Extract CSS to `static/sheet.css` (~200KB saved from template size, enabling browser caching)
- Split into partials: `_stats.html`, `_spells.html`, `_combat.html`, `_features.html`, `_inventory.html`
- Use `{% include %}` for each section
- This reduces delivered HTML per request and enables component-level caching

---

### 7. PDF Generation — Duplicate Spell Cache + No Caching

**Problem:** PDF generator (`pdf_generator.py`):
- `_get_spell_cache()` (105 lines) **re-implements** the same spell loading as main.py's `_load_spell_cache` from `engine.spells`
- Loads **both** the campaign expert engine cache AND manual_data/spells.json again
- Has a supplementary hardcoded dict of spells inline (Absorb Elements, Aganazzar's Scorcher, etc.)
- `fill_official_sheet()` is 397 lines of manually positioned PDF elements
- No reuse of the already-loaded SRD_SPELLS list from main.py

**Fix:**
- Pass SRD_SPELLS as a parameter instead of re-loading
- Move supplementary spells to `data/manual_data/spells.json` (it's already there!)
- Break `fill_official_sheet()` into smaller sub-functions by section (header, stats, spells, features, equipment)

---

### 8. `pdf_generator.py` fill_official_sheet — 397 Lines of Manual Positioning

**Problem:** Uses ReportLab with hardcoded pixel coordinates (x, y) for every field on a 612x792pt page. The sheet has 4 pages of manual positioning. Any design change requires re-measuring every coordinate.

**Impact:** Extremely brittle. A small field size change can cascade through all downstream positions.

**Fix:** Define a coordinate system/constants for each section and compute positions relative to section starts rather than absolute from page top.

---

### 9. No `functools.lru_cache` Usage Anywhere

**Problem:** Several pure functions that are called repeatedly with the same arguments have no caching:
- `_resolve_item_key()` — called for every item in inventory on every sheet render
- `get_spell_slots()` — called per class per render
- `get_class_features()` — called per character
- `get_racial_trait_effects()` — called per character on many pages
- `_get_source_slug_map()` — this is the **only** lazy-loaded cache in the entire codebase

**Fix:** Add `@functools.lru_cache(maxsize=128)` or `@functools.cache` to pure-ish functions.

---

### 10. 14 Levels of Nesting in main.py

**Problem:** Maximum nesting depth of 14 in the manual data loading section (`load_manual_data`). Deeply nested if/for/with/try blocks create spaghetti code.

**Impact:** Hard to follow control flow, easy to introduce bugs in edge cases, impossible to unit test inner paths.

**Fix:** Extract inner loops into named helper functions, use early returns/continues to flatten.

---

## 🟡 Medium Priority

### 11. Data Duplication — D&D Data Lives in 3+ Places

Same D&D 5e data is stored in:
1. **`data.py`** — Hardcoded Python dicts (RACES, CLASSES, FEATS, FEATURE_DESCRIPTIONS, etc.)
2. **`data/manual_data/*.json`** — JSON files with race, spell, item, feat, subclass data
3. **`data/srd_cache/*.json`** — Downloaded from dnd5eapi.co
4. **`main.py`** — Inline RICH_RACE_DESCS, weapon data, armor, item properties
5. **`routes/characters/all.py`** — Subclass feature descriptions, domain/oath spells, fighting styles
6. **`data/exports/*.json`** — Exports from manual data

**Impact:** Updating a race's features requires edits in potentially 4 places. Inconsistency inevitably creeps in.

**Fix:** Pick one canonical data source (suggest: manual_data JSON) and convert the others to derive from it. At minimum, move all hardcoded PHB subclass descriptions from `all.py` into JSON files.

---

### 12. `characters` Table: 70+ Columns Instead of JSON

The characters table has **70+ columns**, many of which are JSON-text columns (skills, features, inventory, etc.). This is a hybrid schema — some things are columns, some are JSON, some are separate tables.

**Impact:** Schema changes require ALTER TABLE migrations. Many columns are unused for most characters.

**Fix:** Consider normalizing into fewer columns + more JSON. E.g., combat stats, ability scores, and class-specific data could be JSON blobs.

---

### 13. Circular Import Chain

```
main.py
  ├─ imports from data.py ✓ (pure data)
  ├─ imports from engine.spells (campaign expert)
  └─ routes/characters/all.py
       └─ imports 50+ symbols from main.py
            ├─ functions (get_db, _render, _build_item_description ...)
            ├─ data dicts (RACES, CLASSES, SRD_SPELLS ...)
            └─ config (DATA_DIR ...)
```

all.py cannot exist without main.py. This makes testing all.py hard (need to import the whole app).

**Fix:** Extract shared functions into a `helpers.py` module that both main.py and all.py can import without circularity.

---

### 14. Only 72 Tests for 32,718 Lines of Python

**Test coverage:** ~0.2%. Tests cover:
- API endpoint smoke tests (14)
- Core function unit tests (31)
- Data integrity (21)
- Template rendering (3)
- Manual sources (3)

**Missing:** No tests for:
- Character creation logic
- Level-up calculations
- Spell slot computation
- Item/equipment logic
- PDF generation (completely untested)
- Monster normalization
- DB migration logic

---

### 15. Hardcoded Path `/home/james/dnd-campaign-expert`

**Problem:** main.py (L82) and pdf_generator.py (L52) hardcode:
```python
_CE_PATH = Path("/home/james/dnd-campaign-expert")
```

This breaks if the project is cloned elsewhere.

**Fix:** Use an environment variable with a fallback, or make it a relative sibling path.

---

## 🟢 Quick Wins (Low Effort)

### 16. Index Creation (5 minutes)
```sql
CREATE INDEX idx_char_spells_char ON character_spells(character_id);
CREATE INDEX idx_characters_user ON characters(user_id);
CREATE INDEX idx_sessions_char ON sessions(character_id);
```

### 17. `functools.lru_cache` on Pure Functions (10 minutes)
Add `@functools.cache` to `_resolve_item_key`, `get_spell_slots`, `get_class_features`, `_meets_feat_prereq`.

### 18. Eliminate Duplicate JSON Loading (15 minutes)
- Cache `_load_manual_json()` results so `_meta.json` loads once, not 4x
- Cache `races.json` loads

### 19. Lazy-Load Some SRD Files (30 minutes)
Some SRD cache files (monsters.json at 1.4MB, magic-items.json at 453KB) are loaded at startup but not needed for character sheets. Move to lazy loading.

### 20. Extract sheet.html CSS to Static File (1 hour)
Move the 200KB+ of CSS in sheet.html's `{% block style %}` to `static/sheet.css`. The browser caches it, saving 200KB+ per sheet load.

---

## Summary Table

| # | Issue | Severity | Effort | Impact |
|---|-------|----------|--------|--------|
| 1 | 61+ JSON files loaded at startup | 🔴 Critical | Medium | Slow restarts, no lazy loading |
| 2 | load_manual_data() 1035L monolith | 🔴 Critical | High | Untestable, unmaintainable |
| 3 | all.py 8504L god file | 🔴 Critical | High | Impossible to navigate |
| 4 | main.py 5195L god file | 🔴 Critical | High | Tight coupling everywhere |
| 5 | Zero DB indexes | 🔴 Critical | Low | Degrades with scale |
| 6 | 530KB single template | 🟠 High | Medium | Slow loads, hard to edit |
| 7 | PDF duplicate spell cache | 🟠 High | Low | Duplicated code |
| 8 | 397L fill_official_sheet | 🟠 High | Medium | Brittle pixel coords |
| 9 | No functools.lru_cache | 🟠 High | Low | Wasted recomputation |
| 10 | 14 levels of nesting | 🟠 High | Medium | Spaghetti logic |
| 11 | Data in 5+ places | 🟡 Medium | High | Inconsistency risk |
| 12 | 70-column table | 🟡 Medium | Medium | Schema rigidity |
| 13 | Circular imports | 🟡 Medium | Medium | Testing difficulty |
| 14 | 72 tests for 32K lines | 🟡 Medium | High | Poor coverage |
| 15 | Hardcoded path | 🟡 Medium | Low | Breaks on other machines |
| 16-20 | Quick wins | 🟢 Quick | Minutes | Immediate payoff |

## Recommended Order of Execution

1. **Week 1 (quick wins):** DB indexes (#16), lru_cache (#17), deduplicate JSON loading (#18), lazy-load monsters (#19)
2. **Week 2 (high impact):** Extract sheet.html CSS (#20), split `load_manual_data()` into loaders (#2)
3. **Week 3 (structural):** Extract DB layer from main.py (#4), create `helpers.py` for shared functions (#13)
4. **Week 4 (major refactor):** Split `all.py` into 4-5 route modules (#3), move subclass data to JSON (#11)
5. **Ongoing:** Add tests for each extracted module (#14)
