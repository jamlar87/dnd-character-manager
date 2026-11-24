# D&D App Scope — `dnd-character-manager` + `ai-dm`

**Purpose of this document.** Hand this file to any LLM agent (or human) that must understand, extend, debug, or propose features for the two D&D applications running on this host. It describes what exists **now**, where the code is, how the pieces fit, what the invariants are, and how to add to them without breaking things.

**Generated:** 2026-11-20 · **Host:** Debian 13 (`192.168.1.54`), user `james`
**Sources of truth used:** live source tree, live systemd units, live SQLite schemas, git HEAD.
**Git HEADs at time of writing:** `dnd-character-manager` = `f76d18b` (2026-11-20), `ai-dm` = `c4a0d29` (2026-11-20).

---

## 0. How an agent should use this document

### 0.1 Epistemic rule

This document is a map, **not** the territory. Every claim here was read from code or a live service, but code moves. Before you change behaviour:

```bash
cd /home/james/dnd-character-manager && git log --oneline -15
cd /home/james/ai-dm              && git log --oneline -15
```

If a statement here disagrees with the file, **the file wins**. Verify with `read_file` / `search_files` before patching.

### 0.2 Fast orientation commands (read-only, safe)

```bash
# what is running, on what port, from which interpreter
systemctl show dnd-character-manager -p ExecStart -p WorkingDirectory
systemctl --user show ai-dm -p ExecStart -p WorkingDirectory
ss -lntp | grep -E '8300|8700'

# module inventory with docstrings (the fastest way to learn either app)
cd /home/james/ai-dm
python3 - <<'PY'
import ast, pathlib
for p in sorted(pathlib.Path('dmcore').glob('*.py')) + [pathlib.Path('app.py')]:
    t = ast.parse(p.read_text())
    doc = (ast.get_docstring(t) or '').splitlines()[:1]
    fns = [n.name for n in t.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    print(f"{p} ({len(fns)} fns) :: {doc[0] if doc else ''}")
PY

# live schema
sqlite3 /home/james/dnd-character-manager/data/characters.db '.tables'
sqlite3 /home/james/ai-dm/data/dm.db '.tables'
```

### 0.3 Hard constraints on this host

* **Default work host is this Debian box.** Never route work to the Windows or Bazzite machines for these apps.
* `sudo` needs a password on Debian, **except** `systemctl restart/start/stop dnd-character-manager` and the `combined` unit, which are NOPASSWD.
* `ai-dm` and `sta-character-manager` are **user** units → always `systemctl --user …`, no sudo.
* The shell injects `PYTHONPATH` pointing at Hermes' Python 3.14 site-packages. That breaks venv imports and pytest. Never prefix a command with `env …` on this host — it silently swallows stdout. Clear the variable inline instead (`PYTHONPATH= ./.venv/bin/python …`) or just call the venv interpreter directly (`./.venv/bin/python`), which is the reliable route.
* Both apps are **no-build-step**: edit `.py`, `.html`, `.css`, `.js` and restart. No npm, no bundler, no migration framework beyond hand-written `_migrate` / `ensure_schema` helpers.

### 0.4 Defining "done"

You are not done when the code compiles. You are done when:

1. `./.venv/bin/python -m pytest -q` (or the app's test files) is green, or you can state honestly which tests are red and why.
2. The route returns the expected payload against the **live** service on its real port.
3. You checked the page in a browser (the user's browser is Firefox) — not only `curl`.
4. For anything user-visible, you verified through the Cloudflare hostname, not only `127.0.0.1`.

---

## 1. The two apps at a glance

| | **D&D Character Manager** | **AI DM** |
|---|---|---|
| Role | The *bookkeeping* app: characters, sheets, campaigns, maps, encounters | The *table* app: runs a live 5e session, narrates, adjudicates |
| Path | `/home/james/dnd-character-manager` | `/home/james/ai-dm` |
| Port | `8300` | `8700` (`AIDM_PORT`) |
| Public URL | `https://characters.jamlarnet.stream` | `https://dm.jamlarnet.stream` |
| Service | system unit `dnd-character-manager.service` | **user** unit `ai-dm.service` |
| Start command | `.venv/bin/python main.py` | `run.sh` → `uvicorn app:app --host 0.0.0.0 --port 8700` |
| Python | `.venv/` (app-local) | `.venv/` (app-local), `PYTHONPATH` unset in `run.sh` |
| DB | `data/characters.db` (18 user tables) | `data/dm.db` (16 user tables + FTS5 index) |
| Python LOC | ~74,800 across `main.py`, `core/`, `routes/`, `services/` | ~36,700 across `app.py`, `dmcore/`, `scripts/` |
| JS LOC | ~20,100 in `static/` | ~2,000 (`app.js` 85 KB is the bulk) |
| Tests | 124 files in `tests/` | 60 `tests_*.py` files, ~905 test functions |
| Auth | Multi-user: email + password, bcrypt, server sessions, CSRF | None — single-operator, LAN/tunnel-only |
| LLM use | Side features: NPC generation, portraits, encounter builder, manual Q&A | Core: every turn (intent parsing, narration), plus campaign spine, summaries |
| Build step | none | none |

**The relationship.** They are separate processes with separate databases, joined by an explicit bridge:

* AI DM **pulls** characters *from* the Character Manager (`dmcore/cm_sync.pull`, `dmcore/characters.import_from_character_manager`, `GET /api/roster`).
* AI DM **pushes** state *back* (`dmcore/cm_sync.push`, `push_gear`, `publish_portrait`) — HP, conditions, slots, inspiration, gear, XP, portrait.
* Sync is fingerprint-based (`characters.sheet_fingerprint`, `sheet_synced_at`) so it is idempotent and two-way without a message bus.
* Both read a shared SRD/reference corpus and both can call the same local-model endpoints.

Neither app imports the other. The only coupling is HTTP + the character DB path that `cm_sync` opens.

---

## 2. Shared engineering conventions (both apps)

1. **FastAPI + Jinja2 + vanilla JS + SQLite.** No React, no ORM, no bundler, no TypeScript. Templates are large single files with `{% include %}` partials. All interactivity is `fetch()` against JSON routes.
2. **Raw `sqlite3`.** WAL mode for AI DM; one connection per call; row dicts. Schema changes are additive, hand-written and idempotent (`_migrate`, `ensure_schema`, `PRAGMA table_info` guards). There is no Alembic — never assume a migration runner.
3. **Server renders truth, client renders state.** Any number the player sees (AC, HP, spell slots, distance, DC) is computed in Python, not JS.
4. **SSE for live updates** (`text/event-stream`), polling nowhere on the hot path.
5. **Deterministic core, LLM at the edges.** Both apps keep rules arithmetic in pure Python. The LLM writes prose and proposals; it never owns a number.
6. **Static assets are cache-busted by restart.** Jinja templates are cached in production; a template edit needs `systemctl restart`.
7. **`ruff` config in each repo**; both are formatted for 100-col-ish reads. Keep the house style: docstring first line = one sentence, no type stubs beyond `from __future__ import annotations`.

---

## Contents

| Part | Section | What is in it |
|---|---|---|
| 0–2 | Orientation | How to use this doc, the two apps at a glance, shared conventions |
| **A** | `dnd-character-manager` | A1 what it is · A2 runtime/ops · A3 repository layout · A4 module map · A5 DB schema (19 tables) · A6 frontend · A7 HTTP surface · A8 security · A9 pitfalls · A10 dev workflow · A11 gaps |
| **B** | `ai-dm` | B1 what it is · B2 runtime/ops · B3 layout · B4 `dmcore` module map (49 modules) · B5 DB schema (15 tables + FTS5) · B6 turn engine + SSE · B7 rules coverage · B8 LLM routing · B9 voice · B10 portraits · B11 frontend · B12 API · B13 security · B14 pitfalls · B15 dev workflow · B16 gaps |
| **C** | Integration | How the two apps couple; which changes must land in both |
| **D** | Operations | Units, ports, tunnel, backups, disk, logs, health |
| **E** | Fast index | "Where do I change X?" table |
| **F** | Verification log | Exactly what was read from the live machine |
| **Appendix A** | CM endpoints | 186 routes, module by module |
| **Appendix B** | AI DM endpoints | 49 routes |
| **Appendix C** | CM file inventory | Every Python file: path, lines, stated purpose + `C2` templates, `C3` static assets |
| **Appendix D** | AI DM file inventory | Root modules, tools, scripts, templates, static, data |
| **Appendix E** | Glossary | Campaign, spine, scene, turn, dice gate, segments, … |

---

# PART A — D&D Character Manager

## A1. What it is

A multi-user web app for **owning and operating 5e characters and the DM side of a table**: build characters level 1–20, run the sheet live (HP, slots, conditions, rests, concentration), drive campaigns, place maps with fog and tokens, build encounters, and generate NPCs/traps/portraits with AI. It is the app players leave open during a session; it is also the DM's prep tool.

Live data at time of writing: 27 characters, 7 users, 5 campaigns, 306 maps, 635 character spells, 49 encounter NPCs, 2 custom traps.

## A2. Runtime, deployment, operations

```
/etc/systemd/system/dnd-character-manager.service   → .venv/bin/python main.py   (User=james)
/etc/systemd/system/dnd-backup.service + .timer     → daily DB backup
~/.cloudflared/config.yml                           → characters.jamlarnet.stream → 127.0.0.1:8300
```

* **Restart:** `sudo -n systemctl restart dnd-character-manager` (NOPASSWD, allowed).
* **Logs:** `journalctl -u dnd-character-manager -n 200 --no-pager`.
* **Health:** `GET /api/health`, `GET /api/health?deep=1` (model/tool availability).
* **Env:** `.env` in the app root, loaded by `core/config.py`. Keys present: `GOOGLE_API_KEY` (Gemini/vision), `OPENROUTER_API_KEY`, `STABLEHORDE_API_KEY`, `PORTRAIT_PROVIDER`, `COMFY_URL`, `COMFY_MODEL`. Portrait generation defaults to the **local ComfyUI on Bazzite** (`:8188`, rootless podman, `ssh bazzite 'podman start comfyui'`) because the public Stable Horde queue measured 6–92 minutes per image versus 14–25 s locally.
* **Data:** `data/characters.db` (SQLite WAL), `static/maps/` (312 files, 306 of them referenced by `dm_maps` rows, 3–5 MB each — the bulk of the repo's disk), OCR text caches (git-ignored), `data/` manual corpus for the reference library.
* **PWA:** `static/sw.js`, `static/pwa.js`, `GET /manifest.webmanifest`, `GET /offline` — the sheet is installable and works offline for read.

## A3. Repository layout (verified from the tree)

```
/home/james/dnd-character-manager/
├── main.py                 1105 L  app factory, middleware (CSRF/session/static), page routes,
│                                   /api/health, PWA manifest+sw, facade re-exports for tests
├── core/                     ~2.2k L  shared primitives extracted out of main.py
│   ├── config.py             40 L   paths, .env, SECRET_KEY, SESSION_TTL_DAYS, APP_ENV
│   ├── constants.py          65 L   named constants, re-exported from core/data.py
│   ├── data.py             1880 L   static game data, SRD caches, ingested-manual registries
│   ├── db.py                 28 L   FastAPI DB dependency, DB path, JSON-column helper
│   ├── features.py           28 L   rule helpers combining data tables + levelling service
│   ├── http.py              110 L   Jinja env, render helper, static-asset cache busting
│   └── security.py          121 L   bcrypt, cookie sessions, ownership checks, CSRF
├── routes/                  186 endpoints total across the package
│   ├── auth.py              217 L   9 ep   register / login / logout / reset / change password
│   ├── maps.py             1461 L  23 ep   battle maps, tokens, snapshots, player mirror, SSE
│   ├── pack.py              428 L   2 ep   campaign packs — one JSON file per campaign
│   ├── table.py             666 L  10 ep   campaign roll feed, player table view, SSE
│   ├── undo.py               85 L   2 ep   peek + revert last action
│   ├── ref_images.py         82 L   2 ep   reference art for monsters / items / NPCs
│   ├── schemas.py           102 L   0 ep   Pydantic request models
│   ├── characters/          ~10.0k L  45 ep
│   │   ├── all.py          2480 L     composite router + shared PHB registries
│   │   ├── creation.py      978 L   4 ep   /create page, create API, NPC→character
│   │   ├── leveling.py     1763 L   4 ep   level-up preview/apply, de-level
│   │   ├── spells.py        904 L  15 ep   add/unlearn/prepare, magic initiate, slots
│   │   ├── campaign.py      705 L   8 ep   campaign team items (shared party stash)
│   │   ├── relationships.py 157 L   5 ep   bonds CRUD + AI generation
│   │   ├── ai_routes.py    1234 L   6 ep   AI generation, background, history, portrait
│   │   ├── transfer.py      179 L   2 ep   JSON export / import
│   │   ├── pdf.py            94 L   1 ep   character PDF
│   │   ├── helpers.py      1236 L   0 ep   monster cache, OCR search, encounter building
│   │   └── sheet/          ~2.5k L   18 ep  the sheet: view, save, inventory, feats, api
│   └── dm/                  ~4.0k L   54 ep
│       ├── page.py          588 L   2 ep   /dm-tools page + campaign page
│       ├── encounters.py   1409 L  15 ep   AI build, copy, initiative, combat state
│       ├── npcs.py          656 L   9 ep   NPC library, portraits, AI NPC builder
│       ├── campaigns.py     496 L  11 ep   CRUD, roster, notes, XP
│       ├── api.py           230 L   7 ep   manual search/summary, live HP
│       ├── monsters.py      264 L   5 ep   read-only monster/spell palette
│       ├── traps.py          98 L   1 ep   AI trap builder
│       ├── widgets.py       116 L   4 ep   encounter + campaign widgets
│       └── _shared.py        62 L   0 ep   shared data/helpers
├── services/               ~13.5k L  business logic (31 modules + 11 loaders)
├── templates/              21 files, 520 KB total (sheet.html alone is 218 KB)
├── static/                 850 MB total — 312 map images dominate
│   ├── (root)              dm-library.js 1.42 MB, create-consts.js 296 KB, sheet-ref.js 272 KB,
│   │                       vtt.js 84 KB, sheet.css 46 KB, layout.css 22 KB, + wizard JS
│   ├── dm/                 16 files  DM Tools page scripts, one per panel
│   ├── sheet/              12 files  sheet tab scripts (edit, equip, features, inventory, …)
│   ├── pages/               5 files  campaign-detail, dashboard, map-chrome, page-utils
│   ├── ui/                  7 files  api.js, dom.js, events.js, conditions.js, shell.js, …
│   ├── maps/              312 files  uploaded battle maps
│   ├── ref-portraits/       3 shards generated reference art
│   ├── icons/               4 files
│   └── data/                1 generated asset (reference_assets.json)
├── tests/                  124 files (test_*.py + conftest.py + support helpers)
├── data.py                 1362 L  D&D 5e data constants (pure data, no logic)
├── ingest_manual.py        3382 L  57 fn  the PDF → structured-data ingestion engine
├── summon_templates.py      836 L  summonable creature templates
├── pdf_generator.py          53 L  back-compat facade over the pdf/ package
├── pdf/                     9 modules  official / sheet / spellbook / summary / layout / text
├── scripts/                60 scripts  ingest sweeps, page maps, audits, portrait batch runs
├── manuals/                PDF corpus (PHB, DMG, MM, Tasha, Mordenkainen, SCAG, Ravnica, …)
├── docs/                   OPS.md + plans/ (audit remediation, refactor plans)
├── engine/                 empty (legacy placeholder — no code)
└── data/                   characters.db, manual_data/, srd_cache/, page_maps/, backups/, logs
```

## A4. Module map — what each layer owns

### A4.1 `core/` (7 modules, thin)

| Module | Responsibility |
|---|---|
| `config.py` | Filesystem paths (`HERE`, `DATA_DIR`, `STATIC`, `TEMPLATES`, `MANUAL_DATA`, `SRD_CACHE`, `PAGE_MAP_DIR`, `DB_PATH`), `.env` parsing, `SECRET_KEY`, `SESSION_TTL_DAYS`, `APP_ENV` |
| `constants.py` | Named constants / small tables, re-exported from `core/data.py` so importers can pick either |
| `data.py` | 1880 L of static game data: class feature tables, ASI flexibility rules, item index, skill maps, ingested-manual registries |
| `db.py` | FastAPI dependency `get_db()`, the DB path, `json_col()` helper for JSON-in-TEXT columns |
| `features.py` | Rule helpers that join `core/data.py` tables to `services/leveling.py` |
| `http.py` | Jinja environment, `render()` helper, static-asset cache busting (`?v=<hash>`) |
| `security.py` | bcrypt hash/verify, cookie session issue + expiry, per-row ownership checks, CSRF compare |

Schema creation and migrations do **not** live in `core/db.py` — they are in `services/db_schema.py` (647 L) and validated by `services/schema.py` (97 L).

### A4.2 `routes/` (186 endpoints, thin: parse → service → JSON)

Grouped by who uses them. Full per-endpoint list is in **Appendix A**.

**Auth & pages** — `auth.py` (9 ep): register, login, logout, reset-password, change-password. Pages are in `main.py`: `/`, `/dashboard`, `/create`, `/dm-tools`, plus the PWA routes.

**Character manager** — `characters/`:
* `all.py` mounts the composite router and owns the shared PHB registries (class/race/background option tables the wizard and sheet read).
* `creation.py` — the wizard page and `POST /api/character/create`, plus NPC→character builds.
* `leveling.py` — level-up preview, applied levelling (ASI, feats, subclass, spell picks), de-level reversal.
* `spells.py` — add/unlearn/prepare spells, slot bookkeeping, Magic Initiate sub-flow, available-spell lists.
* `sheet/` — the sheet itself: `view.py` builds the reference payload, `update.py` is the save path, `inventory.py` equipment/attunement, `feats.py` feat tables, `api.py` 14 live-session JSON endpoints (attacks, summons, conditions, HP sync, charges).
* `ai_routes.py` — AI generation, background prose, history, portraits.
* `transfer.py` / `pdf.py` — JSON export/import, PDF rendering.
* `helpers.py` — shared monster cache, OCR search, encounter-building helpers. No endpoints.

**DM tools** — `dm/`:
* `page.py` — `/dm-tools` and the campaign page.
* `campaigns.py` — campaign CRUD, roster membership, notes, XP awards (11 ep).
* `encounters.py` — the biggest DM module: AI encounter building, copy, membership, initiative, combat-state blob (15 ep).
* `npcs.py` — NPC library CRUD, portraits, AI NPC builder (9 ep).
* `monsters.py` — read-only monster/spell reference for the palette.
* `traps.py` — AI trap builder. `widgets.py` — encounter/campaign widgets.
* `api.py` — manual search/summarise and live HP changes.

**Maps** — `maps.py` (23 ep): map CRUD, image upload/replace, grid align, layers, tokens (add/update/delete/bulk), fog snapshot/restore, camera, player key, `/dm-map/{id}` editor + `/dm-map/{id}/player` mirrored view + SSE state stream, spawn-encounter from tokens, thumbnails, map scenes. Client side is `static/vtt.js` + `static/dm/maps.js`.

**Campaign pack** — `pack.py` (2 ep): a whole campaign in one JSON file, import and export.

**Table channel** — `table.py` (10 ep): campaign-scoped roll feed shared by DM and players, `/table/{player_key}` page, SSE stream, roll submission.

**Undo / refs** — `undo.py` (2 ep) over `action_undo`; `ref_images.py` (2 ep) for reference art.

### A4.3 `services/` (31 modules + 11 loaders, ~13.5k L — the real brain)

| Module | L | Responsibility |
|---|---|---|
| `leveling.py` | 1131 | Level-up deltas, feature grants, ASI/feat application, de-level reversal, spell slots |
| `ref_portraits.py` | 1931 | Reference art for monsters, items and the manual NPC library |
| `portraits.py` | 936 | Portrait prompts + image generation shared by routes and scripts |
| `db_schema.py` | 647 | Schema creation and hand-written additive migrations |
| `combat.py` | 630 | Combat/racial-trait helpers — traits, attacks, armour proficiency |
| `entity_search.py` | 710 | Internal entity search (nav-search "Internal data" section) |
| `items.py` | 435 | Item-index lookup, description/type generation, curse splitting |
| `topology_verify.py` | 390 | Generate-then-verify portraits: resample until a vision model confirms topology |
| `pose_skeleton.py` | 354 | OpenPose-style skeleton the SDXL ControlNet follows |
| `map_grid_align.py` | 245 | Auto-align an overlay grid to the grid printed on the map art |
| `images.py` | 240 | Portrait image helpers shared by character and NPC routes |
| `data_loader.py` | 181 | Import surface for the loaders + startup manual-data merge |
| `undo.py` | 179 | Snapshot/restore of row sets keyed by `action_undo` |
| `limb_placement.py` | 147 | Turn a limb COUNT into a limb LAYOUT before prompting the model |
| `login_guard.py` | 143 | In-memory sliding-window throttle for credential endpoints |
| `campaign_access.py` | 115 | Who may touch a campaign or a character inside it |
| `map_grid.py` | 99 | Hex/square grid maths — the Python side of `static/grid.js` |
| `events.py` | 103 | In-process fan-out for the table channel (roll feed + live push) |
| `schema.py` | 97 | Required-SQLite-schema validation at startup |
| `sources.py` | 85 | Source-attribution hygiene for reference records |
| `sessions.py` / `users.py` | 72 / 40 | Session persistence, user repository ops |
| `reference_assets.py` | 72 | Global reference tables served as a cached asset |
| `dm_portraits.py` | 75 | Courier to the AI DM's portrait painter |
| `validation.py` | 65 | Field bounds for client-posted numbers |
| `rules.py` | 45 | 5e arithmetic shared by more than one part |
| `assets.py` | 30 | Writer for generated `/static/<name>?v=<hash>` assets |
| `text.py` | 28 | Normalisation, slugs, snippet highlighting |
| `auth.py` | 17 | Auth primitives independent of FastAPI |
| `db.py` | 13 | Small connection boundary |
| `loaders/` | 11 files | `files`, `indexes`, `spells`, `races`, `subclasses`, `feats`, `backgrounds`, `traps`, `sources`, `limited_use` — manual ingestion into the reference registries |

**Load-bearing service boundaries:** `services/schema.py` validates at startup — a missing table fails boot loudly. `services/events.py` is in-process only, so SSE fan-out dies with a single worker (do not add a second uvicorn worker without a real broker).

## A5. Database schema (`data/characters.db`, 19 tables)

Counts are live rows at time of writing; they tell you what is exercised and what is dead code paths.

| Table | Rows | Notes |
|---|---|---|
| `users` | 7 | `email`, `password_hash` (bcrypt), `is_admin`, `favorites` (JSON) |
| `sessions` | 3 | `token`, `expires_at`, `user_id` — server-side sessions, cookie holds token |
| `characters` | 27 | **81 columns** — the whole sheet: identity, six abilities, HP/temp/AC/speed/prof, hit dice + used, death saves, `skills`, tool/weapon/armor profs, languages, `features`, `inventory`, `equipped`, `notes`, `personality`, `backstory`, `feature_data`, `attacks_data`, `spell_slot_data`, `passive_perception`, `inspiration`, `exhaustion`, `portrait_url`, `portrait_prompt`, `save_proficiencies`, resist/immune/vulnerable, `condition_immunities`, `background_data`, `spell_slots_used`, `class_levels`, `attuned_items`, `dragonborn_ancestry`, `cp`/`gp`, `expertise_skills`, `fighting_style`, `metamagic`, `invocations`, `pact_boon`, `maneuvers`, `magical_secrets`, `totem_spirits`, `hunters_prey`, `infusions`, `asi_history`, `combat_notes`, `metamagic_history`, `summons`, `conditions`, `favored_enemies`, `favored_terrains`, `personality_data`, `shared`, `journal`, `advantage_map`, `concentration`, `xp` |
| `character_spells` | 635 | Per-character spell rows: `spell_level`, `prepared`, `slots_max`, `slots_used`, `source` (class/feat/race) |
| `character_relationships` | 10 | NPC bonds: `relationship_type`, `description`, `prompt`, `npc_data`, `ai_generated` |
| `action_undo` | 10 | `kind`, `label`, `entities` (JSON snapshots), `undone_at`, `meta` — powers the undo stack |
| `dm_campaigns` | 5 | `party_level`, `party_size`, `quests`, `locations`, `characters`, `npcs`, `widgets`, `player_key`, notes |
| `dm_campaign_characters` | 0 | Join table (campaign ↔ character, `status`, `notes`) — **legacy, the JSON column in `dm_campaigns` is what the app reads** |
| `dm_encounters` | 7 | `difficulty`, `status`, `xp_total`, `combat_state` (JSON: round, turn index), `shared`, `widgets` |
| `dm_encounter_npcs` | 49 | Per-encounter combatant: `initiative`, `hp_current/max`, `ac`, `defeated`, `spell_slots_used`, `creature_data` (full statblock JSON) |
| `dm_npcs` | 1 | Reusable NPC library: 36 columns mirroring `characters` + `is_enemy`, `is_party_npc`, `role`, `faction`, `xp_reward`, `source_book`, `source_page` |
| `dm_maps` | 306 | `image_path`, `grid_type`/`grid_size`/offsets, `parent_map_id` (map tree), `camera`, `fog`+`fog_on`, `draw_data`, `player_key`, `feet_per_cell`, `image_w/h`, `source_manual`/`source_page`/`grid_source`, `rotation`, `hex_turn` |
| `dm_map_tokens` | 16 | `x`,`y`,`w`,`h`, `kind`, `ref_name`, `label`, `hp_*`, `hidden`, `z`, `encounter_en_id`, `character_id` |
| `dm_map_scenes` | 0 | Saved snapshots per map: `snapshot` JSON |
| `dm_custom_traps` | 2 | User traps: `danger`, `trigger`, detection/disarm DC+skill+detail, `effect`, `save_dc`/`ability`, `damage`/type, `area` |
| `roll_log` | 0 | Dice audit: `who`, `source`, `expression`, `results` JSON, `total`, `hidden` |
| `campaign_team_items` | 7 | Shared party stash with `claim` (see `POST …/{item_id}/claim`) |
| `dm_xp_log` | 0 | XP awards with `recipients` and `reversed` |
| `schema_migrations` | 1 | `version`, `applied_at` |

**Schema rules.** JSON-in-TEXT columns are normal here (`inventory`, `equipped`, `feature_data`, `attacks_data`, `combat_state`, `snapshot`, `draw_data`). When you add a column, add it to the `CREATE TABLE` **and** to the `PRAGMA table_info`-guarded migration in `core/db.py`, and give old rows a sane default — 27 characters with 81 columns means partial rows exist in the wild.

## A6. Frontend

### A6.1 Templates (sizes are a proxy for complexity)

| File | Size | What it is |
|---|---|---|
| `sheet.html` | 218 KB | The character sheet. One page, all tabs, all modals' hosts. The single largest artifact in either app. |
| `dm_tools.html` | 82 KB | DM workspace: Campaign Builder, Character Builder, AI Encounter Builder, Create Custom Trap |
| `create.html` | 34 KB | Multi-step character creation wizard |
| `_sheet_sidebar.html` | 31 KB | Persistent left rail of the sheet (vitals, saves, skills, attacks) |
| `_sheet_top.html` | 16 KB | Header strip: portrait, name, class/level, HP, AC, inspiration |
| `campaign_detail.html` | 15 KB | Campaign overview: party, quests, locations, session notes |
| `table.html` | 14 KB | Player-facing table view (keyed by `player_key`) |
| `dashboard.html` | 12 KB | Character list, favorites, create/import entry |
| `landing.html` | 11 KB | Logged-out marketing page |
| `map.html` / `map_player.html` | 9.5 / 5.3 KB | DM map editor / player mirror |
| `_sheet_header.html`, `_sheet_modals.html`, `_sheet_scripts.html`, `_char_portrait.html`, `_map_toolbar.html`, `layout.html`, `login/register/reset/change_password.html` | small | Partials and auth pages |

**Sheet tabs** (`data-tab` values in `sheet.html`): `actions`, `features`, `spells`, `inventory`, `history`. Additional panes rendered as sections: **Notes**, **Relationships**. Tabs are plain JS show/hide over server-rendered DOM; state survives via the page's own JS, not client routing.

### A6.2 Static JS

| File | Size | Role |
|---|---|---|
| `dm-library.js` | 1.42 MB | Client-side compendium: every monster/spell/item/class table the DM screens need, pre-bundled so the DM tools work without round-trips |
| `create-consts.js` | 296 KB | Creation wizard constants (class/race/background option trees) |
| `sheet-ref.js` | 272 KB | Sheet reference data (feature text, spell blurbs) |
| `vtt.js` | 84 KB | The map virtual tabletop: canvas renderer, tokens, fog brush, measuring, drag |
| `sheet.css` / `layout.css` | 46 / 22 KB | Sheet and global styles |
| `create-core.js`, `create-abilities.js`, `create-raceclass.js`, `create-pickers.js`, `create-spells.js`, `create-skills.js`, `create-portrait.js`, `create-submit.js` | 4–31 KB | Wizard stages, one file per step |
| `sheet.js` | 22 KB | Sheet interactions (tabs, roll buttons, charge/feature use, condition chips) |
| `entity-search.js` | 19 KB | Typeahead across entities |
| `source-filter.js`, `source-slug-map.js` | 16 / 30 KB | Filter content by sourcebook; slug↔book name map |
| `dm_tools.js` | 11 KB | DM tools page wiring |
| `widgets.js` | 9.6 KB | Reusable UI widgets (chips, drawers, toasts) |
| `rollfeed.js` | 9.4 KB | Live roll feed (SSE consumer) |
| `char-portrait.js` | 9.2 KB | Portrait render/paint controls |
| `dice.js` | 8 KB | Dice expression roller UI |
| `grid.js` | 4 KB | Grid helpers for the map |
| `sw.js`, `pwa.js` | small | Service worker + install prompt |

**Static subdirectories** (loaded per page, not bundled): `static/dm/` 16 files (one per DM panel: `actions`, `bootstrap`, `builder`, `campaigns`, `combat`, `combat-ui`, `combat-conditions`, `encounter-open`, `encounters`, `items`, `loot`, `maps`, `npcs`, `reference`, `rollfeed`, `traps`); `static/sheet/` 12 files (`edit`, `equip`, `features`, `inventory`, `inventory-ui`, `levelup`, `live`, `popups`, `spells`, `status`); `static/pages/` 5 (`campaign-detail`, `dashboard`, `map-chrome`, `map.css`, `page-utils`); `static/ui/` 7 (`api`, `dom`, `events`, `nav-filter`, `shell`, `conditions`, `page-utils`-adjacent helpers); `static/maps/` 312 uploaded images; `static/icons/`, `static/ref-portraits/`, `static/data/reference_assets.json` generated.

### A6.3 UI surface inventory (what a user can actually do)

**Character lifecycle**
* Create via **wizard**: race/subrace → class/subclass → background → ability scores (point buy / standard array / roll / manual, with the flexible-ASI rules from `core/data.py`) → skills → spells/cantrips → equipment → portrait → review.
* **Import** a character JSON (`POST /api/character/import`), or **build from an NPC** (`POST /api/build-from-npc`), or **copy a DM NPC** (`POST /api/dm/npc/{id}/build-to-character`).
* **Level up**: preview (`/api/character/{id}/level-up-info`) → apply (`apply-level-up`) with ASI/feat choice, spell picks, subclass features, multiclass slots; **de-level** with `/api/character/{id}/de-level-info` + `de-level`.
* **Edit** any field in place (`/api/character/{id}/update`), edit ASI history and expertise separately, re-pick fighting style / metamagic / invocations / maneuvers / magical secrets / totem spirits / favored enemy+terrain / infusions / pact boon / hunter's prey.
* **Share** to the party (`toggle-share`), **favorite**, **delete**, **export JSON**, **export PDF** (`/api/character/{id}/pdf`, from `tests/test_pdf_generation.py`), **print summary PDF** (`/api/ai/summary/pdf`).

**Sheet operations**
* HP/temp HP damage & heal, death saves, exhaustion levels, inspiration gain/spend.
* Concentration tracking (`/concentration`), condition chips (`/conditions` add/clear), advantage map.
* Spell management: prepared toggles, slots used, add/unlearn, available-spells by class/level/source, AI spell suggestions (`/ai-spells`), Magic Initiate sub-flow (`/magic-initiate`, `/reset`, `/use`).
* Feature resources: `use-feature`, `spend-charge`, `reload-charge`, `reset-features`.
* Attunement toggles, equipment slots, inventory add/remove, currency (cp/gp), summons with HP tracking (`/summons`, `/update-summon-hp`).
* Rests are handled through features/HP in this app (the full rest engine lives in AI DM).
* **Journal** per character (`journal` column) and **combat notes**.
* **Undo**: every destructive sheet action writes an `action_undo` row; `/api/undo/peek` shows what would be undone, `/api/undo` reverts.

**AI side-features (character level)**
* `POST /api/ai/generate` — freeform generation (personality, hooks).
* `POST /api/ai/generate-background` — backstory prose from the sheet.
* `POST /api/ai/portrait` + `POST /api/character/portrait` — prompt → image (local ComfyUI, cached).
* `POST /api/character/{id}/generate-history` — in-world history.
* `POST /api/character/{id}/relationships/generate` — NPC bonds with `npc_data` so a bond can become a statblock.
* `POST /api/ai/build` — build a whole character from a concept.

**Table / party surface**
* `/table/{player_key}` — a stripped player view: party HP, shared rolls, initiative; SSE `/api/table/{key}/stream`; `/api/table/{key}/roll` accepts player dice.
* Team stash: `GET/POST/PUT/DELETE /api/campaign/{id}/team-items`, `POST …/{item_id}/claim` — shared loot that any player can claim into their sheet.
* Roll log + live roll feed (`rollfeed.js` over SSE).

**DM surface**
* Campaigns: create/import, edit, delete, party management, NPC attach, XP award with reversal, widgets, export.
* Encounters: create from scratch or copy, add SRD monster (`/api/dm/monster/{index}`, search, by-CR), add NPC, roll initiative, reorder, `combat-state` blob (round/turn/HP per combatant), share to table, delete.
* **AI Encounter Builder** (`/api/dm/ai/build-encounter`) — party profile (`/api/dm/ai/party-profile`) + budget → proposed roster.
* **AI NPC Builder** (`/api/dm/ai/build-npc`), **build-from-gaps** (fills missing roles in a campaign), **AI Trap Builder** (`/api/dm/ai/build-trap`).
* Maps: create, upload image, auto-align grid, set grid type/size/offset/rotation/hex orientation, feet-per-cell, layers, fog (on/off, painted + snapshots), camera, tokens (add/update/delete/bulk, HP, hidden, z-order), spawn an encounter from tokens, mirror to players via `player_key` + SSE, thumbnail, saved scenes.
* **Reference library** (`/api/reference/*`): browse manuals, titles, source map, entity lookup, stream the manual file, view reference art + stats, and `POST /api/reference/query` for AI Q&A over the corpus (`/api/dm/search-manuals`, `/summarize`).
* Roll loot (`/api/campaign/{id}/roll-loot`), sync combat HP (`/api/sync-combat-hp`).

## A7. HTTP surface — shape and conventions

186 routes: `main.py` 21, `routes/` top level 48, `characters/` 45, `characters/sheet/` 18, `dm/` 54.
The exhaustive endpoint inventory (method + path + handler) is **Appendix A**. Conventions:

| Convention | Rule |
|---|---|
| Page routes | `GET` returning Jinja HTML: `/`, `/dashboard`, `/create`, `/dm-tools`, `/campaign/{id}`, `/character/{id}`, `/dm-map/{id}`, `/dm-map/{id}/player`, `/table/{player_key}`, `/login`, `/register` |
| JSON API | Everything under `/api/...`; errors are `{"detail": "..."}` with a 4xx/5xx status |
| Auth | Session cookie set at login; every route dependency-resolves the user. Pages with no user redirect to `/login`; APIs return 401/403 |
| CSRF | POST/PUT/DELETE under `/api/` require the `X-CSRF-Token` header matching the session token. Exempt: `/login`, `/register`, `/reset-password`, `/change-password` (documented in `main.py`) |
| Ownership | `services/campaign_access.py` gated — a character/campaign is reachable only by its owner or a campaign member |
| SSE | `/dm-map/{id}/stream`, `/table/{key}/stream`, campaign live push — one stream per connection, no Last-Event-ID replay |
| Static | Served from `/static` with a `?v=<mtime-hash>` query the templates inject via `core/http.py` |
| Health | `GET /api/health` — no auth, used by the tunnel/uptime checks |
| PWA | `/manifest.webmanifest` and `/sw.js` from `main.py` |

**High-traffic hot spots** (start here when a request is slow): `characters/sheet/view.py` builds the full reference payload per page load; `dm/encounters.py` AI build; `maps.py` upload + grid align; `dm-library.js` is 1.42 MB and is the single largest client download.

## A8. Security model (must not be broken)

* **Sessions:** server-side row in `sessions`; cookie carries the token; `SESSION_TTL_DAYS` from config; expiry checked per request.
* **Passwords:** bcrypt (`core/security.py`); no plaintext anywhere; reset flow exists and is rate-limited by `services/login_guard.py`.
* **CSRF:** double-submit — `csrf_token` cookie (non-HttpOnly, `samesite=lax`, `secure` in prod) must match `x-csrf-token` header on every mutating request. Exemptions are **only** the native form posts in `CSRF_FORM_POST_PATHS = {"/login","/register","/reset-password","/change-password"}`, because a browser cannot set a header on a form submit. Static assets skip the cookie and get cacheable headers; the health probe does not mint a CSRF cookie. `static/layout.js` patches `fetch` to add the header — new mutating routes need no client work, but **raw `<form>` posts outside that exemption set will 403**.
* **Ownership:** every character/campaign/NPC/map row carries `user_id`; routes must filter by the session user. Shared/table access is via `player_key`, which is a capability, not auth.
* **Secrets:** `.env` only, never in templates or JS bundles.

## A9. Conventions and pitfalls specific to this app

1. **`main.py` is a facade.** Re-exported names are load-bearing for tests. Add imports, don't move them.
2. **Hex grids:** `grid_type` + `grid_size` + `hex_turn` + offsets, and `feet_per_cell` are the coordinate contract between `map_grid.py` and `static/grid.js`/`vtt.js`. Changing one without the other silently misplaces every token and the fog mask. Authoring guidance and the exact formulas live in the skill's hex-grid notes; re-derive from `map_grid.py` before touching it.
3. **Fog and draw data are bitmaps/JSON blobs**, not vectors of walls. A "restore snapshot" replaces the blob wholesale.
4. **Maps are large.** 312 images at 3–5 MB. Never read them into Python to inspect; use the thumbnail route.
5. **`dm_campaign_characters` is vestigial.** The party lives in `dm_campaigns.characters` JSON. Do not "fix" the empty table into the new source of truth without migrating both.
6. **Undo is snapshot-based**, so a new destructive endpoint must record snapshots or it becomes an un-undoable hole.
7. **Portrait provider is env-driven.** Any image feature must respect `PORTRAIT_PROVIDER` / `COMFY_URL` / `COMFY_MODEL` and degrade gracefully when the GPU box is off.
8. **Source filtering:** every content loader emits a `source` slug; `source-slug-map.js` must know it or the filter silently drops the content.
9. **PDF generation depends on the templates.** `tests/test_template_rendering.py` and `tests/test_pdf_generation.py` are the guard rails — run them after any `sheet.html` edit.

## A10. Dev workflow

```bash
cd /home/james/dnd-character-manager
./.venv/bin/python -m pytest -q                 # 124 test files
./.venv/bin/ruff check .                        # lint
sudo -n systemctl restart dnd-character-manager # NOPASSWD
curl -s localhost:8300/api/health
```

Adding a feature, in order: `core/db.py` column → `services/<domain>.py` logic → `routes/<domain>.py` endpoint → template/JS → test → restart → browser check.

## A11. Known gaps and rough edges (honest list)

* `dm_campaign_characters` empty while `dm_campaigns.characters` is authoritative — a cleanup with a real migration is outstanding.
* `roll_log` and `dm_xp_log` are empty: the live paths write to the encounter blob / campaign JSON instead. Two logging systems, one dead.
* `dm_map_scenes` empty — snapshot save/restore exists but is unused in practice; snapshot UX is thin.
* `dm_npcs` has 1 row while `dm_encounter_npcs` has 49 with full `creature_data` — the reusable NPC library is underused; encounter statblocks are effectively the library.
* `sheet.html` at 218 KB is past the point where targeted edits are safe without the template-render test.
* Voice/TTS does not exist here (it lives in AI DM); the sheet has no audio.
* No websocket; live updates are SSE only, and only for table feed / map player view / character feed.
* Accessibility/legibility has not been audited (contrast, focus order, mobile tab wrapping).

---

# Part B — `ai-dm` (the AI Dungeon Master)

## B1. What it is

A single-player (one campaign = one party, one human at the table) **AI Dungeon Master** for D&D 5e. The human types what their character does; the app parses intent, resolves the rules **deterministically in Python**, asks for dice when a roll genuinely decides the outcome, then narrates the result in prose. The LLM is used for exactly two things — intent shaping and prose — never for maths, damage, or legality.

Hard design rule from the codebase: *"Rules live in code; these prompts only shape intent and prose"* (`dmcore/prompts.py`). Any change that lets the model decide a number is a regression.

## B2. Runtime and deployment

| Item | Value |
|---|---|
| Path | `/home/james/ai-dm/` |
| Service | **`systemd --user` unit `ai-dm`** (not system) → `systemctl --user restart ai-dm` |
| Launch | `run.sh` → `unset PYTHONPATH; PYTHONNOUSERSITE=1; .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port ${AIDM_PORT:-8700}` |
| Port | `8700` (`AIDM_PORT`) |
| Local URL | `http://192.168.1.54:8700` |
| Public URL | `https://dm.jamlarnet.stream` (Cloudflare tunnel → 192.168.1.54:8700) |
| Unit env | `AIDM_PORT=8700`, `AIDM_NUM_CTX=32768`, `AIDM_GEMMA_MODEL=gemma4-26b-a4b:latest`, `AIDM_PROSE_MODEL=claude-sonnet-4-5`, `AIDM_PROSE_FALLBACK=claude-haiku-4-5`, `FREELMAPI_API_KEY=…` |
| DB | `data/dm.db` (SQLite WAL). `data/aidm.db` exists but is a **0-byte leftover** — ignore it |
| Python | `.venv` (fastapi, uvicorn, jinja2, httpx, python-multipart, pillow, sherpa-onnx) |
| Extras | `models/stt/` (git-ignored STT weights), `data/portraits/`, `data/tts/`, `data/archive/`, `data/backups/` |

`unset PYTHONPATH` in `run.sh` is load-bearing: the shell env injects Hermes's py3.14 site-packages, which breaks the venv.

## B3. Repository layout

```
/home/james/ai-dm/
├── app.py                 1204 L  49 endpoints: pages, campaign CRUD, SSE turn stream, state panel
├── run.sh                          uvicorn launcher (see B2)
├── dmcore/               22,776 L  50 modules — the entire game engine
├── templates/             5 files  base.html, index.html, play.html, dm.html, journal.html
├── static/                app.js, create.js, dm.js, journal.js, style.css (50 KB), icons, manifest
├── data/                  dm.db (live), portraits/, tts/, archive/, backups/, items_full.json,
│                          monsters_full.json, traps.json, srd_extra/
├── tests_*.py             59 files  one suite per domain, pytest
├── conftest.py                     db sandbox fixture
├── tools/                 13 scripts  e2e_{combat,traps,fresh,specials}, reparse_turns,
│                                   audit_dmg, srd_coverage, fetch_srd_extras,
│                                   import_manual_{items,monsters}, spine_variety_report,
│                                   _probe_{sheets,turn}
├── scripts/               3 scripts  dedupe_facts, repair_segments, verify_sync_live
├── probe3.py, probe_chases.py, probe_rules.py, _travel_test.py, clean_entities.py,
│   scratch_db.py                   live probes / one-shot maintenance
└── README.md, requirements.txt
```

## B4. `dmcore/` module map — 49 modules + `__init__.py`, 22,776 lines

| Module | L | Fn | Responsibility |
|---|---|---|---|
| `inventory.py` | 1636 | 66 | The gear ledger — who holds what, what is worn/attuned, containers, ammo |
| `rules.py` | 1494 | 106 | **Deterministic 5e core.** Pure functions, injectable RNG, no LLM |
| `engine.py` | 1272 | 4 | Turn engine: intent → resolution → narration; the SSE generator |
| `director.py` | 1188 | 54 | Campaign architect + story director: spine generation, scene lifecycle, beats |
| `traps.py` | 1042 | 41 | Trap library and trap mechanics |
| `characters.py` | 726 | 20 | Character construction, SRD class maths, read-only import from Character Manager |
| `shop.py` | 731 | 32 | Commerce: prices, markets, buying/selling |
| `portrait_images.py` | 709 | 26 | GPU-rendered portraits, one per NPC, cached forever |
| `sheets.py` | 642 | 27 | Full party sheets for the table |
| `srd.py` | 639 | 27 | SRD 5.1 data access (monsters, spells) from local 5e-bits cache |
| `prompts.py` | 612 | 17 | Prompt construction — intent shaping and prose only |
| `portraits.py` | 590 | 19 | Deterministic procedural portraits (no GPU needed) |
| `checks.py` | 555 | 22 | Plan builders/resolvers: checks, contests, social, downtime, search, traps |
| `cm_sync.py` | 548 | 26 | Two-way bridge with `dnd-character-manager` |
| `dialogue.py` | 540 | 26 | Turning narrated prose into staged speech (speaker attribution) |
| `state.py` | 523 | 40 | Persisted state primitives: party, encounters, character writes, damage, ailments |
| `compendium.py` | 502 | 38 | Every SRD table the app owns, reachable by name |
| `casting.py` | 456 | 15 | Deterministic demographic casting for NPCs |
| `intent_parse.py` | 452 | 11 | Intent parsing: fast/normalize/polish, place and shop matching |
| `db.py` | 414 | 13 | Plain sqlite3, WAL, one connection per call |
| `combat_round.py` | 405 | 13 | Round machinery: death saves, foe AI, legendary actions, XP, loot |
| `encounter.py` | 405 | 19 | Encounter lifecycle: initiative, turn order, spawning, attack profiles |
| `spine_fallback.py` | 372 | 3 | Procedural campaign spines — the safety net when the model fails |
| `explore_runtime.py` | 360 | 9 | Exploration: fall, survival, ready, opportunity attacks |
| `ailment_runtime.py` | 352 | 6 | Poison, disease, rest-ailment resolution |
| `rest_runtime.py` | 338 | 12 | Rest and item/spell use resolution |
| `chase_runtime.py` | 314 | 11 | Chase procedure and complications |
| `ailments.py` | 310 | 11 | DMG ch.8 diseases and poisons |
| `hazards.py` | 281 | 23 | Breath, hunger, thirst, temperature |
| `worldstate.py` | 277 | 11 | NPC agendas/attitudes, faction clocks |
| `llm.py` | 265 | 8 | Local-first LLM router (streaming + JSON, latency budgets) |
| `craft.py` | 262 | 9 | Downtime crafting economy |
| `stt.py` | 245 | 15 | Speech to text (browser recording → words) |
| `afflictions.py` | 239 | 12 | DMG ch.8/9 madness, lingering injuries, massive damage |
| `chases.py` | 231 | 14 | DMG ch.8 chases (p.252-255) |
| `cast_runtime.py` | 227 | 5 | Spellcasting resolution, slots, effects |
| `narrate.py` | 215 | 6 | Prose insurance — fills gaps when the model stalls |
| `attack_runtime.py` | 196 | 3 | Offhand, readied fire, reactions, damage application |
| `objects.py` | 185 | 7 | Object damage (DMG p.246), survival exposure, madness plans |
| `memory.py` | 178 | 13 | Temporal memory: FTS recall, entity dossiers, rolling summaries |
| `dice.py` | 170 | 6 | SRD-shaped expression roller + campaign roll log |
| `archive.py` | 162 | 7 | Journal browsing + full markdown export |
| `dmguide.py` | 157 | 2 | The DM's own operator manual |
| `loot.py` | 141 | 5 | Deterministic SRD-only treasure |
| `trap_runtime.py` | 134 | 5 | Trap/hazard runtime application |
| `backfill.py` | 127 | 6 | Re-derive SRD sheets for characters made before the rich schema |
| `mounts.py` | 120 | 11 | Mounted combat (PHB p.198) + what a stable sells |
| `rolls.py` | 99 | 9 | **The dice gate** — the table's one source of truth for pending rolls |
| `contradiction.py` | 57 | 2 | Continuity guard: player references that clash with world state |

**Reading order for a newcomer:** `db.py` → `state.py` → `rules.py` → `checks.py` → `engine.py` → `app.py`. Everything else hangs off those.

## B5. Database schema (`data/dm.db`, 15 user tables + FTS5)

WAL mode, `PRAGMA foreign_keys=ON`, every child table cascades on `campaign_id`. Booleans are INTEGER 0/1; every complex field is a **JSON string in a TEXT column** (`abilities`, `inventory`, `spells`, `story_spine`, `state_delta`, `intent`, `result`, `data`).

| Table | Rows now | Purpose / key columns |
|---|---|---|
| `campaigns` | 3 | `name`, `premise`, `tone`, `level_start`, `story_spine` (JSON spine), `scene_no`, `status`, `xp`, `model`, `stash` (JSON party stash) |
| `scenes` | 1 | `no`, `title`, `location`, `mood`, `summary`, `status` open/closed, `closed_at` |
| `beats` | 9 | Spine beats: `act`, `order_no`, `title`, `goal`, `trigger`, `status` pending/done |
| `quests` | 4 | `title`, `detail`, `status`, `kind` main/side, `act`, `order_no`, `source` |
| `characters` | 3 | 44 columns: identity (`race`/`subrace`/`class_name`/`subclass`/`background`/`level`), `abilities` JSON, `hp_max/current/temp`, `ac`, `speed`, `prof_bonus`, `skills`/`saves`/`attacks`/`inventory`/`spells`/`slots`/`traits`/`features`/`feats`/`resources` JSON, `conditions`, `xp`, `alive`, `death_success`/`death_fail`, `hit_dice_used`, `spell_ability`, `exhaustion`, `concentration`, `inspiration`, `source_char_id` (link to Character Manager), `sheet_synced_at`, `sheet_fingerprint` |
| `entities` | 15 | The world's NPCs/places: `kind`, `name`, `status`, `description`, `facts` JSON, `first_seen`, `last_seen`, `role`, `agenda`, `attitude`, `clock_name`, `segments`/`filled` (faction clock), `last_seen_scene`; UNIQUE(campaign_id,name) |
| `turns` | 21 | `scene_id`, `no`, `player_input`, `intent` JSON, `check_result` JSON, `outcome`, `narration`, `state_delta` JSON, `model`, `ms`, `segments` JSON |
| `encounters` | 0 | One row per campaign: `data` JSON (combatants, initiative, HP), `round`, `status` |
| `pending_rolls` | 12 | The dice gate: `turn_text`, `intent` JSON, `char_id`/`char_name`, `kind`, `label`, `skill`, `ability`, `mod`, `dc`, `mode` (adv/dis), `ask` (what the player is told), `status` open/answered/cancelled/superseded, `rolled`, `total`, `success`, `resolved_at` |
| `dice_rolls` | 12 | Roll log: `label`, `expr`, `result` JSON, `total`, `crit`, `mode`, `source` tray/turn, `detail` |
| `gear_log` | 4 | Item provenance: `kind`, `actor`, `item`, `qty`, `target`, `cause` |
| `shop_log` | 0 | `kind`, `actor`, `item`, `qty`, `unit_cp`, `total_cp`, `location` |
| `mounts` | 0 | PK(campaign_id,char_id): `name`, `kind`, `size`, `controlled`, `hp_current`, `hp_max`, `ac` |
| `scene_traps` | 1 | `scene_id`, `name`, `data` JSON, `state` hidden/found/sprung, `found_at`, `sprung_at`; UNIQUE(campaign_id,scene_id,name) |
| `summaries` | 23 | Rolling memory: `scope` scene/act/campaign, `upto_turn`, `text` |
| `memory_fts` | 21 | FTS5 virtual table (`campaign_id`, `turn_id` UNINDEXED, `text`, `tokenize='porter'`) + 4 shadow tables — free-text recall of past turns |

**Where truth lives:** current HP/conditions/gear → `characters`; live fight → `encounters.data`; open dice request → `pending_rolls`; what happened and why → `turns` (+ `memory_fts`); who exists in the world → `entities`; story shape → `campaigns.story_spine` + `beats`.

## B6. The turn engine — the one loop that matters

`POST /api/campaign/{cid}/turn` (SSE) → `engine.run_turn(campaign_id, text, forced=None)`:

1. **Load** campaign, current scene, party, encounter state, entity dossiers, recent turns + FTS recall + latest summaries.
2. **Gate check** — `rolls.open_for(cid)`: if a roll is pending the request is refused with `409 {"error":"a roll is waiting", ask, roll, hint}`. `force:true` abandons it (`superseded`).
3. **Intent parse** (`intent_parse.py`) → `{action, actor, target, skill, ability, dc_hint, items, place, shop…}`. Model-shaped, then normalised against the compendium so a typo cannot invent a monster.
4. **Deterministic resolution** (`rules.py` + `checks.py` + the `*_runtime.py` family): attack maths, AC, damage, saves, spells/slots, movement, conditions, ailments, rests, chases, traps, crafting. No LLM touches a number.
5. **Dice gate** (`rolls.py`): if the outcome hinges on a die, the engine emits `roll_request` and **returns**, leaving a `pending_rolls` row open. The player answers via `POST /api/campaign/{cid}/roll` (`{"die":17}` or `{"auto":true}`) — the *exact* number is fed back into the engine, so the pending spec decides mod/DC/adv-dis, not a fresh roll.
6. **State write** (`state.py`): HP, conditions, inventory deltas, entity facts/attitudes, quests, XP, scene advance — recorded as `state_delta` on the turn.
7. **Narration** (`prompts.py` + `narrate.py` + `llm.py`): prose streamed back in chunks. If the narrator dies mid-stream the client gets `reset` then either recovered prose or a holding scene.
8. **Memory** (`memory.py`, `archive.py`): turn + segments indexed into `memory_fts`, scene summaries rolled up, entities upserted.

### SSE event protocol (exact, from `engine.py`)

| Event | Payload | Meaning |
|---|---|---|
| `status` | `{message}` | Phase marker: "reading the table", "rolling", "narrating", "rewriting turn N" |
| `chunk` | `{text}` | Prose (or an `INITIATIVE: …` line) appended to the transcript |
| `roll_request` | `{roll, ask}` | A die is owed; client shows the ask and waits for the player |
| `reset` | — | Discard the chunks produced so far (narration retry) |
| `error` | `{message}` | Terminal failure for this turn (e.g. "campaign not found") |
| `done` | `{turn, ms, outcome, intent, …}` | Turn committed; includes `renarrated:true` and `narration` for a rewrite |

`renarrate(campaign_id, turn_no=None)` re-emits the same protocol for an existing turn (`POST /api/campaign/{cid}/renarrate`), adding `renarrated:true` to `done`.

## B7. Rules coverage in `dmcore` (what a DM can actually resolve)

Combat: initiative/turn order, attack + damage (incl. offhand, readied fire, reactions), cover/adv-dis, crits, death saves, legendary actions, foe AI, XP and loot.
Exploration: travel, `_travel_test.py` probes it, chases (DMG p.252-255), falls, survival/exposure, ready/opportunity attacks, search, hazards (breath/hunger/thirst/temperature).
Magic: casting, slots, concentration, upcasting, item/spell use, scrolls.
Downtime/economy: crafting, shops (prices, buy/sell, `shop_log`), stable stock, mounts.
Ailments: DMG ch.8 diseases and poisons, madness, lingering injuries, massive damage.
Traps: library + runtime (`hidden → found → sprung`), scene-scoped.
Social: contests, attitude shifts, faction clocks, entity dossiers.
Object damage (DMG p.246), lifts/carries, encumbrance, containers, ammo.

## B8. LLM routing (`dmcore/llm.py`) — tier order is a performance contract

```
Tier list (built at import):
  air26          only if AIDM_AIR26 set (llama-server :8086 is gone — normally absent)
  gemma4         ollama @ 192.168.1.31:11434, model from AIDM_GEMMA_MODEL
  freeapi        FreeLLMAPI @ 127.0.0.1:3010/v1, model "auto"  (only if FREELMAPI_API_KEY)
  prose          FreeLLMAPI + AIDM_PROSE_MODEL (claude-sonnet-4-5)   ← narration only
  prose_fallback FreeLLMAPI + AIDM_PROSE_FALLBACK (claude-haiku-4-5) ← narration only
```

* `prose_prefer()` returns `prose` so narration opts in; structured calls (intent parsing, JSON) keep the local-first order and never see the prose tier first.
* `NUM_CTX` **must** match Hermes's `ollama_num_ctx`; a mismatch makes ollama reload the model on every hop (turns take ~24 s instead of ~3 s).
* Env spelling: both `FREELMAPI_API_KEY` and the original misspelled `FREELMAPI_KEY` are read — a correctly-named key was silently ignored once, breaking the tier.
* Timeouts: connect 3 s, read 120 s. Streaming and JSON modes both exist; JSON mode is retried with a repair pass.
* Latency budget is *measured*, not guessed: `turns.ms` records every turn, and `AIDM_NUM_CTX` / tier choice are the tuning levers.

## B9. Voice (STT + TTS)

* **STT** (`dmcore/stt.py`, sherpa-onnx): the composer mic records in the browser → `POST /api/voice/transcribe` → words in the input box. Model files in `models/stt/` (git-ignored). `GET /api/voice/status` reports readiness; it degrades to "no mic" if weights are absent.
* **TTS** (`dmcore/tts.py`, 681 L): `GET /api/tts?…` speaks the narration so the table hears instead of reading ahead; `GET /api/tts/status` health. Voices per speaker come from `dialogue.py` segmentation (`GET /api/campaign/{cid}/voices`), cached under `data/tts/`.
* Both are optional: the app must run with neither present.

## B10. Portraits

`portraits.py` draws deterministic procedural portraits (no GPU, always available). `portrait_images.py` renders painted/photographed NPC portraits through the shared ComfyUI box, cached under `data/portraits/` forever; `POST /api/portrait/paint` triggers a render, `GET /api/campaign/{cid}/party/{char_id}/portrait` serves one, `GET /api/avatar.{json,png,svg}` serves the app's own avatar. Portrait provider is env-driven and must degrade gracefully when the GPU host is off.

## B11. Frontend (5 templates, 4 JS + 1 CSS, plus PWA icons/manifest)

| File | What it is |
|---|---|
| `templates/index.html` | Campaign list / creation (name, premise, tone, starting level) |
| `templates/play.html` | **The table.** Transcript, composer (+mic), roll gate panel, party strip, state panel, shop, journal drawer |
| `templates/dm.html` | DM console: campaign state, scene/beat control, entities, encounters, traps, memory |
| `templates/journal.html` | Journal / archive browser + markdown export |
| `templates/base.html` | Shell, nav, asset includes |
| `static/app.js` | Play-page logic: SSE consumer, transcript, roll prompt, composer |
| `static/create.js` | Campaign creation flow |
| `static/dm.js` | DM console wiring |
| `static/journal.js` | Journal browsing |
| `static/style.css` | 50 KB of styling — the whole visual identity |

No framework, no build step: plain ES modules + `fetch` + `EventSource`. The remaining `static/` files are PWA furniture: `favicon.{ico,svg}`, `favicon-16/32.png`, `favicon-small.svg`, `icon-192/512.png`, `apple-touch-icon.png`, `site.webmanifest`.

## B12. API surface

49 endpoints in `app.py` — 5 pages, 44 JSON. Conventions:

* Pages: `/`, `/campaign/{cid}`, `/campaign/{cid}/dm`, `/campaign/{cid}/journal`, `/campaign/{cid}/party/add`.
* Everything else is `/api/...`; `campaign_or_404(cid)` guards each campaign route.
* The turn stream is SSE (`text/event-stream`) and is the only streaming endpoint.
* Mutations accept JSON; 4xx bodies are `{"detail": …}` or, for the roll gate, `{"error", "ask", "roll", "hint"}`.
* Full inventory: **Appendix B**.

Hot paths: `POST /turn` (whole engine), `GET /state` (rebuilds the state panel), `GET /history` (transcript pagination), `POST /sync/pull|push` (Character Manager bridge), `GET /api/campaign/{cid}/dice` and `/rolls`.

## B13. Security model

* Single-user by design; there is **no auth on ai-dm** — it is bound to `0.0.0.0:8700` and protected by the Cloudflare tunnel only. Public hostname exposure is a deliberate trade-off, not an oversight. Do not add multi-user without adding auth.
* No secrets in templates or JS; keys come from the unit's `Environment=` lines.
* Input hygiene in the turn endpoint: `_clean_action()` + `_input_problem()` reject empty/oversized/absurd input with 400 before any engine work.
* SQL is parameterised throughout `db.py`; JSON columns are parsed defensively and fall back to `{}`.

## B14. Conventions and pitfalls specific to this app

1. **Never let the model produce a number.** Dice, DCs, damage, HP, XP, prices all come from `rules.py` / `checks.py`. If a feature needs a new quantity, add a pure function and a test.
2. **The dice gate is a hard state machine.** `pending_rolls.status` ∈ open/answered/cancelled/superseded. A new turn with an open roll must 409, never silently roll twice.
3. **`data/dm.db` is the live DB.** `data/aidm.db` is a 0-byte junk file; `scratch_db.py` exists precisely so tests point `AIDM_DB` elsewhere.
4. **Everything is campaign-scoped.** Any new table needs `campaign_id … ON DELETE CASCADE`, or deleting a campaign orphans rows.
5. **JSON-in-TEXT is the storage convention** — read with a helper, never `json.loads` bare (old rows have partial shapes).
6. **`NUM_CTX` mismatch = 8× latency.** See B8. This is the most common "the app got slow" cause.
7. **Segments, not raw prose, feed TTS and dialogue.** `turns.segments` is the structured form; `tools/reparse_turns.py` rebuilds it, `scripts/repair_segments.py` fixes drift after a prompt change.
8. **`cm_sync.py` is the only path to Character Manager data.** Character sheets here are read-only copies with `source_char_id` + `sheet_fingerprint`; editing them locally desyncs the idempotence check.
9. **Tests are per-domain files** (`tests_combat.py`, `tests_traps.py`, …) and use `conftest.py`'s throwaway DB. Run the matching file, not the whole suite, while iterating.
10. **`spine_fallback.py` must stay as varied as the model.** If spine generation fails, campaigns come from the procedural fallback — a boring fallback is a product bug.

## B15. Dev workflow

```bash
cd /home/james/ai-dm
./.venv/bin/python -m pytest tests_combat.py -q      # one domain
./.venv/bin/python -m pytest -q                      # all 59 files
systemctl --user restart ai-dm                       # user unit
curl -s localhost:8700/api/health/models
./.venv/bin/python probe3.py                         # live probe over the real API
```

Adding a rule: pure function in `rules.py` → plan builder in `checks.py` (if it needs a roll) → wire in `engine.py` → `state.py` write → test file → live probe. Adding a table: `db.py` schema block + migration → `*_runtime.py` → route → JS.

Probes (`probe3.py`, `probe_chases.py`, `_travel_test.py`) hit the **running** server and clean up after themselves — use them before restarting to confirm end-to-end behaviour.

## B16. Known gaps and rough edges (honest list)

* No auth, no rate limiting, no CSRF — single-user design. Exposed only behind the tunnel.
* Live DB has 3 campaigns / 21 turns: most features are exercised by tests and probes, not by accumulated play. Low-row-count tables (`encounters`, `mounts`, `shop_log`) are untested in real play.
* `data/aidm.db` 0-byte leftover; `clean_entities.py`, `probe_rules.py`, `_travel_test.py` are ad-hoc scripts with no home.
* TTS/STT depend on optional weights under `models/stt/` and a GPU-free TTS path; both are silently degraded when absent, which can look like "voice is broken".
* `dmcore` is 22.8k lines in one package with `rules.py` at 1.5k and `inventory.py` at 1.6k — both are past the comfortable review size.
* No websocket: one-turn-at-a-time SSE, so a second tab can desync the transcript until refresh.

---

# Part C — How the two apps fit together

They are separate processes, separate databases, separate venvs, separate units — and they deliberately share nothing at runtime except HTTP.

```
                     ┌──────────────────────────────┐
  browser ──────────▶│ dnd-character-manager :8300  │  system unit
                     │ systemd: dnd-character-manager│  characters.jamlarnet.stream
                     │ data/characters.db           │
                     └───────────┬──────────────────┘
                                 │  POST /api/ai/portrait  (character portrait requests)
                                 │  GET  /api/campaign/{id}/... (party data used by DM tools)
                                 ▼
                     ┌──────────────────────────────┐
                     │ ai-dm :8700                  │  user unit  (systemctl --user)
                     │ systemd --user: ai-dm        │  dm.jamlarnet.stream
                     │ data/dm.db  + dmcore/cm_sync │
                     └──────────────────────────────┘
           cm_sync.py ──▶ HTTP ──▶ characters app   (pull/push character sheets)
```

Three real couplings, all HTTP:

1. **Party import** — `ai-dm` pulls a character sheet from the Character Manager (`cm_sync.py`, `POST /sync/pull`) and stores a read-only copy in `characters` with `source_char_id` + `sheet_fingerprint`. The fingerprint makes the sync idempotent: unchanged sheet → no write.
2. **Portraits** — `services/dm_portraits.py` in the Character Manager is the courier; `ai-dm`'s `POST /api/portrait/paint` is the painter. A character made in the CM can get the same painted portrait the AI DM gives its NPCs.
3. **Shared art/data conventions** — both use the same reference-art library and the same SRD expectations, but each has its own copy of the data (`core/data.py` vs `srd.py` + `data/srd_extra/`). **There is no shared library.** A rule fix in one does not propagate to the other.

What is *not* shared: users, auth, sessions, CSS, JS, dice logic, spell data. Do not assume a CM feature exists in the AI DM or vice versa.

## C1. When a change has to land in both

| Change | `dnd-character-manager` | `ai-dm` |
|---|---|---|
| A 5e rule (e.g. grappling, exhaustion) | `services/leveling.py`, `services/combat.py`, `data.py` | `dmcore/rules.py`, `dmcore/checks.py` |
| Character schema (new field) | `services/db_schema.py`, `routes/characters/sheet/*`, `sheet.html` | `dmcore/characters.py`, `dmcore/cm_sync.py`, `templates/play.html` |
| Spell list / spell behaviour | `data.py`, `manual_data/`, loaders | `dmcore/srd.py`, `data/srd_extra/` |
| Portrait pipeline | `services/portraits.py`, `services/topology_verify.py` | `dmcore/portrait_images.py`, `dmcore/portraits.py` |

# Part D — Operations

| | `dnd-character-manager` | `ai-dm` |
|---|---|---|
| Unit | **system** `dnd-character-manager` (enabled) | **user** `ai-dm` (enabled) |
| Restart | `sudo -n systemctl restart dnd-character-manager` (NOPASSWD) | `systemctl --user restart ai-dm` |
| Port / bind | 8300, 0.0.0.0 | 8700, 0.0.0.0 |
| Public host | `characters.jamlarnet.stream` | `dm.jamlarnet.stream` |
| Tunnel | `~/.cloudflared/config.yml`, tunnel `e55ec0b1-a474-4c43-9506-72b09104fd1c`, both → `127.0.0.1:<port>` | same file |
| DB | `data/characters.db` (WAL) | `data/dm.db` (WAL) |
| DB backup | `data/backups/`, `data/logs/` | `data/backups/`, `data/archive/` |
| Disk | `static/` 850 MB + `data/` 470 MB | small (portraits/TTS dominate) |
| Logs | `journalctl -u dnd-character-manager -f` | `journalctl --user -u ai-dm -f` |
| Health | `curl -s localhost:8300/api/health` | `curl -s localhost:8700/api/health/models` |
| Tests | `tests/` 124 files, pytest | 59 `tests_*.py`, pytest |

Both `run.sh`-style launchers must `unset PYTHONPATH` (the shell env injects Hermes's py3.14 site-packages and breaks the venv).

**Backups.** Both codebases are private GitHub repos (`jamlar87`). `~/.hermes` backups mirror `~/hermes-config`. Local disk holds the only copy of the databases — a `characters.db` copy is worth more than any code change; snapshot before migrations.

**Disk pressure.** The Character Manager is the disk hog on a 850 MB static tree; `static/maps/` (312 images) is the usual culprit. Deleting a map row without deleting the file leaves orphans; use the map delete route, not `rm`.

# Part E — Where do I change X? (fast index)

| I want to… | File |
|---|---|
| change character sheet fields / layout | `templates/sheet.html` (218 KB), `static/sheet/*.js`, `routes/characters/sheet/` |
| add a character wizard step | `templates/create.html`, `static/create*.js`, `routes/characters/creation.py` |
| change levelling rules | `services/leveling.py`, `data.py`, `routes/characters/leveling.py` |
| fix a spell that will not prepare | `routes/characters/spells.py`, `data.py`, `manual_data/` |
| change the DM Tools page | `templates/dm_tools.html`, `static/dm/*.js` (16 scripts), `routes/dm/*` |
| change battle-map behaviour | `routes/maps.py`, `static/vtt.js`, `static/dm/maps.js`, `services/map_grid*.py` |
| change the table roll feed | `routes/table.py`, `services/events.py`, `static/dm/rollfeed.js` |
| change portrait art or prompts | `services/portraits.py`, `services/ref_portraits.py`, `services/topology_verify.py`, `services/pose_skeleton.py` |
| change character PDF output | `pdf/` (9 modules), `routes/characters/pdf.py` |
| ingest a new manual | `ingest_manual.py` (3382 L), `scripts/`, `services/loaders/` |
| change the DB schema | `services/db_schema.py` (+ `services/schema.py` validation will fail boot if you forget) |
| **AI DM turn behaviour** | `dmcore/engine.py` |
| AI DM rules / maths | `dmcore/rules.py` (+ `checks.py` for roll plans) |
| AI DM prose voice | `dmcore/prompts.py`, `dmcore/narrate.py` |
| AI DM model / speed | `dmcore/llm.py` + the unit env (`AIDM_NUM_CTX`, prose tier) |
| AI DM story structure | `dmcore/director.py`, `dmcore/spine_fallback.py` |
| AI DM combat | `dmcore/combat_round.py`, `dmcore/encounter.py`, `dmcore/attack_runtime.py` |
| AI DM character import | `dmcore/cm_sync.py`, `dmcore/characters.py` |

# Part F — Verification log (what this document is based on)

Everything above was read from the live machine on 2026-11-21, not from memory:

* `ast`-parsed module inventories with line counts, function counts and docstrings for `core/`, `routes/`, `routes/characters/`, `routes/characters/sheet/`, `routes/dm/`, `services/`, `services/loaders/`, `pdf/`, `scripts/`, `tests/` (Character Manager) and all of `dmcore/` + root scripts (AI DM). Raw dumps: `cm_inventory.md`, `aidm_modules.md`, `cm_endpoints.md`, `aidm_endpoints.md`.
* Route extraction by decorator parse: **186** Character Manager routes across the five route groups + `main.py`; **49** AI DM routes in `app.py`. Both totals were cross-checked against `grep -c` of the decorator lines.
* SQLite `sqlite_master` + `PRAGMA table_info` on `data/characters.db` and `data/dm.db`, including every table's `CREATE` statement and live row counts.
* Live service state: `dnd-character-manager` active+enabled (system), `ai-dm` active (user); `~/.cloudflared/config.yml` ingress entries for both hostnames; unit env from `systemctl --user cat ai-dm` including the drop-in `env.conf`.
* `static/` and `data/` tree walks with byte counts; template and JS file sizes.
* `dmcore/engine.py` read directly for the SSE contract (event names and payloads) and the turn pipeline.

Row counts in this document are a snapshot — they will drift. Structural claims (tables, columns, routes, files, line counts) were exact at the time of writing.

---

# Appendix A — `dnd-character-manager` endpoint inventory (186)

Extracted by parsing every route decorator. `ep` = endpoint count per module; the path column is the exact registered path. Auth: `T` = needs a session cookie (all of them except `/login`, `/register`, `/reset-password`, `/api/health`, the PWA routes).



**main.py** — 21 endpoints
- `GET /` → `landing()`
- `POST /api/characters/{char_id}/favorite` → `toggle_favorite()`
- `GET /api/dm/traps` → `dm_list_traps()`
- `POST /api/dm/traps/create` → `dm_create_trap()`
- `POST /api/dm/traps/{trap_id}/delete` → `dm_delete_trap()`
- `POST /api/dm/traps/{trap_id}/update` → `dm_update_trap()`
- `GET /api/health` → `api_health()`
- `GET /api/items/describe` → `describe_item()`
- `GET /api/items/search` → `search_items()`
- `GET /api/reference/entities` → `reference_entities()`
- `GET /api/reference/entity` → `reference_entity()`
- `GET /api/reference/manual-file/{path:path}` → `serve_manual_file()`
- `GET /api/reference/manual-titles` → `manual_titles()`
- `GET /api/reference/manuals` → `list_manuals()`
- `GET /api/reference/open/{slug}` → `open_manual()`
- `POST /api/reference/query` → `query_reference()`
- `GET /api/reference/source-map` → `source_map()`
- `GET /dashboard` → `dashboard()`
- `GET /manifest.webmanifest` → `pwa_manifest()`
- `GET /offline` → `pwa_offline()`
- `GET /sw.js` → `pwa_service_worker()`


### routes/ (top level)

**auth.py** — 9 endpoints
- `GET /change-password` → `change_password_page()`
- `POST /change-password` → `change_password()`
- `GET /login` → `login_page()`
- `POST /login` → `login()`
- `GET /logout` → `logout()`
- `GET /register` → `register_page()`
- `POST /register` → `register()`
- `GET /reset-password` → `reset_password_page()`
- `POST /reset-password` → `reset_password()`

**maps.py** — 23 endpoints
- `POST /api/dm/map/create` → `dm_map_create()`
- `POST /api/dm/map/scene/{scene_id}/delete` → `dm_map_scene_delete()`
- `POST /api/dm/map/scene/{scene_id}/restore` → `dm_map_scene_restore()`
- `POST /api/dm/map/token/{token_id}/delete` → `dm_map_token_delete()`
- `POST /api/dm/map/token/{token_id}/update` → `dm_map_token_update()`
- `GET /api/dm/map/{map_id}` → `dm_map_detail()`
- `POST /api/dm/map/{map_id}/align-grid` → `dm_map_align_grid()`
- `POST /api/dm/map/{map_id}/delete` → `dm_map_delete()`
- `POST /api/dm/map/{map_id}/image` → `dm_map_image()`
- `POST /api/dm/map/{map_id}/layer` → `dm_map_layer_save()`
- `GET /api/dm/map/{map_id}/player-key` → `dm_map_player_key()`
- `POST /api/dm/map/{map_id}/player-key` → `dm_map_player_key_set()`
- `GET /api/dm/map/{map_id}/player/stream` → `dm_map_player_stream()`
- `POST /api/dm/map/{map_id}/snapshot` → `dm_map_snapshot()`
- `POST /api/dm/map/{map_id}/spawn-encounter` → `dm_map_spawn_encounter()`
- `GET /api/dm/map/{map_id}/state` → `dm_map_state()`
- `GET /api/dm/map/{map_id}/thumb` → `dm_map_thumb()`
- `POST /api/dm/map/{map_id}/token/add` → `dm_map_token_add()`
- `POST /api/dm/map/{map_id}/tokens` → `dm_map_tokens_save()`
- `POST /api/dm/map/{map_id}/update` → `dm_map_update()`
- `GET /api/dm/maps` → `dm_maps_list()`
- `GET /dm-map/{map_id}` → `dm_map_page()`
- `GET /dm-map/{map_id}/player` → `dm_map_player_page()`

**pack.py** — 2 endpoints
- `POST /api/dm/campaign/import` → `campaign_pack_import()`
- `GET /api/dm/campaign/{camp_id}/export` → `campaign_export()`

**ref_images.py** — 2 endpoints
- `GET /api/ref-image-stats` → `ref_image_stats()`
- `GET /api/ref-image/{kind}/{name}` → `ref_image()`

**table.py** — 10 endpoints
- `GET /api/campaign/{camp_id}/player-key` → `campaign_player_key()`
- `POST /api/campaign/{camp_id}/player-key` → `campaign_player_key_set()`
- `POST /api/campaign/{camp_id}/roll` → `campaign_roll()`
- `GET /api/campaign/{camp_id}/rolls` → `campaign_rolls()`
- `GET /api/campaign/{camp_id}/stream` → `campaign_stream()`
- `GET /api/character/{char_id}/feed` → `character_feed()`
- `GET /api/character/{char_id}/feed/stream` → `character_feed_stream()`
- `POST /api/table/{key}/roll` → `table_roll()`
- `GET /api/table/{key}/stream` → `table_stream()`
- `GET /table/{key}` → `table_page()`

**undo.py** — 2 endpoints
- `POST /api/undo` → `do_undo()`
- `GET /api/undo/peek` → `undo_peek()`


### routes/characters/

**ai_routes.py** — 6 endpoints
- `POST /api/ai/build` → `ai_build()`
- `POST /api/ai/generate` → `ai_generate()`
- `POST /api/ai/generate-background` → `ai_generate_background()`
- `POST /api/ai/portrait` → `ai_portrait()`
- `POST /api/character/portrait` → `save_portrait_image()`
- `POST /api/character/{char_id}/generate-history` → `generate_character_history()`

**campaign.py** — 8 endpoints
- `POST /api/campaign/{camp_id}/roll-loot` → `campaign_roll_loot()`
- `GET /api/campaign/{camp_id}/team-items` → `campaign_team_items()`
- `POST /api/campaign/{camp_id}/team-items` → `campaign_add_team_item()`
- `PUT /api/campaign/{camp_id}/team-items/{item_id}` → `campaign_update_team_item_qty()`
- `DELETE /api/campaign/{camp_id}/team-items/{item_id}` → `campaign_delete_team_item()`
- `POST /api/campaign/{camp_id}/team-items/{item_id}/claim` → `campaign_claim_team_item()`
- `GET /api/character/{char_id}/campaign` → `character_campaign()`
- `POST /api/character/{char_id}/share-to-team` → `character_share_to_team()`

**creation.py** — 4 endpoints
- `POST /api/build-from-npc` → `build_from_npc()`
- `POST /api/character/create` → `api_create_character()`
- `GET /api/spells/starting` → `starting_spells()`
- `GET /create` → `create_character_page()`

**leveling.py** — 4 endpoints
- `POST /api/character/{char_id}/apply-level-up` → `apply_level_up()`
- `POST /api/character/{char_id}/de-level` → `apply_de_level()`
- `GET /api/character/{char_id}/de-level-info` → `de_level_info()`
- `GET /api/character/{char_id}/level-up-info` → `level_up_info()`

**pdf.py** — 1 endpoints
- `GET /api/character/{char_id}/pdf` → `character_pdf()`

**relationships.py** — 5 endpoints
- `GET /api/character/{char_id}/relationships` → `get_relationships()`
- `POST /api/character/{char_id}/relationships` → `create_relationship()`
- `POST /api/character/{char_id}/relationships/generate` → `generate_relationship()`
- `PUT /api/character/{char_id}/relationships/{rel_id}` → `update_relationship()`
- `DELETE /api/character/{char_id}/relationships/{rel_id}` → `delete_relationship()`

**spells.py** — 15 endpoints
- `POST /api/character/{char_id}/add-spell` → `add_spell()`
- `POST /api/character/{char_id}/ai-spells` → `ai_select_spells()`
- `GET /api/character/{char_id}/available-spells` → `available_spells()`
- `POST /api/character/{char_id}/combat-notes` → `save_combat_notes()`
- `POST /api/character/{char_id}/feat-config` → `save_feat_config()`
- `GET /api/character/{char_id}/gp` → `get_character_gp()`
- `GET /api/character/{char_id}/magic-initiate` → `get_magic_initiate()`
- `POST /api/character/{char_id}/magic-initiate` → `save_magic_initiate()`
- `POST /api/character/{char_id}/magic-initiate/reset` → `reset_magic_initiate_use()`
- `POST /api/character/{char_id}/magic-initiate/use` → `toggle_magic_initiate_use()`
- `POST /api/character/{char_id}/reset-features` → `reset_features()`
- `POST /api/character/{char_id}/toggle-attune` → `toggle_attune()`
- `POST /api/character/{char_id}/toggle-prepared` → `toggle_prepared()`
- `POST /api/character/{char_id}/unlearn-spell` → `unlearn_spell()`
- `POST /api/character/{char_id}/use-feature` → `use_feature()`

**transfer.py** — 2 endpoints
- `POST /api/character/import` → `character_import()`
- `GET /api/character/{char_id}/export` → `character_export()`


### routes/characters/sheet/

**api.py** — 14 endpoints
- `GET /api/character/{char_id}/attacks` → `get_attacks()`
- `POST /api/character/{char_id}/concentration` → `set_concentration()`
- `GET /api/character/{char_id}/conditions` → `get_conditions()`
- `POST /api/character/{char_id}/conditions` → `add_condition()`
- `DELETE /api/character/{char_id}/conditions` → `remove_condition()`
- `POST /api/character/{char_id}/delete` → `delete_character()`
- `GET /api/character/{char_id}/portrait-image` → `character_portrait_image()`
- `POST /api/character/{char_id}/reload-charge` → `reload_charge()`
- `POST /api/character/{char_id}/spend-charge` → `spend_charge()`
- `GET /api/character/{char_id}/summons` → `get_summons()`
- `POST /api/character/{char_id}/summons` → `create_summon()`
- `POST /api/character/{char_id}/toggle-share` → `character_toggle_share()`
- `POST /api/character/{char_id}/update-summon-hp` → `update_summon_hp()`
- `POST /api/sync-combat-hp` → `sync_combat_hp()`

**update.py** — 3 endpoints
- `POST /api/character/{char_id}/edit-asi` → `edit_asi_choice()`
- `POST /api/character/{char_id}/edit-expertise` → `edit_expertise_choice()`
- `POST /api/character/{char_id}/update` → `update_character()`

**view.py** — 1 endpoints
- `GET /character/{char_id}` → `character_sheet()`


### routes/dm/

**api.py** — 7 endpoints
- `POST /api/ai/summary/pdf` → `ai_summary_pdf()`
- `POST /api/dm/character-hp` → `dm_character_hp()`
- `GET /api/dm/characters-for-combat` → `dm_characters_for_combat()`
- `POST /api/dm/search-manuals` → `dm_search_manuals()`
- `POST /api/dm/search-manuals/summarize` → `dm_search_manuals_summarize()`
- `POST /api/dm/summon-hp` → `dm_summon_hp()`
- `GET /api/dm/user-characters` → `dm_user_characters()`

**campaigns.py** — 11 endpoints
- `POST /api/dm/campaign/create` → `dm_campaign_create()`
- `POST /api/dm/campaign/{camp_id}/add-character` → `dm_campaign_add_character()`
- `POST /api/dm/campaign/{camp_id}/add-npc` → `dm_campaign_add_npc()`
- `POST /api/dm/campaign/{camp_id}/award-xp` → `award_xp()`
- `POST /api/dm/campaign/{camp_id}/delete` → `dm_campaign_delete()`
- `POST /api/dm/campaign/{camp_id}/remove-character` → `dm_campaign_remove_character()`
- `POST /api/dm/campaign/{camp_id}/remove-npc` → `dm_campaign_remove_npc()`
- `POST /api/dm/campaign/{camp_id}/update` → `dm_campaign_update()`
- `POST /api/dm/campaign/{camp_id}/update-npc-notes` → `dm_campaign_update_npc_notes()`
- `GET /api/dm/campaign/{camp_id}/xp` → `campaign_xp()`
- `GET /api/dm/campaigns` → `dm_campaigns_list()`

**encounters.py** — 15 endpoints
- `POST /api/dm/ai/build-encounter` → `dm_ai_build_encounter()`
- `GET /api/dm/ai/party-profile` → `dm_ai_party_profile()`
- `POST /api/dm/encounter/create` → `dm_encounter_create()`
- `GET /api/dm/encounter/{enc_id}` → `dm_encounter_detail()`
- `POST /api/dm/encounter/{enc_id}/add-creature` → `dm_encounter_add_creature()`
- `POST /api/dm/encounter/{enc_id}/add-npc` → `dm_encounter_add_npc()`
- `POST /api/dm/encounter/{enc_id}/combat-state` → `dm_encounter_combat_state()`
- `POST /api/dm/encounter/{enc_id}/copy` → `dm_encounter_copy()`
- `POST /api/dm/encounter/{enc_id}/delete` → `dm_encounter_delete()`
- `POST /api/dm/encounter/{enc_id}/remove-npc` → `dm_encounter_remove_npc()`
- `POST /api/dm/encounter/{enc_id}/roll-initiative` → `dm_encounter_roll_initiative()`
- `POST /api/dm/encounter/{enc_id}/share` → `dm_encounter_share()`
- `POST /api/dm/encounter/{enc_id}/update` → `dm_encounter_update()`
- `POST /api/dm/encounter/{enc_id}/update-initiative` → `dm_encounter_update_init()`
- `GET /api/dm/encounters` → `dm_encounters_list()`

**monsters.py** — 5 endpoints
- `GET /api/dm/monster/{index}` → `dm_monster_detail()`
- `GET /api/dm/monsters` → `dm_monster_list()`
- `GET /api/dm/monsters/by-cr` → `dm_monsters_by_cr()`
- `GET /api/dm/monsters/search` → `dm_monster_search()`
- `GET /api/dm/spell/{index}` → `dm_spell_detail()`

**npcs.py** — 9 endpoints
- `POST /api/dm/ai/build-npc` → `dm_ai_build_npc()`
- `POST /api/dm/npc/build-from-gaps` → `dm_npc_build_from_gaps()`
- `POST /api/dm/npc/create` → `dm_npc_create()`
- `GET /api/dm/npc/{npc_id}` → `dm_npc_detail()`
- `POST /api/dm/npc/{npc_id}/build-to-character` → `dm_npc_build_to_character()`
- `POST /api/dm/npc/{npc_id}/delete` → `dm_npc_delete()`
- `GET /api/dm/npc/{npc_id}/portrait-image` → `dm_npc_portrait_image()`
- `POST /api/dm/npc/{npc_id}/update` → `dm_npc_update()`
- `GET /api/dm/npcs` → `dm_npcs_list()`

**page.py** — 2 endpoints
- `GET /campaign/{camp_id}` → `campaign_detail()`
- `GET /dm-tools` → `dm_tools()`

**traps.py** — 1 endpoints
- `POST /api/dm/ai/build-trap` → `dm_ai_build_trap()`

**widgets.py** — 4 endpoints
- `GET /api/dm/campaign/{camp_id}/widgets` → `dm_campaign_widgets()`
- `POST /api/dm/campaign/{camp_id}/widgets` → `dm_save_campaign_widgets()`
- `GET /api/dm/encounter/{enc_id}/widgets` → `dm_encounter_widgets()`
- `POST /api/dm/encounter/{enc_id}/widgets` → `dm_save_encounter_widgets()`

---

# Appendix B — `ai-dm` endpoint inventory (49)

**app.py** — 49 endpoints

- `GET /` → `home()`
- `GET /api/avatar.json` → `avatar_json()`
- `GET /api/avatar.png` → `avatar_png()`
- `GET /api/avatar.svg` → `avatar_svg()`
- `GET /api/campaign/{cid}/dice` → `dice_history()`
- `POST /api/campaign/{cid}/dice/roll` → `dice_roll()`
- `GET /api/campaign/{cid}/dm` → `dm_state()`
- `POST /api/campaign/{cid}/dm` → `dm_command()`
- `GET /api/campaign/{cid}/export.md` → `export_md()`
- `GET /api/campaign/{cid}/history` → `history()`
- `GET /api/campaign/{cid}/inspiration` → `inspiration_state()`
- `POST /api/campaign/{cid}/inspiration` → `inspiration_change()`
- `GET /api/campaign/{cid}/journal` → `journal_api()`
- `POST /api/campaign/{cid}/opening` → `opening()`
- `POST /api/campaign/{cid}/party/attach` → `party_attach()`
- `GET /api/campaign/{cid}/party/{char_id}/portrait` → `party_portrait()`
- `POST /api/campaign/{cid}/party/{char_id}/remove` → `party_remove()`
- `GET /api/campaign/{cid}/party/{char_id}/sheet` → `party_sheet()`
- `POST /api/campaign/{cid}/quest` → `add_quest()`
- `POST /api/campaign/{cid}/quest/{qid}/status` → `quest_status()`
- `POST /api/campaign/{cid}/recap` → `recap()`
- `POST /api/campaign/{cid}/renarrate` → `renarrate()`
- `POST /api/campaign/{cid}/roll` → `answer_roll()`
- `POST /api/campaign/{cid}/roll/{rid}/abandon` → `abandon_roll()`
- `GET /api/campaign/{cid}/rolls` → `campaign_rolls()`
- `GET /api/campaign/{cid}/shop` → `shop_state()`
- `POST /api/campaign/{cid}/shop/buy` → `shop_buy()`
- `POST /api/campaign/{cid}/shop/sell` → `shop_sell()`
- `GET /api/campaign/{cid}/state` → `state()`
- `POST /api/campaign/{cid}/sync` → `sync_sheets()`
- `POST /api/campaign/{cid}/sync/pull` → `pull_sheets()`
- `POST /api/campaign/{cid}/sync/push` → `push_sheets()`
- `POST /api/campaign/{cid}/turn` → `turn()`
- `GET /api/campaign/{cid}/voices` → `campaign_voices()`
- `POST /api/campaigns` → `create_campaign()`
- `DELETE /api/campaigns/{cid}` → `remove_campaign()`
- `GET /api/health/models` → `model_health()`
- `GET /api/monsters` → `monsters()`
- `POST /api/portrait/paint` → `portrait_paint()`
- `GET /api/roster` → `roster()`
- `GET /api/tts` → `tts_say()`
- `GET /api/tts/status` → `tts_status()`
- `GET /api/voice/status` → `voice_status()`
- `POST /api/voice/transcribe` → `voice_transcribe()`
- `GET /campaign/{cid}` → `play()`
- `GET /campaign/{cid}/dm` → `dm_page()`
- `GET /campaign/{cid}/journal` → `journal_page()`
- `GET /campaign/{cid}/party/add` → `party_add()`
- `GET /favicon.ico` → `favicon()`

---

# Appendix C — `dnd-character-manager` file inventory (284 files)

Every Python file in the project with its line count and the first line of its module docstring (its stated job). Locations are relative to `/home/james/dnd-character-manager/`.

| File | L | Purpose |
|---|---|---|
| `data.py` | 1362 | D&D 5e data constants — pure data, no logic. |
| `ingest_manual.py` | 3382 | D&D Manual Ingestion Engine — extract structured data from reference PDFs. |
| `main.py` | 1105 | D&D Character Manager — FastAPI webapp with multi-user character tracking. |
| `pdf_generator.py` | 53 | Backward-compatible facade over the :mod:`pdf` package. |
| `summon_templates.py` | 836 | — |
| `core/__init__.py` | 7 | Shared primitives extracted out of main.py. |
| `core/config.py` | 40 | Paths, environment and process-wide configuration. |
| `core/constants.py` | 65 | Named constants and tables, re-exported from :mod:`core.data`. |
| `core/data.py` | 1880 | Static game data, SRD caches and the ingested-manual registries. |
| `core/db.py` | 28 | SQLite access: the FastAPI dependency, the DB path, JSON-column helper. |
| `core/features.py` | 28 | Rule helpers that combine data tables with the levelling service. |
| `core/http.py` | 110 | Jinja environment, the HTML render helper and static-asset cache busting. |
| `core/security.py` | 121 | Password hashing, cookie sessions and per-row ownership checks. |
| `routes/__init__.py` | 0 | — |
| `routes/auth.py` | 217 | Auth routes — register, login, logout, password reset. |
| `routes/maps.py` | 1461 | Map layer routes — battle maps, their tokens, and per-map snapshots. |
| `routes/pack.py` | 428 | Campaign packs — one JSON file carrying a whole campaign. |
| `routes/ref_images.py` | 82 | Reference art route: monsters, items and NPCs from the shared library. |
| `routes/schemas.py` | 102 | Pydantic models for request validation. |
| `routes/table.py` | 666 | The table channel: a campaign-scoped roll feed shared by the DM and the players. |
| `routes/undo.py` | 85 | Undo endpoints: what the last action was, and putting it back. |
| `routes/characters/__init__.py` | 37 | Character management routes — package. |
| `routes/characters/ai_routes.py` | 1234 | AI generation routes — generation, portrait, background, history. |
| `routes/characters/all.py` | 2480 | Character routes — composite router + the shared PHB registries. |
| `routes/characters/campaign.py` | 705 | Campaign team items routes. |
| `routes/characters/creation.py` | 978 | Character creation routes — /create page, create API, NPC→character |
| `routes/characters/helpers.py` | 1236 | Shared helper functions — monster cache, OCR search, encounter building. |
| `routes/characters/leveling.py` | 1763 | Level-up / de-level routes — progression API. |
| `routes/characters/pdf.py` | 94 | Character PDF generation route. |
| `routes/characters/relationships.py` | 157 | History & Relationships API — character relationship CRUD + AI generation. |
| `routes/characters/spells.py` | 904 | Spell/combat management routes — add spell, magic initiate, prepared, |
| `routes/characters/transfer.py` | 179 | Character export/import routes (JSON). |
| `routes/characters/sheet/__init__.py` | 127 | Character sheet: the page, its save path, equipment/attunement and the |
| `routes/characters/sheet/_shared.py` | 374 | Pure sheet domain helpers: the invocation-name set, the |
| `routes/characters/sheet/api.py` | 495 | Live-session JSON endpoints: attacks, summons, conditions, HP sync, |
| `routes/characters/sheet/feats.py` | 354 | Feat tables and feature enrichment: KNOWN_FEATS/FEAT_DETAILS built |
| `routes/characters/sheet/inventory.py` | 274 | Equipment and attunement: the manual-equipment merge into ITEM_INDEX |
| `routes/characters/sheet/progression.py` | 142 | Level-scaling helpers: spellcasting mod, spells/equipment per level, HP |
| `routes/characters/sheet/update.py` | 240 | Sheet writes: the save path (`POST /character/{char_id}`), the |
| `routes/characters/sheet/view.py` | 864 | The character sheet page: the reference payload written to |
| `routes/dm/__init__.py` | 221 | DM Tools routes — monsters, NPCs, encounters, campaigns, traps, widgets. |
| `routes/dm/_shared.py` | 62 | Data and helpers shared by more than one DM Tools route module. |
| `routes/dm/api.py` | 230 | Cross-cutting DM endpoints: manual search/summary and live HP changes. |
| `routes/dm/campaigns.py` | 496 | Campaigns: CRUD, roster membership, notes and XP. |
| `routes/dm/encounters.py` | 1409 | Encounters: AI building, copy, membership, initiative and combat state. |
| `routes/dm/monsters.py` | 264 | Read-only monster and spell reference endpoints for the DM Tools palette. |
| `routes/dm/npcs.py` | 656 | NPC library: list, detail, create/update/delete, portraits, and AI NPC building. |
| `routes/dm/page.py` | 588 | The DM Tools page itself and the campaign page it links to. |
| `routes/dm/traps.py` | 98 | AI trap builder. |
| `routes/dm/widgets.py` | 116 | Encounter and campaign table widgets. |
| `services/__init__.py` | 1 | Application service boundaries. |
| `services/assets.py` | 30 | Writer for generated static assets — the /static/<name>?v=<hash> files. |
| `services/auth.py` | 17 | Authentication primitives independent of FastAPI and database routing. |
| `services/campaign_access.py` | 115 | Who is allowed to touch a campaign or a character inside it. |
| `services/combat.py` | 630 | Combat / racial-trait helpers — traits, attacks, armor proficiency. |
| `services/data_loader.py` | 181 | Import surface for the data loaders, plus the startup manual-data merge. |
| `services/db.py` | 13 | Small database connection boundary shared by application code. |
| `services/db_schema.py` | 647 | Database schema initialization and migration helpers. |
| `services/dm_portraits.py` | 75 | Portrait painting lives in the AI DM. This module is only the courier. |
| `services/entity_search.py` | 710 | Internal entity search — the nav search bar's "Internal data" section. |
| `services/events.py` | 103 | In-process fan-out for the table channel (shared roll feed + live push). |
| `services/images.py` | 240 | Portrait image helpers shared by the character and NPC routes. |
| `services/items.py` | 435 | Item helpers — item-index lookup, description/type generation, curse split. |
| `services/leveling.py` | 1131 | Leveling / spell-slot helper core. |
| `services/limb_placement.py` | 147 | Turn a limb COUNT into a limb LAYOUT before the prompt reaches the model. |
| `services/login_guard.py` | 143 | In-memory sliding-window throttle for credential endpoints. |
| `services/map_grid.py` | 99 | Hex-grid geometry — the Python side of static/grid.js. |
| `services/map_grid_align.py` | 245 | Auto-align a map's overlay grid to the grid printed on the art. |
| `services/portraits.py` | 936 | Portrait prompts and image generation, shared by the web route and scripts. |
| `services/pose_skeleton.py` | 354 | Draw an OpenPose-style skeleton that the SDXL openpose ControlNet will follow. |
| `services/ref_portraits.py` | 1931 | Reference art for monsters, items and the manual NPC library. |
| `services/reference_assets.py` | 72 | Global reference tables served as a cached asset instead of inline JSON. |
| `services/rules.py` | 45 | D&D 5e arithmetic that more than one part of the app needs. |
| `services/schema.py` | 97 | Required SQLite schema validation. |
| `services/sessions.py` | 72 | Session persistence service, independent of FastAPI request handling. |
| `services/sources.py` | 85 | Source-attribution hygiene for reference records. |
| `services/text.py` | 28 | Text helpers shared by routes, services and scripts. |
| `services/topology_verify.py` | 390 | Generate-then-verify: resample a portrait until a vision model confirms the clause's topology. |
| `services/undo.py` | 179 | Undo: put back the last thing you changed, if nothing has moved it since. |
| `services/users.py` | 40 | User repository operations. |
| `services/validation.py` | 65 | Field bounds for numbers that a client can post. |
| `services/loaders/__init__.py` | 14 | Loader package: one module per responsibility behind data_loader's surface. |
| `services/loaders/backgrounds.py` | 32 | Manual backgrounds -> BACKGROUNDS / BACKGROUND_SOURCES. |
| `services/loaders/feats.py` | 79 | Manual feats -> FEATS / FEAT_BY_NAME, with page sources from the page map. |
| `services/loaders/files.py` | 72 | Disk-side JSON access: cached reads of SRD cache and manual-data files. |
| `services/loaders/indexes.py` | 109 | Manual magic items -> SRD_MAGIC_ITEMS and the ITEM_INDEX lookup table. |
| `services/loaders/limited_use.py` | 135 | Limited-use registry fill-in: homebrew classes, then race and NPC catch-up. |
| `services/loaders/races.py` | 544 | Manual races and subraces -> RACES / SUBASIS / SUBRACE_TRAITS. |
| `services/loaders/sources.py` | 202 | Normalisation and enrichment rules for ingested manual records. |
| `services/loaders/spells.py` | 131 | Manual spells -> SRD_SPELLS (normalised classes/schools + page sources). |
| `services/loaders/subclasses.py` | 121 | Manual subclasses -> CLASSES[..]["subclasses"] and SUBCLASS_FEATURES. |
| `services/loaders/traps.py` | 17 | Manual traps -> MANUAL_TRAPS. |
| `pdf/__init__.py` | 109 | PDF rendering for D&D 5e character sheets, spellbooks and summaries. |
| `pdf/data.py` | 140 | Normalise a character row into the dict the renderers consume. |
| `pdf/layout.py` | 174 | Page geometry, fonts and low-level drawing primitives. |
| `pdf/official.py` | 534 | Fill the official WotC fillable character sheet (AcroForm fields). |
| `pdf/shared.py` | 160 | Lazy lookups against the app data: spells, item descriptions, modifiers. |
| `pdf/sheet.py` | 382 | Legacy three-column ReportLab character sheet (pages 1-2). |
| `pdf/spellbook.py` | 307 | Spellbook pages (spell matrix, spell/equipment appendices). |
| `pdf/summary.py` | 64 | Printable PDF for an AI manual-search summary. |
| `pdf/text.py` | 542 | Feature / appendix text builders (plain strings, no drawing). |
| `scripts/analyze_gaps.py` | 49 | Analyze monster description gaps — what sources are plate, what monsters do we have PDFs for? |
| `scripts/append_extraction.py` | 279 | Append ONE manual extraction into data/manual_data/*.json (safe path). |
| `scripts/apply_cached_descs.py` | 59 | Re-apply cached descriptions to monsters.json after accidental overwrite. |
| `scripts/audit_category_placement.py` | 122 | Is every record in the right core file? |
| `scripts/audit_duplicates.py` | 139 | Find duplicate records in the merged library. |
| `scripts/audit_ingest_attribution.py` | 287 | Are ingested records wired to the right place? |
| `scripts/audit_source_books.py` | 252 | Audit all source books: compare PDF contents against ingested data. |
| `scripts/audit_xge_spells.py` | 71 | Deep audit: XGE spells comparison. |
| `scripts/backfill_phb_spells.py` | 228 | Backfill the PHB's spells, which the original ingestion never extracted. |
| `scripts/backup_db.py` | 73 | Automated SQLite backup with rotation for D&D Character Manager. |
| `scripts/build_background_page_map.py` | 128 | Build a background→page mapping for D&D 5e backgrounds. |
| `scripts/build_class_page_map.py` | 270 | Build a class/subclass→page mapping for D&D 5e classes. |
| `scripts/build_feat_page_map.py` | 218 | Build a feat→page mapping for D&D 5e feats. |
| `scripts/build_feature_page_map.py` | 242 | Build a class/subclass feature → source mapping. |
| `scripts/build_monster_page_map.py` | 268 | Build monster_page_map.json by extracting stat block pages from the Monster Manual PDF. |
| `scripts/build_page_map.py` | 1042 | Build an item→page mapping by searching D&D PDFs with pymupdf. |
| `scripts/build_race_page_map.py` | 350 | Build a race/subrace→page mapping for D&D 5e races. |
| `scripts/build_spell_page_map.py` | 288 | Build a spell→page mapping by searching D&D PDFs with pymupdf. |
| `scripts/build_trait_page_map.py` | 251 | Build a racial trait→page mapping for D&D 5e racial traits. |
| `scripts/cache_manual_text.py` | 85 | Cache extracted text for every library book that has none. |
| `scripts/check_desc_context.py` | 76 | Check why the LLM couldn't find descriptions for specific monsters. |
| `scripts/check_wiring.py` | 70 | Check TTP and WGE entries are present in merged manual_data files. |
| `scripts/creature_art_audit.py` | 124 | Audit installed reference portraits against the anatomy their own manual clause demands. |
| `scripts/dedupe_records.py` | 140 | Merge duplicate records found by scripts/audit_duplicates.py, and normalise their names. |
| `scripts/fill_monster_descriptions.py` | 341 | Fill missing monster descriptions from cached PDF text. |
| `scripts/find_missing_backgrounds.py` | 79 | Brute-force find exact pages for missing backgrounds. |
| `scripts/find_missing_bg2.py` | 37 | Second pass — search for the 3 missing + fix index-hit pages. |
| `scripts/find_missing_bg3.py` | 50 | Third pass — targeted searches for tricky ones. |
| `scripts/find_missing_bg4.py` | 50 | Fourth pass — search for Orzhov, Cult of Dragon, Dragon Scholar. |
| `scripts/find_missing_bg5.py` | 32 | Final pass — look at HotDQ appendix A and GGR backgrounds section. |
| `scripts/find_missing_feats.py` | 65 | Brute-force find exact pages for the 4 missing feats. |
| `scripts/fix_ggr_items.py` | 79 | Fix GGR magic items: remove OCR duplicates, split Guild Signet into 10 individual signets. |
| `scripts/fix_tortle_to_ttp.py` | 165 | Re-attribute the Tortle race from the 2022 reprint to the 2017 original the library owns. |
| `scripts/fix_unknown_sources.py` | 212 | Fix unresolved ("Unknown Source") attributions in the app's reference data. |
| `scripts/gap_analysis.py` | 101 | Gap analysis across all source books. |
| `scripts/generate_portraits.py` | 406 | Generate the portraits the character roster is missing. |
| `scripts/ingest_sweep.py` | 373 | Ingest every library book that has text but no structured extraction. |
| `scripts/make_icons.py` | 84 | Draw the app's icons — no image files in git history by hand, no emoji fonts to depend on. |
| `scripts/normalize_source_displays.py` | 54 | Normalise every record's `source` display to the name the app expects. |
| `scripts/normalize_sources.py` | 293 | Source normalizer for D&D ingested manual data. |
| `scripts/ocr_manual.py` | 205 | OCR-clean a D&D manual PDF with custom font encoding issues. |
| `scripts/page_fit.py` | 123 | Does every cited page exist in the book it names? |
| `scripts/portrait_backfill_watchdog.py` | 320 | Keep the reference-art backfill alive across a Hermes or host restart. |
| `scripts/pose_skeleton.py` | 20 | CLI for the pose-skeleton renderer. |
| `scripts/post_merge_fixup.py` | 128 | Post-merge fixup: repair known LLM/OCR extraction artifacts in merged data. |
| `scripts/probe_openrouter_extraction.py` | 127 | Probe: can OpenRouter's free models do the extraction work the sweep does? |
| `scripts/regen_verified.py` | 95 | Resume-safe driver for generate_verified over an explicit name list. |
| `scripts/register_action_gaps.py` | 196 | Register limited-use traits that have no FEATURE_ACTION_TYPES entry. |
| `scripts/repair_manual_paths.py` | 140 | Re-point pdf_map entries at the PDFs that actually exist. |
| `scripts/repair_page_citations.py` | 234 | Repair page citations that do not point at the record. |
| `scripts/repair_source_attribution.py` | 268 | Resolve records whose citation points at the wrong book or a page that lacks them. |
| `scripts/resolve_chapters.py` | 283 | Brute-force chapter-to-book resolver — indexed edition. |
| `scripts/resolve_pages.py` | 233 | PDF page-finder: resolves 'p.?' source references by searching PDFs. |
| `scripts/restore_tortle_mpmm.py` | 171 | Restore Tortle to Monsters of the Multiverse (MPMM) now that the book is in the library. |
| `scripts/sync_seeded_sources.py` | 329 | Sync seeded subclass/subrace sources from the manual records, then from book TOCs. |
| `scripts/test_matching.py` | 73 | Test the improved _find_best_context function on specific monsters. |
| `scripts/verify_portraits.py` | 79 | Resample-and-verify portraits for creatures whose clause states exotic anatomy. |
| `scripts/verify_text_caches.py` | 86 | Find text caches that are truncated or stale. |
| `scripts/wire_manual.py` | 108 | Wire a new manual PDF into the library: file in the tree, symlink in the repo, pdf_map entry. |
| `scripts/wire_warlock_manuals.py` | 99 | Wire the Kobold Press "Warlock" zines into the source slug map. |
| `tests/conftest.py` | 130 | Shared fixtures for D&D Character Manager tests. |
| `tests/js_bundle.py` | 36 | Read a front-end bundle's whole source, as the browser would. |
| `tests/support_source.py` | 32 | Reading route source when a route module is a package. |
| `tests/test_ai_chain.py` | 440 | The AI tier chain's own contract: order, fall-through, and what counts as an answer. |
| `tests/test_ai_encounter_prompt.py` | 109 | The AI encounter builder must USE the inputs the DM-tools form sends. |
| `tests/test_ai_feature_wiring.py` | 193 | The AI features' client wiring and honesty, pinned where the failures were. |
| `tests/test_api_endpoints.py` | 142 | API endpoint tests. |
| `tests/test_app_page_table_matches_pdfs.py` | 37 | The app's citation validator must not under-count a book's pages. |
| `tests/test_attunement_ui.py` | 208 | Attunement: the sheet's card, its client state mirror, and the server contract. |
| `tests/test_auth_routes_service_usage.py` | 22 | Regression tests for auth route behavior during service extraction. |
| `tests/test_auth_service.py` | 17 | Tests for extracted authentication primitives. |
| `tests/test_authorization_regressions.py` | 39 | Cross-user access regression tests for owned character data. |
| `tests/test_backup_database.py` | 38 | Tests for the active SQLite backup utility (scripts/backup_db.py). |
| `tests/test_campaign_pack.py` | 333 | Campaign packs: export a whole campaign to one file, import it as a new one. |
| `tests/test_campaign_team_sharing.py` | 209 | Stage 0 regressions for campaign team items (loot pool) and share-to-team. |
| `tests/test_character_create_payload.py` | 65 | The create-character payload contract. |
| `tests/test_character_delete_portrait_regressions.py` | 269 | Regressions for the Aug-2026 audit fixes: |
| `tests/test_choice_constant_single_source.py` | 91 | The choice-system level tables have exactly ONE definition, and it lives in data.py. |
| `tests/test_combat_hp_sync.py` | 136 | Combat HP sync: the per-tick read must not run on the event loop. |
| `tests/test_comfy_provider.py` | 245 | The local ComfyUI provider (bazzite, AMD RX 9060 XT via ROCm). |
| `tests/test_compression_and_health.py` | 93 | The origin must compress its own text responses, and must say whether it is alive. |
| `tests/test_construct_prompts.py` | 77 | Constructs must not be described as living creatures. |
| `tests/test_core_functions.py` | 225 | Core function tests. |
| `tests/test_core_import_hygiene.py` | 128 | Import-hygiene guard: `core/` must never depend on `main`, and nothing under |
| `tests/test_csrf_regressions.py` | 63 | CSRF regression tests. |
| `tests/test_dashboard_portraits.py` | 139 | Dashboard character cards render the portrait when one is set. |
| `tests/test_data_integrity.py` | 190 | Data integrity tests. |
| `tests/test_data_loader_service.py` | 23 | Tests for cached JSON data access. |
| `tests/test_db_service.py` | 14 | Tests for the extracted database connection boundary. |
| `tests/test_delegated_actions.py` | 245 | Contracts for the delegated data-action layer. |
| `tests/test_dice_roller.py` | 125 | Click-to-roll dice: ONE asset, applied to any surface that renders rules text. |
| `tests/test_dm_authorization_regressions.py` | 145 | Ownership regressions for DM resources. |
| `tests/test_dm_library_manuals.py` | 68 | The manual library tree is cached asset data, not 70 KB of server-rendered HTML. |
| `tests/test_dm_library_spells.py` | 83 | The spell library is cached asset data, not 580 KB of server-rendered HTML. |
| `tests/test_dm_library_traps.py` | 86 | Reference traps are cached asset data, not server-rendered HTML (Sept 2026). |
| `tests/test_duplicate_dict_keys.py` | 121 | Duplicate dict keys are a silent override, and this codebase has shipped one twice. |
| `tests/test_encounter_selection.py` | 171 | The encounter builder's monster selection: the whole library, and the parameters it is given. |
| `tests/test_encounter_tracker_ui.py` | 85 | Combat tracker: keyboard navigation and click-to-locate. |
| `tests/test_entity_index_startup.py` | 34 | The entity index must be buildable before the manual merge has finished. |
| `tests/test_entity_search.py` | 472 | Entity search: the nav search bar's "Internal data" section. |
| `tests/test_expertise_progression.py` | 212 | Expertise progression regression tests. |
| `tests/test_feature_sources.py` | 75 | Class/subclass feature provenance in the nav search's internal index. |
| `tests/test_handler_loop_safety.py` | 63 | Loop-safety guards. |
| `tests/test_handoff_login.py` | 117 | A logged-out hero handoff must survive the sign-in bounce. |
| `tests/test_hex_orientation.py` | 98 | The hex overlay has two orientations a quarter turn apart. |
| `tests/test_horde_provider.py` | 247 | Stable Horde provider: submit, poll, download — and the failure paths that matter. |
| `tests/test_ingest_chapter_overrides.py` | 44 | A manual with no usable TOC must be given an explicit chapter map. |
| `tests/test_item_descriptions.py` | 72 | Weapon descriptions must state a ranged weapon's range. |
| `tests/test_item_index_integrity.py` | 76 | Regression tests for the merged ITEM_INDEX integrity (2026-08-12). |
| `tests/test_item_search.py` | 65 | Tests for the DM Tools item search + team-pool flow (2026-08-12). |
| `tests/test_layout_assets.py` | 62 | The shared layout CSS + JS are versioned static assets, not inline on every page. |
| `tests/test_list_ordering.py` | 175 | Browsable name lists read in alphabetical order — the order a reader means by that. |
| `tests/test_load_character_access.py` | 124 | Regression tests for ``load_character`` — the single character-access check. |
| `tests/test_login_lockout.py` | 94 | A dead `dnd_token` cookie must never lock anyone out of the login form. |
| `tests/test_login_throttle.py` | 172 | Brute-force protection on POST /login and POST /register. |
| `tests/test_manual_sources.py` | 206 | Test: validate all manual data sources have clean (Manual, pg#) format. |
| `tests/test_manuals_tab.py` | 141 | Regression tests: the 📚 Manuals tab must list each book once. |
| `tests/test_map_canvas.py` | 179 | The map canvas: the page renders for its owner only, and the asset keeps its contract. |
| `tests/test_map_grid_align.py` | 146 | Auto-aligning the overlay to the grid printed on the art. |
| `tests/test_map_grid_math.py` | 153 | Pin the hex maths to the canvas' arithmetic. |
| `tests/test_map_grid_provenance.py` | 124 | Whose grid is it? |
| `tests/test_map_layers.py` | 236 | Fog of war and the drawing layer — storage, cleaning, and the canvas contract. |
| `tests/test_map_player.py` | 224 | The player view (second screen): what it may see, and how it authenticates. |
| `tests/test_map_provenance.py` | 83 | Provenance: an ingested map must say (and link to) the manual page it came from. |
| `tests/test_map_rotation.py` | 72 | Turning the artwork: a region map scanned sideways should be readable on the table. |
| `tests/test_map_scaling.py` | 382 | Map scaling: the grid, the image, and how big a creature's token is. |
| `tests/test_map_spawn.py` | 149 | Spawning an encounter onto a map, and the HP link back to the tracker. |
| `tests/test_map_stream.py` | 444 | The map player view's live push: a poke per save instead of a 3 s poll. |
| `tests/test_map_thumbs.py` | 99 | Thumbnails for the map list: small, cached, and never a reason for the list to be slow. |
| `tests/test_maps_api.py` | 256 | The map layer's API: maps, placements, snapshots. |
| `tests/test_maps_tab_scale.py` | 124 | The Maps tab has to survive a corpus-sized library: 689 maps must not become 689 DOM rows. |
| `tests/test_mpmm_asi.py` | 241 | Monsters of the Multiverse flexible ASI (Tortle). |
| `tests/test_name_and_scope_regressions.py` | 503 | Regression tests for undefined-name (NameError 500) bugs + cross-user write holes. |
| `tests/test_npc_identity_prompts.py` | 115 | An NPC portrait must match the NPC the record describes. |
| `tests/test_overland_scale.py` | 93 | Overland maps are measured in miles per hex, which the old 1-100 clamp could not express. |
| `tests/test_page_fit.py` | 28 | Every cited page must exist in the book it names. |
| `tests/test_page_map_pages.py` | 71 | Race and trait page maps must cite the page the books actually have. |
| `tests/test_page_modules.py` | 301 | Contracts for the ES-module page structure. |
| `tests/test_page_repair_guards.py` | 86 | A page repair must not offer a class spell list as the place a spell is described. |
| `tests/test_password_change.py` | 49 | Authenticated password-change regression tests. |
| `tests/test_pdf_generation.py` | 201 | PDF generation characterization tests. |
| `tests/test_pdf_pins_hold.py` | 62 | The pinned slug -> file mappings must hold, and must agree with _meta.json. |
| `tests/test_portrait_backfill_watchdog.py` | 146 | The portrait-backfill watchdog's output policy. |
| `tests/test_portrait_force_regenerates.py` | 62 | --force must actually regenerate, not hand back the existing file. |
| `tests/test_portrait_full_view.py` | 67 | Every portrait tile must be clickable, NPCs included. |
| `tests/test_portrait_generation.py` | 434 | Portrait generation: keyless provider + honest failures (Sept 2026). |
| `tests/test_portrait_pipeline.py` | 380 | Portrait pipeline: one write contract, one render contract (Sept 2026). |
| `tests/test_portrait_watchdog_progress.py` | 118 | The watchdog must judge progress, not just liveness, and must resume the right chain step. |
| `tests/test_portraits_everywhere.py` | 162 | Portrait tiles on every surface that lists characters (Sept 2026). |
| `tests/test_progression_matrix.py` | 467 | Character progression regression matrix. |
| `tests/test_progression_regressions.py` | 50 | Character progression and spell-slot regression tests. |
| `tests/test_prompt_house_style.py` | 128 | Every prompt must be fantasy-coded and ask for a blank background. |
| `tests/test_prompt_token_budget.py` | 104 | Every prompt must fit inside SDXL's 77-token window. |
| `tests/test_pwa.py` | 148 | PWA: installable, and honest about being offline. |
| `tests/test_reference_art.py` | 284 | Reference art (monsters / items / NPCs): file store, lazy generation, route. |
| `tests/test_reference_consts_asset.py` | 157 | Global reference tables are cached asset data, not inline JSON on every page. |
| `tests/test_reference_sources.py` | 317 | Regression tests for "Unknown Source" attributions in the DM-tools reference data. |
| `tests/test_roll_feed_js.py` | 227 | The roll feed's client asset, and the dice sink it depends on. |
| `tests/test_rules_service.py` | 171 | Unit tests for the shared rules/access primitives. |
| `tests/test_schema_migrations.py` | 36 | Schema migration and validation regression tests. |
| `tests/test_schema_service.py` | 36 | Schema validation service tests. |
| `tests/test_security_regressions.py` | 78 | Regression tests for security and schema hardening. |
| `tests/test_security_scope_regressions.py` | 152 | Scope and escaping regressions found by the security audit. |
| `tests/test_session_service.py` | 80 | Tests for session repository/service boundary. |
| `tests/test_sheet_helpers_regression.py` | 222 | Regression tests for character-sheet helpers (item effects, natural attacks, |
| `tests/test_sheet_input_bounds.py` | 199 | Stage 0 regression: every client-facing numeric write path is bounded. |
| `tests/test_sheet_item_art.py` | 117 | Sheet item art: the item popout and the item picker must show reference art. |
| `tests/test_sheet_script_extraction.py` | 222 | Guards for the sheet-script extraction + static-data split (Sept 2026). |
| `tests/test_source_filter.py` | 365 | Regression tests: "filter by manual" book-scope feature. |
| `tests/test_source_slugs_are_library_books.py` | 57 | Every record's `_source_manual` must name a book the app can open. |
| `tests/test_spell_registry.py` | 66 | Regression tests for the DM Tools spell registry integrity. |
| `tests/test_static_cache_is_short.py` | 56 | Reference art must not be cached for hours. |
| `tests/test_subclass_table.py` | 112 | Subclass table guards. |
| `tests/test_table_channel.py` | 1416 | Table channel: shared roll feed + live push. |
| `tests/test_template_rendering.py` | 155 | Template rendering tests — verify various character types render without 500s. |
| `tests/test_thumb_caching.py` | 90 | Thumbnails must be encoded once, not once per request. |
| `tests/test_token_placement.py` | 168 | Placing a token has to be visible. |
| `tests/test_topology_verify.py` | 139 | Tests for the generate-then-verify layer. |
| `tests/test_transfer.py` | 170 | Export/import JSON round-trip tests. |
| `tests/test_undo.py` | 403 | Undo: the last action, put back — or refused when the world has moved on. |
| `tests/test_users_service.py` | 40 | Tests for user repository operations. |
| `tests/test_widgets_api.py` | 119 | Table widgets: counters and timers on an encounter or a campaign. |
| `tests/test_xss_escaping.py` | 149 | Stage 0 regressions for the DM-panel XSS defects. |

FILES: 284

---

## C2. Templates (`/home/james/dnd-character-manager/templates/`, 21 files, 520 KB)

| File | Size | Use |
|---|---|---|
| `sheet.html` | 218 KB | **The character sheet.** All tab markup in one file; the tab scripts live in `static/sheet/`. Biggest template by far. |
| `dm_tools.html` | 82 KB | **The DM Tools page.** Encounter builder, NPC library, campaigns, maps, traps, loot panels. |
| `create.html` | 34 KB | Character creation wizard shell (steps injected by `static/create-*.js`). |
| `_sheet_sidebar.html` | 31 KB | Sheet left rail: ability scores, saves, skills, senses. |
| `_sheet_top.html` | 16 KB | Sheet header block: name, class/level, HP, AC, conditions, inspiration. |
| `table.html` | 14 KB | Player table view (`/table/{player_key}`): roll feed + party HP. |
| `campaign_detail.html` | 15 KB | Campaign page: roster, notes, XP, shared stash, links to maps. |
| `dashboard.html` | 12 KB | Character list / home after login. |
| `landing.html` | 11 KB | Marketing/landing page at `/`. |
| `_sheet_modals.html` | 12 KB | Sheet dialogs: spell picker, item add, feat picker, level-up. |
| `map.html` | 10 KB | DM battle-map editor shell. |
| `_sheet_header.html` | 7.8 KB | Sheet identity strip (portrait, race/class, xp bar). |
| `register.html` / `login.html` / `reset_password.html` / `change_password.html` | 3–3.9 KB | Auth pages. |
| `_sheet_scripts.html` | 3.3 KB | Per-tab script include block with cache-busting. |
| `map_player.html` | 5.3 KB | Player-facing mirrored map view. |
| `_char_portrait.html` | 2.6 KB | Reusable portrait widget. |
| `layout.html` | 4 KB | Base shell (nav, theme toggle, asset includes). |
| `_map_toolbar.html` | 1.5 KB | Map editor toolbar partial. |

## C3. Static assets (`/home/james/dnd-character-manager/static/`, ~850 MB)

### Root scripts (28 JS + 2 CSS)

| File | Size | Use |
|---|---|---|
| `dm-library.js` | 1.42 MB | **Largest client asset.** Pre-baked monster/spell/item library the DM panels search offline. |
| `create-consts.js` | 296 KB | Static option tables for the creation wizard (classes, races, backgrounds, spells). |
| `sheet-ref.js` | 272 KB | Pre-baked reference payload for the sheet (features, subclass tables). |
| `vtt.js` | 84 KB | The battle-map canvas: tokens, fog, measuring, drag/drop. |
| `sheet.css` | 46 KB | Sheet styling. |
| `layout.css` | 22 KB | Global shell + theme variables. |
| `sheet.js` | ~20 KB | Sheet bootstrap/tab switching. |
| `dm_tools.js` | — | DM page bootstrap, mounts the `static/dm/*` panels. |
| `create-core.js`, `create-abilities.js`, `create-raceclass.js`, `create-skills.js`, `create-spells.js`, `create-pickers.js`, `create-portrait.js`, `create-submit.js` | — | Creation wizard, one module per step. |
| `dice.js` | — | Dice expression roller + tray UI. |
| `rollfeed.js`, `rollfeed.css` | — | Shared campaign roll feed widget. |
| `grid.js` | — | Client grid maths (square/hex), pairs with `services/map_grid.py`. |
| `entity-search.js` | — | Internal entity search box. |
| `source-filter.js`, `source-slug-map.js` | — | Source-book filtering for rules text. |
| `char-portrait.js` | — | Portrait upload/crop widget. |
| `widgets.js` | — | Small shared UI widgets (collapsibles, chips). |
| `layout.js` | — | Nav, theme toggle, mobile behaviour. |
| `pwa.js`, `sw.js` | — | Service worker registration + offline cache. |

### `static/dm/` — 16 files, one per DM Tools panel
`bootstrap.js` (wires all panels) · `actions.js` (DM action bar) · `builder.js` (encounter builder) · `campaigns.js` · `combat.js` + `combat-ui.js` + `combat-conditions.js` (initiative, HP, conditions) · `encounter-open.js` (open an encounter, spawn to map) · `encounters.js` (list/CRUD) · `items.js` · `loot.js` · `maps.js` (map panel glue) · `npcs.js` · `reference.js` (rules lookup) · `rollfeed.js` · `traps.js`.

### `static/sheet/` — 12 files, one per sheet tab
`edit.js` · `equip.js` (attunement/armour) · `features.js` · `inventory.js` + `inventory-ui.js` · `levelup.js` · `live.js` (HP/conditions during play) · `popups.js` · `spells.js` · `status.js` · `story.js` (backstory/notes) · `summons.js` (summoned creatures).

### `static/pages/` — 5 files
`dashboard.js` · `campaign-detail.js` · `map-chrome.js` + `map.css` (map page chrome) · `page-utils.js` (shared page helpers).

### `static/ui/` — 7 files
`api.js` (fetch wrapper + CSRF header + error toasts) · `dom.js` · `events.js` · `shell.js` · `nav-filter.js` · `source_ref.js` (rules-text source links) · `wakelock.js` (keep screen awake at the table).

### Binary / generated
`static/maps/` 312 uploaded battle maps · `static/ref-portraits/` 3 shards of generated reference art · `static/icons/` 4 PWA icons · `static/data/reference_assets.json` generated reference index.

---

# Appendix D — `ai-dm` file inventory

### Root modules

| File | L | Purpose |
|---|---|---|
| `_travel_test.py` | 32 | — |
| `app.py` | 1204 | FastAPI app: campaign creation, live play, SSE turn stream, state panel. |
| `clean_entities.py` | 21 | — |
| `conftest.py` | 58 | Pytest config. |
| `probe3.py` | 80 | Live probe for batch 3: surprise, withdraw, disengage, fall through run_turn. |
| `probe_chases.py` | 115 | Live probe: chases over the real HTTP API (creates, plays, then cleans up). |
| `probe_rules.py` | 17 | — |
| `scratch_db.py` | 71 | Point AIDM_DB at a throwaway file before dmcore.db resolves its path. |
| `tests_afflictions.py` | 198 | DMG ch.8-9: madness, lingering injuries, massive damage, system shock. |
| `tests_ailments.py` | 214 | Disease and poison (DMG ch. 8): tables, save maths and the rest clock. |
| `tests_ammo.py` | 119 | Ammunition tests — PHB p.146: a shot with an ammunition weapon leaves the quiver. |
| `tests_campaign_delete.py` | 135 | Campaign deletion must take every child row with it, and only that campaign. |
| `tests_casting.py` | 97 | The cast has bodies, and the bodies belong to the region. |
| `tests_chases.py` | 95 | DMG ch. 8 chase rules — the pure tables and geometry. |
| `tests_clocks.py` | 43 | A faction clock line has to end on a plan, not on a preposition. |
| `tests_cm_portrait.py` | 57 | A painted hero reaches the Manager's sheet — once, and never over the player's own art. |
| `tests_cm_sync.py` | 210 | Two-way sheet sync with the DnD Character Manager (dmcore.cm_sync). |
| `tests_combat.py` | 498 | Unit tests for multiattack, damage defenses and concentration (SRD rules). |
| `tests_condition_timers.py` | 71 | Condition durations: stamped on apply, counted down each round, dropped when spent. |
| `tests_conditions.py` | 181 | Conditions must actually bite: SRD roll modes, blocked actions, expiry. |
| `tests_dialogue.py` | 343 | Per-NPC speech boxes: the marker contract, the parser, and the turn plumbing. |
| `tests_dm_consistency.py` | 143 | Regression tests for the two logic misses the table reported: |
| `tests_dmg.py` | 248 | DMG batch 1 tests — attitude-driven social DCs, cover, objects, contests, |
| `tests_dmg2.py` | 140 | DMG batch 2 tests — contests on forced dice, group checks, downtime gating, travel. |
| `tests_dmg3.py` | 208 | DMG batch 3 tests — surprise, falling, opportunity attacks, disengage, light, water. |
| `tests_dmg4.py` | 219 | DMG batch 4 tests — two-weapon fighting offhand swing and the Ready action. |
| `tests_draw.py` | 127 | Draw tests — PHB p.190: drawing a weapon is a free object interaction. |
| `tests_fact_merge.py` | 94 | Entity-fact dedup tests — deterministic, no LLM calls. |
| `tests_fallback_turn.py` | 60 | A turn must never be stored blank. |
| `tests_features.py` | 478 | Class/racial feature usage: intent detection, resource spend, rest refresh. |
| `tests_gate.py` | 215 | The dice gate: nothing in the story moves until the table hands the DM a number. |
| `tests_gear.py` | 360 | Gear ledger tests — purchases land in the pack, worn items are tracked, limits hold. |
| `tests_gear2.py` | 270 | Gear batch 2 — hand slots, armour AC, proficiency/strength warnings, attunement rules. |
| `tests_gear3.py` | 196 | Gear batch 3 — a line about a cloak must never swing, roll or spring anything. |
| `tests_gear_prompt.py` | 47 | — |
| `tests_hazards.py` | 210 | Survival batch — air, food, water, cold and heat behave like the DMG says. |
| `tests_intent.py` | 28 | — |
| `tests_intent_guard.py` | 45 | The intent label is a reading of the player's words, never a verdict on them. |
| `tests_kill.py` | 48 | Drive a fight to the death and confirm XP is awarded on victory. |
| `tests_light.py` | 166 | Light is a fact of the room, and it rules on the eyes. |
| `tests_monsters.py` | 54 | Encounter creatures should follow the scene, not default to a Bandit. |
| `tests_narrator.py` | 221 | Narrator insurance: a table never sees an empty turn. |
| `tests_opening.py` | 211 | The table opens itself: every campaign starts with a real scene, not a prompt. |
| `tests_party_handoff.py` | 138 | Party members created mid-campaign come from the Character Manager's own wizard. |
| `tests_places_gear.py` | 218 | Places and dressing — a metropolis stocks armour, and one verb can carry a list. |
| `tests_play.py` | 146 | End-to-end playthrough: create a campaign, play N scripted turns, report each result. |
| `tests_portrait_images.py` | 269 | Painted portraits: prompt/cache plumbing must hold without a GPU in reach. |
| `tests_portraits.py` | 90 | Procedural portraits: deterministic, valid SVG, and varied enough to name. |
| `tests_prof.py` | 130 | Proficiency staleness — a stored prof_bonus must never understate the level. |
| `tests_quests.py` | 141 | Quest log engine tests — deterministic, no LLM calls. |
| `tests_racial.py` | 122 | Racial traits must behave like class features: matchable, narrated, charge-tracked. |
| `tests_roster.py` | 78 | The cast rail shows every body, and a body's face never depends on where it is drawn. |
| `tests_shop.py` | 246 | Commerce tests — money, SRD pricing, markets, buy/sell transactions. |
| `tests_sight.py` | 168 | Sight rot follows the eyes, not the call site. |
| `tests_smoke.py` | 41 | — |
| `tests_specials.py` | 137 | Monster save-based actions: breath weapons, roars, auras. |
| `tests_spine_variety.py` | 184 | Campaign openings must differ campaign to campaign. |
| `tests_srd_checks.py` | 109 | SRD fidelity for ability checks, and trap-routing precedence. |
| `tests_stt.py` | 136 | Dictation: audio in, words out, and nothing broken when it cannot listen. |
| `tests_sweep.py` | 380 | Sweep every subsystem the engine offers, one real turn each. |
| `tests_traps.py` | 405 | Trap library + trap mechanics tests. |
| `tests_traps_bypass.py` | 432 | Improvised bypass tests — the traps the corpus says cannot be disarmed. |
| `tests_traps_corpus.py` | 152 | Corpus-wide trap coverage: all 446 Character Manager traps must be runnable. |
| `tests_travel.py` | 36 | Destination extraction for the "move/travel" intent. |
| `tests_turns.py` | 274 | Tests for the 5e combat structure: initiative, turn order, monster turns. |
| `tests_voices.py` | 107 | Every speaking body gets a voice, and keeps it. |
| `tests_world.py` | 111 | World-state + continuity guard tests — deterministic, no LLM calls. |

### `tools/`

| File | L | Purpose |
|---|---|---|
| `tools/_probe_sheets.py` | 26 | — |
| `tools/_probe_turn.py` | 53 | Run one real turn against a COPY of the live DB so the game state stays clean. |
| `tools/audit_dmg.py` | 404 | Conformance audit: ai-dm rules engine vs. 5e source manuals (PHB/DMG). |
| `tools/e2e_combat.py` | 105 | End-to-end *combat clear* check on a throwaway DB copy. |
| `tools/e2e_fresh.py` | 114 | End-to-end smoke test on a throwaway DB copy. |
| `tools/e2e_specials.py` | 68 | End-to-end check that monster save-based actions reach a live encounter. |
| `tools/e2e_traps.py` | 144 | End-to-end trap run on a throwaway DB copy, narrator stubbed. |
| `tools/fetch_srd_extras.py` | 57 | One-time SRD top-up: subclass level tables and subclass feature text. |
| `tools/import_manual_items.py` | 286 | Merge the manual item sheets (dnd-character-manager) into the SRD item corpus. |
| `tools/import_manual_monsters.py` | 234 | Normalise the manual monster corpus (dnd-character-manager) into the SRD schema |
| `tools/reparse_turns.py` | 31 | Re-parse stored turns with the player's input in hand; report or apply. |
| `tools/spine_variety_report.py` | 67 | Report the opening of N fresh campaigns, procedurally, with no model in the loop. |
| `tools/srd_coverage.py` | 41 | Coverage check: what SRD data does each app ship, and what does ai-dm actually read? |

### `scripts/`

| File | L | Purpose |
|---|---|---|
| `scripts/dedupe_facts.py` | 59 | Collapse near-duplicate entity facts already stored in the campaign DB. |
| `scripts/repair_segments.py` | 45 | One-shot: re-parse stored turns so speech written before the bare-marker |
| `scripts/verify_sync_live.py` | 59 | Live verification of the two-way sheet sync on campaign 1056. |

### `templates/` and `static/`

| File | Bytes | Purpose |
|---|---|---|
| `templates/base.html` | 735 | Shell: nav, asset includes, page skeleton |
| `templates/dm.html` | 4,618 | DM console: scene/beat control, entities, encounters, traps, memory |
| `templates/index.html` | 3,924 | Campaign list + creation form |
| `templates/journal.html` | 2,011 | Journal/archive browser + markdown export |
| `templates/play.html` | 8,595 | The table: transcript, composer, roll gate, party strip, state panel |
| `static/app.js` | 85,015 | Play page: SSE consumer, transcript, roll prompt, composer |
| `static/apple-touch-icon.png` | 18,804 | iOS home-screen icon |
| `static/create.js` | 6,972 | Campaign creation flow |
| `static/dm.js` | 10,311 | DM console wiring |
| `static/favicon-16.png` | 643 | favicon 16 |
| `static/favicon-32.png` | 1,247 | favicon 32 |
| `static/favicon-small.svg` | 745 | small vector favicon |
| `static/favicon.ico` | 15,086 | favicon |
| `static/favicon.svg` | 1,532 | vector favicon |
| `static/icon-192.png` | 20,310 | PWA icon 192 |
| `static/icon-512.png` | 66,003 | PWA icon 512 |
| `static/journal.js` | 6,323 | Journal browsing + export |
| `static/site.webmanifest` | 485 | PWA manifest |
| `static/style.css` | 50,506 | Whole visual identity (50 KB) |

### `data/` (runtime content, not code)

* `data/aidm.db` — 0 bytes
* `data/archive/` — directory
* `data/backups/` — directory
* `data/dm.db` — 561,152 bytes
* `data/dm.db.bak-20261115-2317` — 1,159,168 bytes
* `data/dm.db.bak-20261119-223920` — 561,152 bytes
* `data/dm.db.bak-archive-20261115-2205` — 1,060,864 bytes
* `data/dm.db.bak-atkfix` — 753,664 bytes
* `data/dm.db.bak-cleanup-20261116-124253` — 565,248 bytes
* `data/dm.db.bak-speakers-20261120183229` — 561,152 bytes
* `data/items_full.json` — 1,571,980 bytes
* `data/monsters_full.json` — 4,834,478 bytes
* `data/portraits/` — directory
* `data/srd_extra/` — directory
* `data/traps.json` — 565,240 bytes
* `data/tts/` — directory

---


# Appendix E — Glossary

| Term | Meaning |
|---|---|
| **campaign** | One save: party + world + story spine. Both apps key almost everything on it. |
| **spine / beats** | The generated three-act campaign skeleton (`story_spine`, `beats`). Fallback is procedural. |
| **scene** | A location/situation the party is inside; turns belong to a scene; closing one rolls a summary. |
| **turn** | One player→DM exchange: input, intent, check result, outcome, narration, state delta. |
| **intent** | The parsed structured form of what the player said (action, actor, target, skill, items). |
| **dice gate / pending roll** | The hard stop where the engine refuses to proceed until the player supplies a die. |
| **plan** | A proposed check/contest with its DC and modifiers, built by `checks.py` (`resolve_*`). |
| **runtime module** | `*_runtime.py` — applies a rule family (rest, cast, attack, ailment, chase, trap, explore). |
| **state_delta** | The recorded mutations a turn caused (HP, gear, conditions, quests, XP). |
| **memory_fts** | FTS5 index over past turn text, used for recall instead of resending history. |
| **entity** | A named person/place in the world with facts, role, agenda, attitude, faction clock. |
| **segments** | Structured narration split by speaker, used for voice/TTS and dialogue boxes. |
| **reference art** | Generated portraits/illustrations for monsters, items, NPCs (sharded under `static/ref-portraits/`). |
| **pack** | A whole campaign exported as one JSON file (`routes/pack.py`). |
| **sheet tabs** | The Character Manager sheet's per-tab JS (`static/sheet/*.js`), one panel each. |
| **DM tools** | The CM's DM surface at `/dm-tools`: encounters, NPCs, campaigns, maps, traps, loot. |
| **roll feed** | The shared campaign dice feed (`routes/table.py` + `static/dm/rollfeed.js`), DM and players together. |
| **player key** | Per-player opaque key used for `/table/{player_key}` and the mirrored map view. |
