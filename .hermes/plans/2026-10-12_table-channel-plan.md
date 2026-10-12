# Table Channel (roll feed + live push) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline) or
> superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** every roll taken anywhere in the app lands in a shared, campaign-scoped feed on the
DM's screen and each player's device within ~100 ms, and player devices can roll their own dice
into that feed without gaining write authority over any game state.

**Architecture:** one write path (`POST …/roll`) → durable `roll_log` row in SQLite → non-blocking
in-process publish → SSE streams (DM session-scoped, player key-scoped and filtered). The table is
the source of truth; SSE is only the fast path, so a reconnect replays from `roll_log`.

**Tech stack:** FastAPI (`StreamingResponse`, `text/event-stream`), SQLite (WAL, `init_db()`
migrations), Jinja templates, vanilla JS assets with `static_asset_version()`.

**Spec:** `.hermes/plans/2026-10-12_table-channel.md` (decisions, probe evidence, security, risks).

## Global Constraints

- Assets: one versioned `static/<feature>.js` + `.css` per surface, loaded via
  `static_asset_version()`; NEVER inline into `dm_tools.js` or a template.
- DB changes go through `init_db()` in `services/db_schema.py` only.
- Every write route needs the ownership check the rest of its family uses
  (`_own_campaign(db, user, id)`-style; see `routes/dm.py` campaign routes).
- Ownership/auth probes must cover: owner 200, unrelated user 404, key-of-other-campaign 404.
- Player surface may write rolls and nothing else.
- Roll expressions validate against `^\d{0,3}d\d{1,3}([+-]\d{1,3})?$` (count ≤ 100, sides ≤ 1000).
- Hidden rolls are filtered server-side, never client-side.
- Retire the sheet's 2 s poll and the player view's 3 s poll ONLY after the stream is verified
  in a real browser on the deployed service.
- Scripted probes against the public URL must send `User-Agent: Mozilla/5.0` (edge 403s otherwise).
- Full suite is ~1,026 tests / ~14 min: run it backgrounded, unpiped, alone (not beside a
  service restart or a browser driving the live app).

---

## Progress (updated as work lands)

- **Slice 1 — DONE** (committed): Task 1 schema (`roll_log`, `dm_campaigns.player_key`,
  `REQUIRED_TABLES`), Task 2 bus (`services/events.py`), Task 3 write path + history,
  Task 4 DM SSE stream. Live-verified: a roll posted through the public API appeared on the
  public stream 0.19 s later.
- **Slice 2 — DONE** (committed): Task 5 player key + filtered stream + player rolls (rate
  limited, roster-checked, forced `who='player'`/`hidden=False`), Task 6 dice sink +
  `static/rollfeed.js`/`.css`, Task 7 DM panel + `/table/{key}`. Live-verified in a real
  browser: hidden roll never reached the player page, visible one arrived with no reload,
  revoking the key closed a live stream in 20.2 s.
- **Slice 3 — PARTIAL** (committed): the sheet's read-only feed is in
  (`/api/character/{id}/feed[/stream]`, the players' projection, verified live on Garim's
  sheet). **The poll retirement did NOT happen**, deliberately: `syncSheetHP`'s 2 s poll and
  the map player view's 3 s poll fetch combat/HP state, which this channel does not carry, so
  switching them off would silently stop HP syncing at the table. That needs a new `hp`/state
  event kind published from every DM-side HP write — its own slice, with its own verification.
- **Task 9 — DONE** except the route/flow sweep (`references/route_sweep.md`): the new routes
  were exercised end-to-end over real HTTP instead, which covers the UnboundLocalError class
  for these paths; run the sweep anyway on the next change that touches them.

### Task 1: Roll log schema (table + campaign key)


**Files:**
- Modify: `services/db_schema.py` (inside `init_db()`, next to the other `CREATE TABLE IF NOT EXISTS`)
- Test: `tests/test_table_channel.py`

**Interfaces:**
- Produces: table `roll_log(id, campaign_id, character_id, character_name, who, source, label, expression, results, total, hidden, created_at)`, index `idx_roll_log_campaign`, column `dm_campaigns.player_key TEXT DEFAULT ''`.

- [ ] **Step 1: failing test** — `tests/test_table_channel.py`

```python
def test_roll_log_schema_and_campaign_key(test_db):
    con = sqlite3.connect(str(test_db)); con.row_factory = sqlite3.Row
    cols = {r["name"] for r in con.execute("PRAGMA table_info(roll_log)")}
    assert {"id", "campaign_id", "character_id", "who", "expression", "total", "hidden"} <= cols
    camp_cols = {r["name"] for r in con.execute("PRAGMA table_info(dm_campaigns)")}
    assert "player_key" in camp_cols
    con.close()
```

