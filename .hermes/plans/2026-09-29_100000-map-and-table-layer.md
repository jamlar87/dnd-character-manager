# Map & Table Layer — Implementation Plan

> **For Hermes:** execute task-by-task in order; each task ends with a green test run and a commit.
> Repo conventions live in the `dnd-character-manager` skill (read it before touching routes).

**Goal:** Fold the Atlas-VTT feature set into the D&D Character Manager as a spatial map layer plus
the cheap table-side wins, reusing the monster/NPC/encounter/art machinery that already exists.

**Architecture:** No new framework. Maps are rows keyed by campaign, tokens reference the existing
reference library by `(kind, name)` so token art comes from `/api/ref-image/{kind}/{name}` with zero
new art pipeline. Map images go through `services/images.py::normalize_portrait` (the single image
write contract) and live as files under `static/maps/`, served with the same short cache header as
reference art. The canvas is its own versioned asset (`static/vtt.js` + `static/vtt.css`) — never
inline in `dm_tools.js` (8.3k lines, page already 102 KB).

**Tech stack:** FastAPI + SQLite (`services/db_schema.py` migrations), Jinja templates, vanilla JS
canvas, `static_asset_version()` cache busting.

---

## Progress (updated as work lands)

- **Slice 0 — DONE** (committed + pushed): dice click-to-roll (`static/dice.js`), tracker
  keyboard/click-to-locate, table widgets (counters + timers, per encounter and per campaign).
- **Slice 1 — DONE** (committed): 1.1 schema, 1.2 map CRUD + ownership, 1.3 image upload
  to `static/maps/`, 1.4 canvas (`/dm-map/{id}` + `static/vtt.js`: pan/zoom/fit, square + hex
  grids with snapping, token art from the existing routes, drag-to-move, HP/size/hide/label,
  palette search, camera memory, **snapshots** = 1.7), **1.5 spawn-from-encounter** (footprint
  from the monster's role line; token HP mirrors back onto the tracker row and marks a
  combatant defeated at ≤0), **1.6 fog of war + drawing** (two overlay layers, saved separately
  from placements, carried by snapshots), plus the 🗺️ Maps tab in DM tools.
- **Slice 2 — DONE** (committed): the player view — `/dm-map/{id}/player` (chromeless, key
  auth so a TV/tablet needs no login), a server-side projection (`/api/dm/map/{id}/state?k=`)
  that withholds the DM's drawing, hidden tokens and everything under the fog, poke via
  BroadcastChannel + a 3 s poll, Follow/Free-look, one `readOnly` renderer mode.
- **Slice 3 — DONE** (committed): campaign packs — `GET /api/dm/campaign/{id}/export` (one
  self-contained JSON file: campaign, characters, NPCs, maps with tokens/fog/draw/setups, the
  encounters behind them, and base64 images) and `POST /api/dm/campaign/import` (always a NEW
  campaign, every id remapped, never a secret). Buttons: 📦 Export on the campaign page, 📥
  Import on the campaign page and the DM-tools campaign tab. Details:
  `references/campaign-packs.md`.
- **Remaining (nice-to-haves):** rectangle/circle fog brushes, a measure tool, lighting, and the
  Fantasy Statblocks YAML importer (MIT) as a slice-4 idea.
- Live example map kept for inspection: **`/dm-map/4`** ("Example — dungeon crawl", a synthetic
  test image, 3 tokens, one revealed strip of 4 cells and a green pen stroke from the
  verification run — clears with 🚫 All and 🧽 Clear).

---

## Ground truth (verified, not assumed)

- Tables today: `dm_campaigns`, `dm_encounters`, `dm_encounter_npcs` (**already carries
  `initiative`, `hp_current`, `hp_max`, `defeated`**), `dm_campaign_characters`, `campaign_team_items`.
  No map/scene/token/table exists anywhere.
- Combat endpoints already exist: `POST /api/dm/encounter/{id}/{roll-initiative,update-initiative,combat-state}`,
  `POST /api/sync-combat-hp`, `GET /api/dm/characters-for-combat`.
- Migrations are `CREATE TABLE IF NOT EXISTS` + `try: ALTER TABLE ... except OperationalError: pass`
  inside `init_db()` (`services/db_schema.py:213-280`), versioned in `schema_migrations`.
