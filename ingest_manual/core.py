from __future__ import annotations
from pathlib import Path
import sys


# ── Tee logger (defined early, instantiated after HERE) ───────────────────────
# Hermes's process manager uses pipes which do full buffering at the C level
# regardless of Python's buffering mode. We tee output to a log file so progress
# is always visible via `tail -f`. The log is overwritten on each run.
_LOG_PATH: Path | None = None  # Set after HERE is defined


class _TeeLogger:
    """Write to both stdout and a log file, flushing every line."""

    def __init__(self, log_path: Path):
        self._stdout = sys.stdout
        self._log = open(str(log_path), 'w', buffering=1)  # line-buffered

    def write(self, data: str) -> int:
        self._stdout.write(data)
        self._log.write(data)
        if data and '\n' in data:
            self._stdout.flush()
            self._log.flush()
        return len(data)

    def flush(self) -> None:
        self._stdout.flush()
        self._log.flush()

    def fileno(self) -> int:
        return self._stdout.fileno()

    def close(self) -> None:
        self._log.close()

# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

HERE = Path(__file__).resolve().parents[1]
MANUALS_DIR = (HERE / "manuals").resolve()
CACHE_DIR = HERE / "data" / "manual_cache"
OUTPUT_DIR = HERE / "data" / "manual_data"

# Activate tee logger now that HERE is known
_LOG_PATH = HERE / 'data' / 'ingestion.log'
sys.stdout = _TeeLogger(_LOG_PATH)

CHUNK_CHARS = 8000          # ~2000 tokens per chunk
CHUNK_OVERLAP = 400         # overlap between chunks for context
LLM_TIMEOUT = 60            # seconds per LLM call

# Known valid values for post-extraction validation
VALID_ABILITIES = ["strength", "str", "dexterity", "dex", "constitution", "con",
                   "intelligence", "int", "wisdom", "wis", "charisma", "cha"]
VALID_SPELL_SCHOOLS = ["abjuration", "conjuration", "divination", "enchantment",
                       "evocation", "illusion", "necromancy", "transmutation"]
VALID_RARITIES = ["common", "uncommon", "rare", "very rare", "legendary", "artifact", "varies"]
VALID_SIZES = ["Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan"]
VALID_SKILLS = ["Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception",
                "History", "Insight", "Intimidation", "Investigation", "Medicine",
                "Nature", "Perception", "Performance", "Persuasion", "Religion",
                "Sleight of Hand", "Stealth", "Survival"]

# Books we should skip for extraction (already in base data via SRD or hardcoded)
# Still cached for search, just skip LLM extraction
SKIP_EXTRACTION: set[str] = set()  # All manuals processed — nothing skipped
