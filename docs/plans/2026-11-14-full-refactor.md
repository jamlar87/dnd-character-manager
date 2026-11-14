# Full Refactor — D&D Character Manager

> **For Hermes:** implement in the stage order below. Each stage ends green (`pytest` + live health check) and is committed separately. Do not start a stage while the previous one is uncommitted.

**Goal:** Reduce the repo from a monolithic FastAPI app to a layered one — shared rule/access primitives, thin route handlers, services that own logic — without changing D&D behaviour, and close the correctness holes the audit found on the way.

**Architecture chosen:** inner-to-outer. Fix the exploitable behaviour first (cheap, high value), consolidate the duplicated primitives next (they are the seams every later split needs), then split the god modules (`main.py` → `core/`, `routes/dm.py` → package, `leveling`/`sheet` → services), then the front-end (shared `ui/` modules before `type="module"`).

**Tech stack:** FastAPI 0.136, SQLite (no ORM), Jinja2, vanilla JS (no build step), Python 3.13, 1334 tests.

---

## Baseline (measured 2026-11-14)

| Metric | Value |
|---|---|
| Python files / LOC | 224 / 68,465 |
| Tests | 1,334 passing (`pytest -q`, ~20 min) |
| Largest modules | `routes/dm.py` 3,769 · `main.py` 2,984 · `routes/characters/sheet.py` 2,614 · `routes/characters/all.py` 2,467 · `pdf_generator.py` 2,216 |
| Fat functions | 31 ≥150 lines, 9 ≥300, 4 ≥500 (worst: `dm_ai_build_encounter` 581, `apply_level_up` 534) |
| Front-end | 806 JS functions, 379 inline `on*=` handlers, 12 inline template scripts (372 lines) |
| DB | 16 user indexes, `schema_migrations` present |
| Service | `dnd-character-manager.service`, :8300, live |

**Already fixed — do not re-litigate:** DB indexes, `lru_cache` adoption (433 sites), `main.py` 5,195→2,984, `routes/characters/all.py` 8,504→2,467, async-handler blocking (`87180ac`), `SELECT *` portrait blobs in the character list, login/session throttling, spell-slot tables (single home, verified clean), dice roller (single home).

---

## Stage 0 — Exploitable defects (do first, one owner, small diff)

| # | Defect | File | Fix |
|---|---|---|---|
| 0.1 | **IDOR write** — any logged-in user can move items out of another user's character | `routes/characters/campaign.py:479-509` | compare `char["user_id"]` before writing; 403 otherwise; scope the campaign scan by membership |
| 0.2 | **Inventory inflation** — `qty:-5` creates items | `campaign.py:516-546` | `qty = max(1, min(int(qty), current_qty))`; reject `qty <= 0` with 400 |
| 0.3 | **Money destroyed** — `int(50 cp × 0.01) = 0` | `campaign.py:448` | integer cp arithmetic, convert once at the edge |
| 0.4 | **XSS** — team item name rendered unescaped in DM panel | `campaign.py:379` → `static/dm_tools.js:2714` | `escapeHtml` at render, cap name length at write |
| 0.5 | **Script-breakout XSS** — character name closes the `<script>` block | `routes/dm.py:351` → `templates/dm_tools.html:1274` | `|tojson` (or `json.dumps(...).replace("<","\\u003c")`) |
| 0.6 | **Attribute XSS** — `onclick="previewCharSheet(${ch.id}, '${ch.name}')"` | `static/dm_tools.js:2372,2377,3712-3714` | escape into `data-*`, delegate the listener |
| 0.7 | **No input bounds on sheet writes** — `{"level":200,"hp_max":1e8}` persists | `routes/characters/sheet.py:1244-1266` | one clamp table (`services/validation.py`) used by sheet, creation and DM paths |
| 0.8 | Swallowed roster parse → phantom 403 | `campaign.py:331-332,508-509` | log + 500 instead of `pass` |
| 0.9 | Fabricated monster HP (`except: hp = 10`) | `routes/dm.py:142,710,766,3242` | `hp = None` + log; UI asks |

**Gate:** targeted tests for each (`tests/test_campaign_team_sharing.py`, `tests/test_sheet_input_bounds.py`, new `tests/test_xss_escaping.py`), then full suite, restart, health 200.

---

## Stage 1 — Performance (measured, one owner)

| # | Fix | File | Evidence |
|---|---|---|---|
| 1.1 | Manual search blocks the event loop ~0.9 s/query | `routes/dm.py:2367,2390` → `helpers.py:928` | 91-file `readlines()` = 640 ms, 33.5 MB; wrap in `run_in_threadpool`, read only hit books |
| 1.2 | N+1 in `GET /api/dm/campaigns` | `routes/dm.py:3015-3033` | 13.51 ms → 0.14 ms with one `IN` query (96×) |
| 1.3 | `SELECT *` drags 3.4 MB portraits in 8 sites | `sheet.py:484,487`, `leveling.py:215,733,1275,1397`, `pack.py:120`, `relationships.py:61` | 14.63 ms vs 4.98 ms per row; callers never read the blob |
| 1.4 | `/create` rebuilds a 288 KB asset every request | `services/reference_assets.py:25-59` | 11 ms of `json.dumps` per wizard load; add `_CREATE_CONSTS_VERSION` guard like its two siblings |
| 1.5 | N+1 spell lookup per party member | `routes/dm.py:1413` | 0.196 ms vs 0.092 ms; linear in party size |

**Gate:** same tests + a timing spot-check before/after for 1.1/1.2/1.4; full suite; restart; health.

---

## Stage 2 — Shared primitives (the seams later splits depend on)