- `static_asset_version(name)` — `main.py:572`; `/static/` and `/api/ref-image/` get
  `public, max-age=300, must-revalidate` and **no cookie** (`main.py:429-470`); a cookie means
  Cloudflare BYPASS.
- `normalize_portrait(value, max_px=1024)` — `services/images.py:82`.
- `.gitignore` already ignores `static/ref-portraits/`; `static/maps/` needs the same.
- DM tabs: campaigns, encounters, combat, monsters, spells, npcs, items, traps, manuals
  (`templates/dm_tools.html:402-410`).
- `openEncounter(id)` = encounter + combat UI (`static/dm_tools.js:867`); `showMonster(index)` =
  stat block popup (`static/dm_tools.js:408`).

---

## Salvage & licensing (researched 2026-09-29, before writing any of it)

Repo: `github.com/ByteMirror/atlas-vtt` (Fabian Urbanek). **LICENSE = GNU AGPL-3.0-only**
(read from the raw LICENSE file; releases ≤ 0.1.6 were PolyForm Noncommercial, current is AGPL).

**HARD RULE: copy no Atlas code into this app.** AGPL §13 is network copyleft: serving a
derived work to users obliges you to offer them the complete corresponding source. This app is
served on `characters.jamlarnet.stream` to accounts other than James (`tyguymoore`,
`tylerclaygreen778`, `bronjstevens`), and its repo is private — so any lifted Atlas source
would demand publishing this app's source to those users. Ideas, algorithms and file FORMATS
are not copyrightable and are fine to reimplement; their source, their token-ring art and
their React/PixiJS architecture are not (and the latter two would be a rewrite anyway).

Safe to take, ranked by value:

1. **Fantasy Statblocks (javalent) — MIT, verified.** It defines the de-facto Obsidian 5e
   statblock format (YAML frontmatter in markdown). An importer here means any community
   bestiary a user already has becomes a library import instead of hand entry. Highest-value
   salvage by far. (Their monster DATA is not needed — the app has 1,798 of its own.)
2. **Icons: game-icons.net, CC BY 3.0** (widgets, map pins, token, end-combat, loot coin) and
   the starter class tokens, **CC BY 4.0**. Usable in-app with a `CREDITS.md`. Directly needed
   by Task 0.3 (widget icons) and Task 1.4/1.6 (pins, toolbar).
3. **Kenney "Impact Sounds" / "Casino Audio" — CC0** for dice-roll and timer feedback; the
   knotwork dice-toast corners are CC0 too. No attribution needed.
4. **Interface icons: Lucide, ISC** (what Atlas itself uses; also bundled with Obsidian).
5. **Every package Atlas bundles is MIT** (floating-ui, radix, pixi/colord, zustand, zundo,
   jszip). If we want one, take it from npm — never from their bundle.
6. **Format interop, not code**: their scenes are `.atlasmap` files with tags/thumbnails in
   `.atlas-data`, and there is a collection-bundle export (`collectionBundle/bundleFormat.ts`).
   A `POST /api/dm/campaign/import-atlas` would let James move prep he already made into this
   app. Implement it by parsing an artifact he exports from the plugin (or public docs) — the
   clean posture is to work from the file format, not from their TypeScript.
7. **Ideas to reimplement ourselves (no code, no licence surface):** grid auto-detection from
   the map image, hex geometry + numbering, fog reveal model, per-scene snapshots, camera
   memory, and "click a creature in the tracker → locate its token on the map".

---

## Slice 0 — table-side wins (no new subsystem)

### Task 0.1 — Click any dice expression to roll

**Objective:** A single shared asset turns any `2d6+3`-shaped text in rendered content into a
clickable roll, with a result toast — the video's best small feature.

**Files:**
- Create: `static/dice.js`
- Create: `tests/test_dice_roller.py`
- Modify: `templates/layout.html` (one `<script src=... static_asset_version('dice.js')>` so every page has it)

