# Package A — decisions and evidence (2026-11-22)

Package A is "cleanup & foundation": stranded tables, dead rows, 0-byte files, a
deep health probe, and audit/timeline scaffolding. Each roadmap item was decided
from what the code and the live database actually say, not from the table names.

## Done

| Item | What was actually wrong | Fix |
|---|---|---|
| 0-byte files | `data/dm.db` and `data/dnd.db` (both 0 bytes, neither referenced anywhere in the code) | deleted |
| Stranded probes at the repo root | `probe_rules.py`, `probe3.py`, `probe_chases.py`, `_travel_test.py`, `clean_entities.py` ran work at import time from the root, so a bare `pytest` could collect them | moved to `tools/` with a README, a repo-root bootstrap, no duplicate imports; `probe_rules.py` re-run from the new home |
| Bare `pytest` died before any test | `scripts/test_matching.py` reads `data/manual_cache` at import and got collected | `pytest.ini` with `testpaths = tests` |
| `dm_campaign_characters` empty on live DB | the table shipped with no write path: 5 campaigns had rosters, the table had 0 rows, so every reader parsed `dm_campaigns.characters` and any JOIN silently returned nothing | `services/roster.py` mirrors each roster write into the table (add-character, remove-character, character delete, campaign-pack import), plus an idempotent startup backfill in `db_schema._migrate_roster_join` |

## Decisions — no code change, with the evidence

**`dm_map_scenes` — keep.** It is not a stranded table; it is a shipped feature that
has simply never been used on this database. `routes/maps.py` has the full cycle
(`POST /api/dm/map/{id}/scene` save at :908, `/scene/{id}/restore` at :915,
`/scene/{id}/delete` at :966, list at :264, and `scene_count` in the map list), and
`static/dm/maps.js` drives it. 306 maps, 0 saved scenes means nobody has used it,
not that it is dead.

**`roll_log` — keep.** Written by the live dice path (`routes/table.py:108`) and
read back by the table channel; `tests/test_table_channel.py` pins the schema and
the write path. 0 rows because no dice have been rolled in these campaigns through
the CM interface. It is already the one home for rolls.

**`dm_xp_log` — keep.** Written by the XP award path (`routes/dm/campaigns.py:467`),
read by the ledger view (`:416`), and undo marks rows `reversed = 1`
(`routes/undo.py:75`) instead of deleting them. 0 rows because no XP has been
awarded through the UI yet.

**`dm_npcs` — keep, it is the library.** 15 rows, a list endpoint
(`routes/dm/npcs.py:108`, exposed as `dm_npcs_list`), and the DM page selects from
the same columns (`routes/dm/page.py:107`). Encounters reference it through
`dm_encounter_npcs` (49 rows), which is the "encounter blob" the roadmap worried
about — the reusable library and the per-encounter roster are separate tables and
both are in use.

**`data/ingestion.log` (0 bytes) — keep.** It is an append-only log written by
`ingest_manual.py`; being empty only means nothing has been ingested since it was
created.

## Still open in Package A

- `event_log(kind, actor, target, before, after, ts)` plus
  `GET /api/character/{id}/history.json` and `GET /api/campaign/{id}/timeline.json`.
- `?deep=1` health probe (GPU box, prose LLM, SRD cache, disk, db) surfaced on a
  `/status` page.