| # | Primitive | New home | Replaces |
|---|---|---|---|
| 2.1 | `ability_mod(score)` + Jinja filter | `services/rules.py` | 36 hand-rolled sites (`combat.py:191`, `sheet.py:872`, `pdf_generator.py:321`, 20 `{% set %}` copies) |
| 2.2 | `proficiency_bonus(level)` reading the table | `services/rules.py` | `creation.py:173` formula |
| 2.3 | `HYDRATE_JSON_FIELDS` + `hydrate_json_columns(char)` | `services/schema.py` | 7 divergent field lists (`sheet.py:494`, `main.py:1876`, `dm.py:203/693/813/1369`, `ai_routes.py:733`) |
| 2.4 | `load_character(db, user, id, mode)` | `routes/characters/helpers.py` | 3 divergent access rules (`sheet.py:487`, `:1282`, `:1860`) |
| 2.5 | `campaign_role(db, user, camp_id) -> dm\|member\|none` | `services/campaign_access.py` | 5 copies in `campaign.py:317,368,560,578,593` |
| 2.6 | Hex grid math, one spec | `static/grid.js` + `services/map_grid.py` | `vtt.js:90,341` + `maps.py:1068` (already shipped one placement bug) |
| 2.7 | `has_portrait` expression | `services/schema.py` constant | 7 copies |
| 2.8 | `escapeHtml` / `api()` / `openSourceRef` / conditions table | `static/ui/*.js`, `static/data/conditions.js` | 5 escape impls, 130 hand-rolled fetches, 3 `openSourceRef`, drifted condition text |

**Gate:** each sub-item migrates call sites in the same commit; `tests/test_rules_service.py` + existing suites; visually verify sheet + DM page after 2.8.

---

## Stage 3 — Kill the import cycle (`core/` extraction)

`main.py` is the root of the import graph (`from main import …` in 18 modules), papered over by deferring `include_router` into `lifespan()`.

- `core/db.py` — `DB_PATH`, `get_db`
- `core/auth.py` — `get_current_user`, `require_user`, `_require_owned`, `_user_filter`
- `core/data.py` — SRD registries, `_get_source_slug_map`
- `core/templating.py` — `_render`, `static_asset_version`
- move leftover trap CRUD → `routes/dm/traps.py`, reference routes → `routes/reference.py`
- delete the deferred-import block; register routers at import time

**Risk: high** (touches every module) but unblocks Stages 4–5. Do it alone, no parallel work.

---

## Stage 4 — God-module splits

| Module | Split into |
|---|---|
| `routes/dm.py` 3,769 (54 routes, 5 sub-apps) | `routes/dm/{encounters,campaigns,npcs,ai_builders,library,page}.py` + `services/encounter_ai.py` (out of `dm_ai_build_encounter`, 581 L) + `services/npc_conversion.py` |
| `routes/characters/leveling.py` 1,703 / 7 funcs | thin handlers + `services/leveling/{choices,apply,delevel,payload}.py` (`apply_level_up` 534 L → dispatch table keyed by class) |
| `routes/characters/sheet.py` 2,614 | `services/sheet_context.py` (`character_sheet` 729 L → VM builder), `services/item_effects.py`, `services/advantage.py`; handlers ~300 L |
| `services/data_loader.py` 1,406 | `services/ingest/{races,classes,spells,items,monsters,feats,sources}.py` + 60-line orchestrator (`load_manual_data` is 1,048 L) |
| `routes/maps.py` 1,471 | `routes/maps/{crud,tokens,stream,images}.py` + `services/map_tokens.py` |
| `pdf_generator.py` 2,216 | `pdf/layout.py` (`Rect`/`TextBox` dataclasses replace 9–11 positional params) + `pdf/pages/*` |

One module per commit. `test_progression_matrix`, `test_sheet_render`, `test_data_loader_service` are the guards.

---

## Stage 5 — Front-end structure

1. `static/ui/escape.js` (5 escape impls → 1) — enables everything else
2. `static/ui/events.js` — `data-action` delegation; drop 379 globals (per page: DM page then sheet page, they don't load each other)
3. `static/api.js` — 130 fetch sites → one wrapper with typed errors
4. `static/data/conditions.js` — fixes already-drifted condition text
5. page modules: `static/pages/{campaign-detail,table,dashboard}.js`; then `static/dm/*`, `static/sheet/*`
6. `type="module"` **last**, per page — `on*=` attributes cannot see module scope, so step 2 must land first
7. reference blobs (1.7 MB JS-parsed JSON) → `.json` + lazy fetch, keeping `?v=` cache-busting parity

---

## Housekeeping (low risk, any time)

- delete dead templates `templates/_sheet_top.html`, `templates/_sheet_sidebar.html` (nothing includes them; already drifted 90.7 %)
- drop ~20 unused imports (pyflakes list) and 17 unreferenced symbols
- archive spent scripts: `find_missing_bg{2,3,4,5}.py`, `test_matching.py`, `analyze_gaps.py`; untrack `.hermes/archive/`
- reconcile `_slug_displays` (`main.py:2397`) vs `book_title_map` (`ingest_manual.py:3067`) — `BGDIA`/`GoS` vs `TTLT` disagree
- collapse `spells.py:711` spells-known table onto `get_spells_known_max`
- bug: `routes/dm.py:1666` `"easy": MEDIUM_XP` (easy pays medium)

## Non-goals

- No ORM migration, no framework swap, no build step for JS.
- `ingest_manual.py` (3,374 L, CLI tooling) stays as-is this round.

## Verification convention

Every stage: targeted `pytest -q tests/…` → full `pytest -q` → `sudo -n systemctl restart dnd-character-manager` → `curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:8300/api/health` → commit → push.