**Contract (assert in the test by reading the asset as text, as this repo does for JS guards):**
- `window.DiceRoller = { enhance(root), roll(expr), parse(expr) }`
- Regex must match `1d20`, `2d6+3`, `1d8 - 1`, `4d6kh3`-free (no advantage syntax in v1), and must
  NOT match a bare number, a date, or `p.222` style page refs.
- `enhance(root)` wraps matches in `span.dice-roll` with `role="button"`; it must skip
  `<script>`, `<style>`, `<textarea>`, `input`, and any node already inside `.dice-roll`
  (idempotent — calling twice must not double-wrap).
- `roll()` uses `Math.random()`; output includes every die face + total.
- No `eval`, no `innerHTML` of user text (build nodes with `textContent`).

**Steps:** write failing test → run (`pytest tests/test_dice_roller.py -q`, expect FAIL) → write
asset → pass → wire `showMonster()` (`static/dm_tools.js:408`) to call `DiceRoller.enhance(el)` on
its rendered popup → commit.

### Task 0.2 — Encounter tracker: click-entry-to-locate + keyboard nav

**Objective:** Clicking an initiative row highlights and scrolls to that combatant; `J`/`K`/arrows
move the active row; the tracker can be exposed read-only (for the future player view).

**Files:** Modify `static/dm_tools.js` (inside `openEncounter`/`updateInitiatives`), `templates/dm_tools.html`
(CSS class `.init-active`), Test: `tests/test_encounter_tracker_ui.py` (asset-text guards: the
`.init-active` class exists in CSS, the click handler calls a scroll-into-view, and the keyboard
handler only binds while the encounter panel is open).

### Task 0.3 — Table widgets (timers, counters, per-scene flag)

**Objective:** The video's widget bar: named counter with icon/colour and ±1, a start/reset timer,
and "show on every scene" meaning campaign-scoped instead of encounter-scoped.

**Files:**
- Modify: `services/db_schema.py` (two ALTERs: `dm_encounters.widgets TEXT DEFAULT '[]'`,
  `dm_campaigns.widgets TEXT DEFAULT '[]'`)
- Modify: `routes/dm.py` (GET/POST `/api/dm/{encounter|campaign}/{id}/widgets`, `_require_owned`
  + campaign ownership)
- Create: `static/widgets.js`, Modify `templates/dm_tools.html` (widget bar in the combat panel)
- Test: `tests/test_widgets_api.py` (temp DB: create → tick → reset → cross-user 404)

---

## Slice 1 — the map layer (the real build)

### Task 1.1 — Schema + migrations

```sql
CREATE TABLE IF NOT EXISTS dm_maps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, campaign_id INTEGER NOT NULL,
  name TEXT NOT NULL, image_path TEXT DEFAULT '',
  grid_type TEXT DEFAULT 'square', grid_size INTEGER DEFAULT 50,
  grid_offset_x INTEGER DEFAULT 0, grid_offset_y INTEGER DEFAULT 0,
  parent_map_id INTEGER, camera TEXT DEFAULT '', notes TEXT DEFAULT '',
  shared INTEGER DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dm_map_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, map_id INTEGER NOT NULL,
  x REAL DEFAULT 0, y REAL DEFAULT 0, w INTEGER DEFAULT 1, h INTEGER DEFAULT 1,
  kind TEXT DEFAULT 'creature', ref_name TEXT DEFAULT '', label TEXT DEFAULT '',
  hp_current INTEGER DEFAULT 0, hp_max INTEGER DEFAULT 0,
  hidden INTEGER DEFAULT 0, z INTEGER DEFAULT 0,
  encounter_en_id INTEGER, character_id INTEGER
);
CREATE TABLE IF NOT EXISTS dm_map_scenes (       -- named snapshots / "game saves"
  id INTEGER PRIMARY KEY AUTOINCREMENT, map_id INTEGER NOT NULL,
  name TEXT NOT NULL, snapshot TEXT DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```
Test: `tests/test_schema_migrations.py` extended — a fresh DB has the three tables; an old DB
(created without them) gains them on `init_db()`; columns recorded in `schema_migrations`.

### Task 1.2 — Map CRUD + ownership