- [ ] **Step 2: run it, expect FAIL** (`no such table: roll_log`) — `pytest tests/test_table_channel.py -k schema -v`
- [ ] **Step 3: implement** — append to the `executescript` in `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS roll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    character_id INTEGER,
    character_name TEXT DEFAULT '',
    who TEXT NOT NULL DEFAULT 'player',
    source TEXT DEFAULT '',
    label TEXT DEFAULT '',
    expression TEXT NOT NULL,
    results TEXT NOT NULL DEFAULT '[]',
    total INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_roll_log_campaign ON roll_log(campaign_id, id DESC);
```

and, after the script, the additive-column pattern already used in this file:

```python
for _sql in ("ALTER TABLE dm_campaigns ADD COLUMN player_key TEXT DEFAULT ''",):
    try:
        db.execute(_sql)
    except sqlite3.OperationalError:
        pass
db.commit()
```

- [ ] **Step 4: run test, expect PASS**
- [ ] **Step 5: commit** — `feat: roll_log table and campaign player key`

---

### Task 2: The event bus

**Files:**
- Create: `services/events.py`
- Test: `tests/test_table_channel.py`

**Interfaces:**
- Produces: `subscribe(campaign_id: int) -> asyncio.Queue`, `unsubscribe(campaign_id: int, q)`,
  `publish(campaign_id: int, event: dict) -> None`, `subscriber_count(campaign_id: int) -> int`,
  `QueueFull` policy = drop that subscriber.
- Consumes: nothing.

- [ ] **Step 1: failing tests**

```python
import asyncio
from services import events

def test_publish_reaches_each_subscriber():
    q = events.subscribe(1); q2 = events.subscribe(1)
    events.publish(1, {"kind": "roll", "id": 7})
    assert q.get_nowait()["id"] == 7 and q2.get_nowait()["id"] == 7
    events.unsubscribe(1, q); events.unsubscribe(1, q2)

def test_publish_with_no_subscribers_is_a_noop():
    events.publish(999, {"kind": "roll"})      # must not raise

def test_publish_into_a_campaign_does_not_leak():
    a = events.subscribe(1); b = events.subscribe(2)
    events.publish(1, {"kind": "roll", "id": 1})
    assert a.qsize() == 1 and b.qsize() == 0
    events.unsubscribe(1, a); events.unsubscribe(2, b)

def test_a_stalled_subscriber_is_dropped_not_blocking():
    q = events.subscribe(3)
    for i in range(events.QUEUE_MAX + 5):
        events.publish(3, {"kind": "roll", "id": i})     # must never block or raise
    assert events.subscriber_count(3) == 0
```

- [ ] **Step 2: run, expect FAIL** (`ModuleNotFoundError: services.events`)
- [ ] **Step 3: implement** `services/events.py`:

```python
"""In-process fan-out for the table channel.

One uvicorn process serves this app (verified), so an asyncio queue per subscriber reaches
everyone. Correctness never depends on it: roll_log is the source of truth and a reconnecting
client replays from it. If the service ever runs multiple workers, move this to the DB or Redis.
"""
import asyncio

QUEUE_MAX = 200
_subs: dict[int, set[asyncio.Queue]] = {}

def subscribe(campaign_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.setdefault(int(campaign_id), set()).add(q)
    return q

def unsubscribe(campaign_id: int, q: asyncio.Queue) -> None:
    s = _subs.get(int(campaign_id))
    if not s:
        return
    s.discard(q)
    if not s:
        _subs.pop(int(campaign_id), None)

def subscriber_count(campaign_id: int) -> int:
    return len(_subs.get(int(campaign_id), ()))

def publish(campaign_id: int, event: dict) -> None:
    """Non-blocking. A full queue means a stalled client: drop it, never the roller."""
    for q in list(_subs.get(int(campaign_id), ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            unsubscribe(campaign_id, q)
```

- [ ] **Step 4: run, expect PASS**
- [ ] **Step 5: commit** — `feat: in-process event bus for the table channel`

---

### Task 3: Roll write path + history (DM)

**Files:**
- Create: `routes/table.py`
- Modify: `main.py` (include the router beside the others at `include_router` block)
- Test: `tests/test_table_channel.py`

**Interfaces:**
- Consumes: `services.events.publish`, `roll_log`, `dm_campaigns.player_key`.
- Produces: `insert_roll(db, *, campaign_id, character_id=None, character_name="", who="player",
  source="", label="", expression, results: list[int], hidden=False) -> dict` (validates, prunes to
  500, returns the stored row as a dict) and `ROLL_RE`.

Validation rules:
- `ROLL_RE = re.compile(r"^\s*(\d{0,3})d(\d{1,3})\s*([+-]\s*\d{1,3})?\s*$", re.I)`; count (empty→1) ≤ 100,
  sides ≤ 1000; anything else → `ValueError`.
