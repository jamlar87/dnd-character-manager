# Table Channel — shared roll feed + live push (design)

> Spec for the feature chosen on 2026-10-12. Implementation plan: same directory,
> `2026-10-12_table-channel-plan.md`. Related umbrella plan:
> `2026-09-29_100000-map-and-table-layer.md` (this is the table-side half that plan
> listed as "nice-to-have" — it never covered rolls or a push channel).

**Goal:** the table plays off one live feed — every roll made anywhere in the app appears
on the DM's screen and on each player's device within ~100 ms, and player devices can roll
their own dice into that feed without gaining any authority over game state.

**Status:** design approved by James 2026-10-12. Decisions below are his answers, not
defaults I picked.

---

## Why this shape — the probe that decided it

Before designing, the open question was whether a push channel survives the Cloudflare
tunnel, because a buffered edge turns "live" into "a burst at the end" and the whole
feature would need a different design.

Measured on the deployed service (temporary `text/event-stream` route on `main.py`,
removed afterwards; working tree verified clean):

| Probe | Result |
|---|---|
| 6 events @700 ms, public URL | first byte 0.189 s, events arrived **+0.70 s apart** — streamed, not buffered |
| 150 s soak, tick every 5 s | **31/31 events, zero drops**, no reconnect |
| Response headers | `content-type: text/event-stream`, `content-encoding: identity`, `cf-cache-status: DYNAMIC` |
| Same path with a non-browser User-Agent | **403** (Cloudflare/edge rejects it) — probes must send a browser UA |

Conclusions that the design depends on:
- SSE through the tunnel works with no compression to defeat; a `: hb` comment heartbeat
  every 20 s is still worth sending (cheap insurance, and it keeps intermediaries awake).
- The app is a **single uvicorn process** (verified: one pid, no reloader child), so an
  in-process fan-out reaches every subscriber. This is a load-bearing assumption — see
  Risks.
- Any scripted probe must set `User-Agent: Mozilla/5.0` or it gets a 403 from the edge.

## Decisions (James, 2026-10-12)

1. **Player capability = read + roll.** A player device sees live state and rolls dice into
   the shared feed. All game-state authority stays with the DM. Nothing else is writable
   from the player path.
2. **Identity = the campaign key**, reusing the existing key-auth contract (`secrets.token_hex(16)`
   + `secrets.compare_digest`, rotate or blank-to-revoke, exactly like `dm_maps.player_key`).
   The device then picks its character from that campaign's roster. Trust-based, zero
   friction, matching the existing TV view — accepted for a home table.
3. **Hidden rolls:** any roll may be marked hidden. It is still written to the log, and is
   withheld from player streams **server-side**. Default is visible.
4. **Retention:** the newest **500 rolls per campaign**; older rows pruned on insert.

## Architecture

One write path, one durable log, one push path.

```
 sheet roll ─┐
 DM tools roll ─┤→ POST roll → roll_log (SQLite)  → services/events.publish(campaign)
 player roll ─┘        (prune to 500)                    │
                                                          ├→ GET /api/campaign/{id}/stream  (DM session)
                                                          └→ GET /api/table/{key}/stream    (key auth, filtered)
```

- **`roll_log` is the source of truth.** SSE is only the fast path; a client that reconnects
  with `Last-Event-ID` replays the gap from the table, and a fresh page load reads
  `GET /api/campaign/{id}/rolls`. Nothing depends on having been connected.
- **`services/events.py`** holds one subscriber set per campaign of bounded `asyncio.Queue`s.
  Publish is non-blocking; a full queue means a stalled client, which is dropped and told to
  resync rather than allowed to block the roller.
- **The emitter publishes after the DB commit**, never before — a subscriber must never see
  a roll that a crash could have lost.
- Existing polls stay as the fallback where the stream cannot connect (`EventSource` failure,
  or an old cached page): the sheet's 2 s `syncSheetHP` and the player view's 3 s poll are
  retired only after the stream proves itself in the real browser.

## Data model