`routes/maps.py` (new router, imported by `routes/__init__` the same way the others are):
`GET/POST /api/dm/map/create`, `GET /api/dm/map/{id}`, `POST /api/dm/map/{id}/update|delete`,
`POST /api/dm/campaign/{cid}/map/create`. Every route: `require_user` + `_require_owned(db, user,
"dm_maps", map_id)` (admin bypasses). Delete cascades tokens + scenes.

Test: `tests/test_maps_api.py` — owner 200, non-owner 404, row unchanged after the 404 (never test
a write fix against live rows — temp DB only).

### Task 1.3 — Map image upload

`POST /api/dm/map/{id}/image` with a data URL → `normalize_portrait(value, max_px=4096)` → write
`static/maps/map-{id}-{sha1[:8]}.webp` → store `image_path`. Add `static/maps/` to `.gitignore`.
Serve by the existing static mount (short cache header already applies).

Test: oversized junk rejected with the value unchanged; a 1×1 PNG accepted and the file appears.

### Task 1.4 — Canvas renderer

`static/vtt.js` + `static/vtt.css`: pan/zoom, image draw, grid overlay (square + hex with
numbering), token layer drawing from `dm_map_tokens` (art via `/api/ref-image/{kind}/{name}?size=`
for creatures/npcs, `/api/character/{id}/portrait-image?size=` for PCs), drag to move, resize,
right-click menu (hide from players, set HP, remove). Camera per map persisted in `localStorage`
**and** the map row (video's "stays where you left it"). Debounced token-position saves.

### Task 1.5 — Spawn from an encounter

`POST /api/dm/map/{id}/spawn-encounter` takes an encounter id, reads `dm_encounter_npcs`
(composition + `hp_current/hp_max` already there), places tokens around the map centre and links
`encounter_en_id`. Two-way HP sync reuses the `sync-combat-hp` pattern.

### Task 1.6 — Fog of war + draw

Canvas mask; reveal = brush strokes stored as a compact per-scene JSON (debounced save). Draw tool
writes temporary strokes broadcast to the player view only.

### Task 1.7 — Snapshots (game saves)

`POST /api/dm/map/{id}/snapshot` serialises tokens + fog + camera into `dm_map_scenes.snapshot`;
`POST /api/dm/map/scene/{id}/restore` writes it back. Thumbnail = the existing ref-image of the
first token, or a CSS placeholder.

---

## Slice 2 — player view

- Local (his use case): same browser, second window, `BroadcastChannel('dnd-map')` — no server round trip.
- Remote: `GET /campaign/{cid}/display?player=1` reading a `map_state` row, polled at 1–2 s (app has
  no sockets; polling matches existing patterns). `visible_to_players` per token, fog mask applied,
  `frozen` boolean honours the DM's freeze, `overlay_image` shows an image to players.
- Access: campaign membership (roster JSON), the same predicate family as `main._user_dms_character`.

## Slice 3 — campaign pack export/import

Extend the character export pattern (`/api/character/{id}/export` → `version: 1`) to
campaign + maps + tokens + encounters + NPCs as a versioned JSON blob: `GET
/api/dm/campaign/{id}/export`, `POST /api/dm/campaign/import`.

---

## Verification (every slice, per the repo skill)

1. `pyflakes main.py routes/ services/ data.py` — zero undefined names; read `redefinition of unused` too.
2. Full suite (background, `exec` the interpreter, ~8 min) — count must not drop.
3. Flow sweep on a **temp copy of `data/`** — GET+POST with real payloads, skip `/api/ai/*`,
   zero 5xx.
4. Restart the service (kill the main PID; systemd restarts it) → public 200 → live probe with a
   minted session on real rows; delete the temp session.
5. Rendered page weight for the touched pages; new assets must be their own files, not inline.
6. Commit + `git push` (plain, never `env -u GITHUB_TOKEN`).

## Risks / open questions

- **Image size:** 4096 px maps through `normalize_portrait` — confirm WebP size stays sane; store as
  a file, never a data URL in a row (walking that path is how this app got 3 MB sheets).
- **Fog storage:** rect-list JSON grows with painting; cap per scene and compact on save.
- **Player view transport:** BroadcastChannel first (zero infra); SSE only if remote play is real.
- **Scope:** v1 = Slice 0 + 1.4/1.5/1.7. Fog (1.6) and Slice 2 land after the canvas is proven.
