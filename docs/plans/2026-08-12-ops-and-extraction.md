# D&D Character Manager — Ops Hardening & Monolith Extraction Plan

> **For Hermes:** Implement in vertical slices. Run tests after every slice and commit each milestone.

**Goal:** Add a coverage gate to CI, automate DB backups with rotation, and
shrink the remaining `main.py` monolith (5,325 lines) via verified extractions.

**Tech Stack:** pytest-cov, SQLite backup API, system cron, FastAPI.

---

## Phase 1 — Coverage gate

- Install `pytest-cov` into `.venv` (dev-only; add to requirements).
- Measure baseline coverage first (expect low — audit noted ~0.2%).
- Add `.coveragerc` excluding import-time-heavy / third-party paths.
- Add `--cov --cov-report=term --cov-fail-under=<baseline>` to CI
  (`test.yml`) so coverage can only go up, never silently down.
- Local: `pytest --cov` works out of the box.

## Phase 2 — Automated DB backup + rotation

- New `scripts/backup_db.py` using the SQLite backup API (`sqlite3`
  `Connection.backup`) — safe against WAL and concurrent writes,
  unlike the manual `cp` in OPS.md.
- Backups to `data/backups/characters-YYYYMMDD-HHMMSS.db`; rotate: keep
  newest 14, delete older.
- Verify: run once, confirm backup file opens and schema validates,
  confirm rotation deletes old files.
- Install a daily cron entry (user crontab, TZ America/New_York,
  consistent with existing `daily-postclose-analysis.sh` pattern).
- Update `docs/OPS.md` backup section to reference the script.

## Phase 3 — Extract main.py (5,325 → ~3,500 lines)

### 3a — Delete dead monster-helper block (lines 4388–4777, ~390 lines)

Verified dead: `_normalize_manual_monster`, `_template_monster_entries`,
`MANUAL_MONSTERS`, `_xp_for_cr`, `_load_monster_cache` in `main.py` are
imported by NOBODY; canonical versions live in
`routes/characters/helpers.py` (which sorts; main's copy does not and is
never called). Pure deletion, zero behavior change.

### 3b — Extract item helpers → `services/items.py` (~540 lines)

Move `_resolve_item_key`, `_split_curse_text`, `_build_item_description`,
`_build_item_type`, `_resolve_source`, `_extract_srd_dice`,
`_item_rarity_for_level` to `services/items.py`. These read module globals
(`ITEM_INDEX`, `SRD_MAGIC_ITEMS`, `ITEMS_BY_RARITY`) that live in main —
use the pdf_generator pattern: lazy `from main import X` inside function
bodies (no top-level main import → no circular import). Then main.py does
`from services.items import (...)` at top (safe: services/items has no
top-level main import), binding the names so every existing
`from main import _resolve_item_key` call site keeps working unchanged.
Delete the moved definitions from main.py.

### 3c — Extract racial/combat helpers → `services/combat.py` (~880 lines)

Same mechanics for `get_racial_trait_effects`, `_build_racial_traits`,
`_subrace_traits`, `_find_weapon`, `_parse_enhancement`,
`_build_attack_for_weapon`, `_build_inventory_attacks`,
`_build_charged_item_attacks`, `_normalize_equipped`,
`_build_named_item_types`, `_get_named_item_types`, `_equipped_names`,
`_normalize_armor_profs`, `get_character_armor_profs`, `_resolve_armor_item`,
`check_armor_proficiency_from_set`. Lazy main imports inside bodies; main
re-binds via top-level `from services.combat import (...)`. Delete the
moved definitions from main.py.

### Remaining (documented, not attempted)

None — all items completed:

- `_render` was already minimal (8 lines; the ~690-line context builder had
  moved to `routes/characters/sheet.py` during the template split).
- `init_db` + `_migrate_npc_source_columns` (410 lines) → `services/db_schema.py`.
- `load_manual_data` merge (1,056 lines) + 9 loader helpers + private
  constants → `services/data_loader.py`. AST-verified zero bare rebinds of
  registry names, so lazy `from main import ...` at call time propagates
  in-place mutations to the same objects; `global` statement dropped.

**Final: main.py 5,325 → 2,279 lines (-57%).** Coverage 43% → 44.5%.

---

## Verification gate (every slice)

```bash
.venv/bin/python3 -m pytest -q
.venv/bin/python3 -m compileall -q main.py data.py routes services pdf_generator.py
sudo systemctl restart dnd-character-manager
curl -s -o /dev/null -w "%{http_code}" http://localhost:8300/   # 200
```

No slice is complete with failing tests or a broken live server.

## Commit policy

One commit per phase/slice, message documents changed files + tests run.
DB, logs, secrets, manuals never committed.