- `results` must be a list of ints whose length equals the parsed count, each within `1..sides`.
- `total` is computed server-side as `sum(results) + mod` — a client-sent total is ignored.
- `character_name` is resolved from the `characters` row when `character_id` is given.

- [ ] **Step 1: failing tests** — valid insert returns the row and the right total; `1d20+5` with
  `[17]` → `total == 22`; bad expression `"; DROP TABLE"` → `ValueError`; 60 results for `1d20` →
  `ValueError`; inserting 505 rolls leaves exactly 500 rows, newest kept.

- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** `insert_roll` + `POST /api/campaign/{id}/roll` (ownership check copied
  from the neighbouring campaign routes) + `GET /api/campaign/{id}/rolls?limit=50` (clamped 1..500,
  newest first). The POST publishes `{"kind": "roll", **row}` **after** `db.commit()`.
- [ ] **Step 4: run, expect PASS**; then `pyflakes routes/table.py main.py | grep "undefined name"`.
- [ ] **Step 5: commit** — `feat: campaign roll write path and history endpoint`

---

### Task 4: DM SSE stream

**Files:**
- Modify: `routes/table.py`
- Test: `tests/test_table_channel.py`

**Interfaces:**
- Produces: `GET /api/campaign/{id}/stream` → `text/event-stream`, `StreamingResponse`.
- Event shape: `id: <roll_log.id>`, `event: roll`, `data: <json row>`; plus `: hb` comment every 20 s.
- Consumes: `services.events.subscribe/unsubscribe`, `roll_log`.

- [ ] **Step 1: failing tests** — (a) a generator harness drives `stream_events(db, campaign_id,
  last_id)` directly: with `last_id=0` it yields rows in id order; with `last_id=<n>` it yields only
  ids `> n`; (b) an admin request for another DM's campaign returns 404; (c) the response headers
  carry `content-type: text/event-stream` and `cache-control: no-cache, no-transform`.
  Do NOT stream through `TestClient` for ordering — the generator is called directly, which is what
  makes the ordering claim true. (Live streaming is proven in Task 9.)
- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** `stream_events(...)` (replay from `roll_log` by `Last-Event-ID`, then
  live queue, heartbeat via `asyncio.wait_for(q.get(), 20)`) and the route
  (`X-Accel-Buffering: no`, `Cache-Control: no-cache, no-transform`).
- [ ] **Step 4: run, expect PASS**; restart and `curl` the endpoint locally to confirm it streams.
- [ ] **Step 5: commit** — `feat: DM SSE stream for campaign rolls`

---

### Task 5: Player key, player stream, player roll

**Files:**
- Modify: `routes/table.py`
- Test: `tests/test_table_channel.py`

**Interfaces:**
- Produces: `GET|POST /api/campaign/{id}/player-key` (mirrors `dm_map_player_key`: create on first
  ask, rotate, blank = revoke), `campaign_by_key(db, key) -> dict | None` (constant-time compare),
  `GET /api/table/{key}/stream` (player projection), `POST /api/table/{key}/roll`,
  `GET /table/{key}` (Task 7 renders it).

