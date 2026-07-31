# D&D Character Manager — Operations Guide

## Service

- **Systemd unit:** `dnd-character-manager.service` (system-level, port 8300)
- **Status:** `systemctl status dnd-character-manager`
- **Restart:** `sudo systemctl restart dnd-character-manager`
- **Logs:** `journalctl -u dnd-character-manager -n 200 -f`
- **Health check:** `curl -s -o /dev/null -w "%{http_code}" http://localhost:8300/` → `200`
- The app serves at `http://localhost:8300` (Cloudflare `*.jamlarnet.stream` front).

> **Note:** there is also a duplicate **user** unit `dnd-char-manager.service` that is
> **stopped + disabled** — do not re-enable it. Both units historically ran
> `ExecStartPre=fuser -k 8300/tcp` + `Restart=always`, which caused a mutual
> SIGKILL loop. The system unit is canonical.

## Data locations

| What | Path |
|---|---|
| SQLite DB | `data/characters.db` (override: `DND_DATA_DIR`) |
| SRD cache | `data/srd_cache/*.json` |
| Manual data | `data/manual_data/*.json` |
| PDF template | `/media/james/SlowDisk1tb/home-move/DnD-Manuals/5E_CharacterSheet_Fillable.pdf` |
| Campaign Expert path | `DND_CAMPAIGN_EXPERT_PATH` (defaults to sibling `../dnd-campaign-expert`) |

## Backup / restore

Backup (stop-free; SQLite WAL-safe):

```bash
DB=data/characters.db
TS=$(date +%Y%m%d%H%M%S)
cp "$DB" "$DB.pre-remediation.$TS"
# WAL + shared-memory files if present
cp "$DB-wal" "$DB-wal.pre-remediation.$TS" 2>/dev/null || true
cp "$DB-shm" "$DB-shm.pre-remediation.$TS" 2>/dev/null || true
```

Restore:

```bash
sudo systemctl stop dnd-character-manager
DB=data/characters.db
cp "$DB.pre-remediation.TIMESTAMP" "$DB"
sudo systemctl start dnd-character-manager
```

Verify after restore:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8300/   # 200
journalctl -u dnd-character-manager -n 50 | tail                # no errors
```

## Startup timing

`load_manual_data()` logs its elapsed time at import:

```
[timing] load_manual_data: 92ms
```

To profile full app startup:

```bash
time python -c "import main"
```

## Clean-environment checks

```bash
# Compile check (catches syntax errors without importing)
python -m compileall -q main.py data.py routes pdf_generator.py

# Full test suite (needs the venv)
.venv/bin/python -m pytest -q --disable-warnings --maxfail=1
```

## Tests

```bash
# Full suite
.venv/bin/python -m pytest -q --disable-warnings --maxfail=1

# Focused sets
.venv/bin/python -m pytest tests/test_progression_matrix.py -q
.venv/bin/python -m pytest tests/test_pdf_generation.py -q
.venv/bin/python -m pytest tests/test_template_rendering.py -q
```

## Config env vars

| Var | Default | Purpose |
|---|---|---|
| `DND_DATA_DIR` | `<repo>/data` | Data directory (DB, caches, manual data) |
| `DND_CAMPAIGN_EXPERT_PATH` | `<repo>/../dnd-campaign-expert` | Campaign Expert PDFs/manuals |
| `DND_RELOAD` | unset | Set non-zero for uvicorn auto-reload (dev only) |
