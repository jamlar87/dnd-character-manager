# Seven work packages — AI-DM + Character Manager

Recovered 2026-11-22 from the planning session (the original file was lost; this
copy is now the reference of record). Order of record: C (AI-DM context) was
delivered first, then A, then the rest.

Status: **C1–C7 done** (continuity guard v2, dossier, tone guard, beat adherence,
recap ladder, long-campaign tests, AI feature flags). **A in progress** — cleanup,
0-byte files, probe relocation, stranded-table decisions and `?deep=1` health are
done; see `2026-11-22-package-a-decisions.md` for the evidence behind each table
decision. Remaining in A: `event_log` + the history/timeline endpoints.

I'm going to package the work into 7 coherent slices so each slice ships its own evidence, then propose an order, then dig into the AI-DM-specific work because that's your priority.

WORK PACKAGES
Package A — Cleanup & Foundation
T1 items: stranded tables, dead rows, 0-byte files, deep health probe, audit/timeline scaffolding.

Concrete deliverables:

Delete data/aidm.db (0 bytes, junk).
Promote clean_entities.py, probe_rules.py, _travel_test.py into tools/ or tests/ with proper headers.
Decide on dm_campaign_characters: migrate dm_campaigns.characters JSON into it as authoritative, OR drop the table. Real migration script with backup.
Decide on dm_map_scenes: ship the "load scene" UI button OR drop the table.
Pick one home for roll/XP logging. Either populate roll_log and dm_xp_log from the live paths, OR drop them.
Decide on dm_npcs: build the UI that surfaces the reusable library (NPCs > encounters), OR document the encounter blob as the library.
New event_log(kind, actor, target, before, after, ts) table in CM, passive writers, GET /api/character/{id}/history.json and GET /api/campaign/{id}/timeline.json.
Wire ?deep=1 health probe to actually check GPU box, prose LLM, SRD cache, disk, db. Surface on a /status page.
Tests for each.
Risk: low. Nothing breaks the live game.
Time: ~1 week.
Evidence: pytest green, live /api/health?deep=1 returns expected JSON, audit endpoints return non-empty after one turn.

Package B — Shared Rules Library
T1/2: extract 5e rules into a third package both apps import.

Concrete deliverables:

Stand up ~/dnd-rules/ (path-imported, not PyPI) with: abilities.py, skills.py, conditions.py, spells.py, monsters.py, combat.py, rest.py, movement.py, srd_cache/.
Move the deterministic bits from CM's services/leveling.py and services/rules.py into it. Move the deterministic bits from ai-dm's dmcore/rules.py, dmcore/inventory.py, dmcore/conditions.py, dmcore/spells.py into it.
Both apps re-export ~/dnd-rules/ items. Old import paths still work for one minor version.
Tests run from ~/dnd-rules/ with its own conftest; both apps drop duplicated tests.
Live probe on each app after the move: same campaign, same expected roll.
Risk: medium. Two apps import from one source. Any non-determinism in either app's prior copy gets surfaced as a test diff. Mitigation: snapshot both apps' test outputs before and after the move.
Time: ~1-2 weeks. This is the single biggest payback and the prerequisite for §3.2 (battle-map bridge).
Evidence: pytest green in all three places, live probe in both apps, code-count drops: dmcore/rules.py 1,494 L → ~700 L; inventory.py 1,636 L → ~800 L.

Package C — AI-DM Context Reinforcement
Your stated priority. The "rock solid DM" package.

Concrete deliverables:

C1. Continuity Guard v2 (dmcore/contradiction.py — currently 57 L, 2 fns, undersized for what it's supposed to do)

Temporal claim tracker: every player turn writes an assertion_set of facts the player implies (NPC presence, location, possession). Contradictions against entities/memory_fts flagged before narration commits.
Cross-session fact consistency: when the player returns, the engine asserts "last time the bandit's name was X" vs current entity dossier.
NPC age / time-of-day / place consistency checks.
Tests: 8+ scenarios that simulate a contradiction.
C2. Entity Dossier Quality (entities table, worldstate.py at 277 L)

Schema migration: add relations, secrets, motivations, last_contradicted_at, disposition_history, voice_id.
Auto-merge on near-duplicate entities (Levenshtein ≤ 2) when the engine encounters a near-match.
Active tracking: every turn that mentions an entity updates its dossier; engine can recap "what do we know about X?"
Tests: 5+ scenarios that grow a dossier over 30+ turns and verify continuity.
C3. Tone Guard & Voice Consistency

Voice embeddings per NPC: capture on first speech, detect drift on later speech.
Detect narrator breaking character (third-person tense drift, modern word slips).
NPC dialogue attribution validation: ensure quote markers match the segments.
Tests: 4+ scenarios with deliberate drift injected.
C4. Story Beat Adherence

beats table already exists (9 rows). Engine checks current scene against the active beat's trigger field; if the trigger has fired and the beat is still pending, surface a DM-facing warning.
Drift detection: scenes outside the active beat's location/tone get a soft warning, never a hard block (the DM gets to override).
Spine re-anchoring on completion: when a beat flips to done, the engine re-reads the spine for what's next.
Tests: 6+ scenarios over a full 3-act campaign.
C5. Recap Memory Consolidation

Hierarchical memory: raw turns → scene summary → act recap → campaign arc. Already have summaries (23 rows) — extend the scope model.
Memory pruning: after a scene closes and its summary is committed, the raw turns can be compacted for FTS but never deleted.
Recap command (/api/campaign/{cid}/recap): now returns a layered recap, not a flat one.
Tests: 4+ scenarios that prove "what happened last session" is answerable from the consolidated memory alone.
C6. Long-Form Playthrough Tests

New tests_long_campaign.py: scripted 50-100 turn campaigns that run end-to-end on a sandbox DB, then assert the engine's recall, entity dossiers, and beat adherence at the end.
Specifically test: "after 80 turns, can the engine correctly say who has the MacGuffin?" "after 60 turns, what is the goblin leader's motivation?"
These tests run with stubbed LLM (deterministic intent + deterministic narration) so they are fast and not flaky.
C7. Feature Flags for AI Behavior

Per-campaign AI behavior toggles: strict_continuity, enforce_beat_adherence, voice_consistency_check, tone_preset, narrator_verbosity.
Set at campaign creation, editable in DM console.
Gates risky prompt changes behind a flag so a model regression is reversible without code rollback.
Risk: medium. Touches the turn engine (engine.py) — every turn passes through it. Mitigation: feature flags default to current behavior; new behaviors opt in per campaign. Probes run on a sandbox DB.
Time: ~2-3 weeks for the full package. Can split into C1-C7 sub-slice deliveries if you want staged approval.
Evidence: tests_long_campaign.py green over 80+ turns, recap command returns hierarchical output, feature flag toggles work end-to-end.

Package D — CM UX Polish
T2: code-split dm-library.js, mobile audit, a11y pass, theming tokens, spell picker merge.

Code-split dm-library.js (1.42 MB) into panel-scoped lazy chunks. Network panel proves each DM panel only loads what it needs.
Mobile audit of sheet.html and dm_tools.html: drawer sidebar, stacked tabs, thumb-sized controls. Test on Firefox mobile viewport.
A11y: focus order, ARIA roles for condition chips / dice tray, contrast on theme toggle, keyboard nav for map canvas. New tests/test_a11y.py.
Theming: single :root token map. Spacing, radius, colour all tokenized.
Spell picker merge: one render path "I am a Sorcerer 5 with PHB+XGE — show me spells I can prepare now."
Risk: low-medium. Touches the largest template; sheet render tests guard the layout.
Time: ~1-2 weeks.

Package E — New Features
T3 items.

Voice-first mode (ai-dm): mic → intent → prose → TTS loop. Real product bet; sherpa-onnx STT is small but not great. 1-2 weeks just to know if the loop works.
Battle-map ↔ ai-dm bridge: depends on Package B. CM exposes GET /api/campaign/{cid}/map_state; ai-dm gains POST /api/campaign/{cid}/map_event. Cover/AoO/cover maths align with the map. 1 week.
Real-time multiplayer: WebSocket upgrade of the player mirror. Multi-tab desync fix. 2 weeks.
Homebrew toggle: homebrew.json per campaign/character, loaded at startup. Already 60% of the infra exists. 1 week.
Reference-art cross-linking: wire portrait generation into NPC create. 2-3 days.
Encounter difficulty audit: post-build XP-adjusted CR estimator. 1 week.
Scene generator (ai-dm): director.py extension that time-budgets a session. 1 week.
Retrospective view: end-of-campaign aggregated readout. 2-3 days.
Risk: medium-high for E1 and E2. Low for E4-E8.

Package F — Architectural Rewrites
T4 items. Each needs its own explicit approval because they restructure the data layer.

Per-campaign DB isolation (ai-dm): data/campaigns/<cid>.db with summaries and memory_fts global. Big change; required before scale. 2 weeks.
Replace JSON-in-TEXT for hot paths: inventory, combat_state, turns.intent, turns.state_delta → real tables. 2-3 weeks.
Shared ~/dnd-shared/ library: bigger than Package B; package and auth conventions. 2-4 weeks.
Real SSE broker (replace in-process events.py): Redis pub/sub or SQLite-based broker. Required before any WebSocket work. 1-2 weeks.
CM characters table split (81 columns): vertical into characters_identity, characters_combat, characters_progression, characters_inventory. 1-2 weeks.
Risk: high. Every existing test must pass; every undo snapshot must migrate.

Package G — Cross-cutting
T5: integration tests, observability/metrics, OpenAPI docs, backup/restore UX, dev container, feature flags.

Integration tests: tests/test_cm_ai_roundtrip.py for the cross-app sync. 2 days.
Observability: requests_log table in CM, /api/metrics route. 1 week.
OpenAPI docs: FastAPI auto-OpenAPI + Swagger UI mount. 1 day in CM (admin-gated), free in ai-dm.
Backup/restore UX: GET /api/admin/restore-preview?date=…. 2 days.
Dev container: docker-compose.yml per app with stub DBs. 1 week.
Feature flags infrastructure (different from Package C7's AI flags): general-purpose toggle system. 1 week.