- [ ] **Step 1: failing tests** — key of campaign A cannot read/post campaign B's stream (404);
  hidden roll reaches the DM stream and NEVER the key stream; a player roll naming a character not
  in `dm_campaigns.characters` is rejected 400 (note: the join table `dm_campaign_characters` is
  EMPTY on the live DB — roster membership must be read from the campaign's `characters` JSON);
  a player roll forces `who='player'` even if the payload says `'dm'`; revoking the key (blank)
  makes the stream 404 on its next heartbeat check.
- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** all five routes + `campaign_by_key`.
- [ ] **Step 4: run, expect PASS** + pyflakes sweep.
- [ ] **Step 5: commit** — `feat: player key, filtered stream and player rolls`

---

### Task 6: The dice sink (the one hook)

**Files:**
- Modify: `static/dice.js` (sink only — no behaviour change when no sink is installed)
- Create: `static/rollfeed.js`, `static/rollfeed.css`
- Modify: `templates/layout.html` (two tags, `static_asset_version()`)
- Test: `tests/test_dice_roller.py` (extend) + `tests/test_roll_feed_js.py` (new, node)

**Interfaces:**
- `DiceRoller.setSink(fn)`; the sink is called as `fn(result, meta)` from **inside `roll()`**,
  because `enhance()`'s click handler calls the closure-local `roll` and would bypass any patch on
  the exported object (found 2026-10-12 — the obvious hook is a silent no-op).
- `result` = `{expr, rolls, mod, total, text}` (existing contract). A throwing sink must never
  break a roll: the call is wrapped in `try {} catch (e) {}`.
- Produces: `window.RollFeed = {init(cfg), post(expr, opts), render(rows), feedRows(), onEvent(ev)}`
  where `cfg = {streamUrl, rollUrl, campaignId, characterId, canRoll, hiddenAllowed}`.

- [ ] **Step 1: failing tests** — node: `setSink(fn)` then `roll('2d6+3')` calls `fn` once with
  `total === rolls[0]+rolls[1]+3`; a sink that throws still returns the roll result; with no sink
  installed `roll()` behaves as before (regression guard on the existing contract).
  Text-level guards in `tests/test_roll_feed_js.py`: `rollfeed.js` parses `data:` lines, filters
  nothing client-side that the server filters, and never assigns `innerHTML` from an event payload.
- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** the sink in `dice.js`; `rollfeed.js` (EventSource with
  `Last-Event-ID` from the last rendered id, reconnect backoff, `post()` → `fetch` with CSRF header
  like the other assets, DOM built with `textContent`); `rollfeed.css`; two `<link>/<script>` tags
  in `layout.html`.
- [ ] **Step 4: run, expect PASS** + `node --check static/rollfeed.js static/dice.js`
- [ ] **Step 5: commit** — `feat: dice sink and roll feed asset`

---

### Task 7: DM tools feed panel + player surface

**Files:**
- Modify: `templates/dm_tools.html` (panel markup), `static/dm_tools.js` (mount + campaign select only)
- Create: `templates/table.html`, `routes/table.py` (the `/table/{key}` page)
- Test: `tests/test_table_channel.py`, plus the UI conventions in `references/ui-conventions.md`

**Interfaces:**
- Consumes: `window.RollFeed.init(cfg)`; `window.TABLE_CFG` set by the template (same shape as
  `map_player.html`'s `PLAYER_CFG`).

- [ ] **Step 1: failing tests** — `/table/{key}` renders for a valid key, 404 for a bad one; the DM
  tools page contains the feed panel mount point; the player page exposes a roster picker built from
  the campaign's characters (names escaped).
- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** — DM panel: one slim non-wrapping bar (campaign select, hide-toggle,
  clear) + collapsible body, per the UI conventions; player page: chromeless like `map_player.html`,
  roster picker remembered in `localStorage`, roll pad (d4–d100, adv/dis, modifier, label, hidden
  never offered to players).
- [ ] **Step 4: run, expect PASS**; restart, then prove the loaded `?v=` equals
  `sha1sum static/rollfeed.js | cut -c1-12`.
- [ ] **Step 5: commit** — `feat: DM roll feed panel and player table surface`

---

### Task 8: Sheet feed, then retire the polls

**Files:**
- Modify: `templates/sheet.html` (`{% block scripts %}`), `static/sheet.js` (feed mount, poll demotion)
- Modify: `static/vtt.js` (player poll demotion)
- Test: `tests/test_table_channel.py` (text guards on the poll demotion, per the repo's static-asset
  test convention)

- [ ] **Step 1: failing tests** — sheet exposes `canRoll: false` on its feed mount; both polls only
  start when `EventSource` is unavailable or closed (guard on the source text).
- [ ] **Step 2: run, expect FAIL**
- [ ] **Step 3: implement** — sheet feed (read-only, campaign-scoped via `dm_campaigns.characters`);
  demote the 2 s and 3 s intervals to a 30 s safety net that runs only when the stream is closed.
- [ ] **Step 4: run, expect PASS**
- [ ] **Step 5: commit** — `feat: sheet roll feed; polls become the stream's fallback`

---

### Task 9: Live verification, docs, final suite

- [ ] **Step 1:** second instance on a copied `data/` (port 8310) + real HTTP:
  `curl -N -H 'User-Agent: Mozilla/5.0'` the player stream, post a DM roll and a player roll,
  assert arrival order and that the hidden roll never appears on the player stream, then revoke the
  key and confirm the stream ends.
- [ ] **Step 2:** deployed service: mint a key through the real endpoint, drive the feed in a real
  browser, screenshot the panel and the player page.
- [ ] **Step 3:** route + flow sweep on a temp `data/` copy (`references/route_sweep.md`).
- [ ] **Step 4:** backgrounded full suite, unpiped, read the true exit status.
- [ ] **Step 5:** clean up test users/sessions created by probes (see Pitfall 4/61).
- [ ] **Step 6:** docs — add the pitfalls learned (edge requires a browser UA; the enhance()-bypasses-
  exported-hook trap; single-process bus assumption) to `references/pitfalls_verification.md` and
  `references/pitfalls_frontend.md`, index them in SKILL.md, add
  `references/table-channel.md`, and commit.