New table (via `init_db()` in `services/db_schema.py`, `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS roll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    character_id INTEGER,
    character_name TEXT DEFAULT '',
    who TEXT NOT NULL DEFAULT 'player',      -- 'dm' | 'player'
    source TEXT DEFAULT '',                  -- 'sheet' | 'dm-tools' | 'player-view'
    label TEXT DEFAULT '',                   -- e.g. "Longsword attack", "Dex save"
    expression TEXT NOT NULL,                -- e.g. "1d20+5"
    results TEXT NOT NULL DEFAULT '[]',      -- JSON: individual dice, kept for display
    total INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_roll_log_campaign ON roll_log(campaign_id, id DESC);
```

New column on `dm_campaigns` (same `try: ALTER TABLE ... except OperationalError: pass`
pattern already used for added columns):

- `player_key TEXT DEFAULT ''` — the campaign-level share key; generated on first ask,
  rotatable, blank means revoked. Gives the player surface `/table/{key}`.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/campaign/{id}/roll` | DM session + ownership | DM roll (any expression), `hidden` flag, label, campaign-scoped |
| GET | `/api/campaign/{id}/rolls?limit=` | DM session + ownership | history for a page load or resync |
| GET | `/api/campaign/{id}/stream` | DM session + ownership | SSE: rolls + state pokes for that campaign |
| GET | `/api/campaign/{id}/player-key` | DM session + ownership | read/create the share key (mirrors `dm_map_player_key`) |
| POST | `/api/campaign/{id}/player-key` | DM session + ownership | rotate, or blank to revoke |
| GET | `/api/table/{key}/stream` | key (`compare_digest`) | SSE for players: hidden rolls and DM-only fields filtered |
| POST | `/api/table/{key}/roll` | key | player roll; `character_id` must be on that campaign's roster |
| GET | `/table/{key}` | key | the player page: live map link + roster picker + roll pad + feed |

Payloads, both roll endpoints:

```json
{"expression": "1d20+5", "label": "Longsword attack", "hidden": false,
 "character_id": 12, "source": "player-view", "results": [17], "total": 22}
```

The client sends raw dice it already rolled (`static/dice.js` `DiceRoller.roll()`), and the
server **re-validates** `expression` against a dice grammar and recomputes nothing else: a
client may not post a `total` that disagrees with its own `results`, and an expression that
fails validation is rejected rather than stored. `total` computed server-side from
`results` + the expression's flat modifier.

## Surfaces

- **One hook, not per-button edits.** `static/dice.js` already exposes
  `window.DiceRoller = {parse, roll, enhance}` and `enhance` turns any die expression into a
  clickable roll. A new `static/rollfeed.js` wraps `DiceRoller.roll()` so every existing roll
  surface (sheet action rows, DM tools stat blocks, spell cards) publishes without touching
  those call sites.
- **DM tools**: a new collapsible **Roll feed** panel — one slim bar (campaign select, hide
  toggle, clear) plus the feed body, per `references/ui-conventions.md`. Its own versioned
  asset (`static/rollfeed.js` + `static/rollfeed.css`) loaded from `layout.html` via
  `static_asset_version()`, never inline into the 8.3k-line `dm_tools.js`.
- **Sheet**: the same feed, scoped to the campaign the character is in (membership comes from
  `dm_campaigns.characters` JSON — the `dm_campaign_characters` join table is empty on the
  live DB), rendered read-only.
- **`/table/{key}`**: the player surface. Reuses the player-view chrome conventions, adds a
  roster picker (persisted per device) and a roll pad (d4…d100, advantage/disadvantage,
  modifier, label) that posts to `/api/table/{key}/roll` and shows the feed.
- **Polls retire last.** `syncSheetHP`'s 2 s interval and `map_player`'s 3 s interval stay
  until the stream is verified in a browser on the deployed service; then they drop to a 30 s
  safety net and only run when `EventSource.readyState === CLOSED`.

## Security

- Player path can write **rolls only** — no character, token, HP or map writes.
- `character_id` on a player roll is validated against the campaign roster, so a device cannot
  roll "as" a character outside that campaign, or as the DM.
- Hidden rolls are filtered **in the server projection** for key-authenticated streams; the
  client is never sent the row.
- A revoked/rotated key kills live streams on the next heartbeat (the stream re-checks the
  key against the row, not just at connect).
- Per-key roll rate limit (token bucket, 20 rolls / 10 s) so a stuck client or a script
  cannot fill the log or the fan-out.
- Roll expressions are validated against a strict grammar (`NdM[+/-K]`, N ≤ 100, M ∈ {2,4,6,8,10,12,20,100});
  rejected with 400, never stored, never echoed into HTML unescaped.
- The `roll_log` row is campaign-scoped: `/api/campaign/{id}/rolls` and the stream both go
  through the same ownership check as the rest of `/api/dm/campaign/*`.

## Verification (per the repo skill)

1. **Unit / pytest** (`tests/test_table_channel.py`, `tests/test_roll_feed_js.py`):
   - bus: subscribe → publish → receive; publish with no subscribers is a no-op; a full queue
     drops that subscriber instead of blocking the publisher.
   - replay: subscriber connecting with `Last-Event-ID: <id>` receives exactly the rows after
     that id, in order.
   - hidden: a DM roll marked hidden appears in the DM stream and never in the key stream.
   - scope: an unrelated logged-in user gets 404 on another DM's campaign stream/rolls; a
     player key for campaign A cannot read or post to campaign B.
   - roster: a player roll naming a character not on the roster is rejected (not silently
     attributed).
   - prune: inserting past 500 leaves exactly 500, newest kept.
   - node test for the JS hook (same pattern as `tests/test_dice_roller.py`):
     `require('static/rollfeed.js')` with `global.window`/`global.document` stubs, assert
     that a roll produces the right payload — a text grep cannot prove a roll total.
2. **Route + flow sweep** on a temp copy of `data/` (`references/route_sweep.md`) — the
   UnboundLocalError class lives in exactly this kind of loop-scoped new code.
3. **pyflakes sweep** (`undefined name`), plus `tests/test_name_and_scope_regressions.py`.
4. **Live, on the deployed service** (the only proof that matters): mint a campaign key
   through the real endpoint, `curl -N -H 'User-Agent: Mozilla/5.0'` the player stream,
   post a roll as the DM and as a player, assert arrival order, assert a hidden roll never
   appears on the player stream, then revoke the key and confirm the stream dies.
5. **Rendered DOM** check in the browser: feed shows the roll, `?v=` on every touched asset
   equals `sha1sum static/<file> | cut -c1-12`, and the retired polls do not double-render.
6. Full suite (`pytest -q`) alone, backgrounded, unpiped — read the real exit status.

## Slices

1. **Backend + DM stream**: `services/events.py`, schema, roll endpoints, DM SSE, DM feed panel.
2. **Player path**: campaign player key, `/api/table/{key}/*`, `/table/{key}` surface + roll pad.
3. **Retire the polls** and fold the sheet feed in, once the stream is verified in a browser.

Each slice ends green (tests + pyflakes + live check) and committed on its own.

## Risks / open questions

- **Single-process assumption.** The in-process fan-out breaks silently if the service ever
  runs multiple workers or a reloader parent — half the clients would simply never receive
  events, with no error anywhere. Mitigation: `services/events.py` raises at publish time if
  it detects more than one serving process is not knowable from inside; instead the *tests*
  pin the single-process expectation, and the README/skill note says "if you add
  `--workers`, move the bus to the DB or Redis". Replay keeps correctness regardless —
  only latency would regress.
- **Cloudflare 100 s idle timeouts** on other plans/edges: the 20 s heartbeat covers it; the
  probe showed 150 s survival on this zone.
- **SQLite write contention** with a busy table: rolls are single small INSERTs with WAL on;
  the prune runs in the same transaction. If the DM rolls 20 dice/sec this is still fine;
  the rate limiter caps the pathological case.
- **Two DMs / shared campaigns**: the feed is per campaign row, so a campaign shared with a
  second DM shows the same feed to both. Acceptable; no per-DM channel.
