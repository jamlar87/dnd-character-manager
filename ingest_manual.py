#!/usr/bin/env python3
"""D&D Manual Ingestion Engine — extract structured data from reference PDFs.

Usage:
  python3 ingest_manual.py --list          List manuals and extraction status
  python3 ingest_manual.py "Volo's Guide"  Deep-sweep a specific manual
  python3 ingest_manual.py --all           Process all un-extracted manuals
  python3 ingest_manual.py --merge         Rebuild merged data from extractions

For each manual this:
  1. Extracts full text via pdftotext (cached)
  2. Chunks into LLM-friendly sections (~8000 chars)
  3. Sends each chunk to Gemini/Ollama to extract ALL data types at once
  4. Validates extracted fields against known D&D 5e values
  5. Saves per-manual structured JSON
  6. Merges all manuals' data into app-loadable JSON files

Data extracted: races, subraces, racial traits, spells, magic items,
  equipment, monsters, NPCs, feats, backgrounds, subclasses.
"""
from __future__ import annotations

import json, os, re, sys, time, hashlib, urllib.request, urllib.error
from pathlib import Path
from typing import Any

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

HERE = Path(__file__).parent
MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
CACHE_DIR = HERE / "data" / "manual_cache"
OUTPUT_DIR = HERE / "data" / "manual_data"
STATE_FILE = HERE / 'data' / 'ingest_state.json'

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
SKIP_EXTRACTION = {"PHB", "MM"}  # SRD covers these (DMG run manually)

# Telegram notification (loaded from ~/.hermes/.env if not already exported)
def _load_telegram_env():
    """Load TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env or ~/.hermes/.env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return token, chat_id
    # Fallback: parse from Hermes .env file
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "TELEGRAM_BOT_TOKEN" and not token:
                token = val
            elif key == "TELEGRAM_CHAT_ID" and not chat_id:
                chat_id = val
    return token, chat_id

TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = _load_telegram_env()

# ═══════════════════════════════════════════════════════════════════════════════
# Telegram notification
# ═══════════════════════════════════════════════════════════════════════════════

def _notify_telegram(text: str):
    """Send a Telegram message via Bot API. Logs failure but never blocks extraction."""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  (Telegram notify: not configured, skipping)")
        return
    # Trim to Telegram's 4096 char limit
    if len(text) > 4000:
        text = text[:4000] + "\n...\n(truncated)"
    try:
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if not result.get("ok"):
            # If HTML parse fails, retry without parse_mode
            if "can't parse entities" in str(result).lower():
                data2 = json.dumps({
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                }).encode()
                req2 = urllib.request.Request(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data=data2,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req2, timeout=10)
    except Exception as e:
        print(f"  (Telegram notify failed: {e})")


def _send_readout(text: str):
    """Print to stdout AND send to Telegram if configured."""
    print(text)
    # Strip box-drawing chars for Telegram (they render poorly)
    clean = text.replace("┌─", "──").replace("─┐", "──").replace("├─", "──") \
                .replace("─┤", "──").replace("└─", "──").replace("─┘", "──") \
                .replace("│", " ").replace("⚠", "⚠️")
    _notify_telegram(clean)

# ═══════════════════════════════════════════════════════════════════════════════
# LLM Callers (sync, urllib — no httpx dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_deepseek_key() -> str:
    """Load DeepSeek API key from env or ~/.hermes/.env."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # Fallback: parse from Hermes .env file
    env_file = Path(os.path.expanduser("~/.hermes/.env"))
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _call_deepseek(prompt: str, model: str = "deepseek-chat") -> str | None:
    """Call DeepSeek API (V4 Pro / Chat)."""
    key = _load_deepseek_key()
    if not key:
        return None
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.1,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [DeepSeek] {e}")
        return None


def _call_gemini(prompt: str) -> str | None:
    """Tier 2: Google Gemini 2.0 Flash."""
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    try:
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
            data=body,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
            return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"  [Gemini] {e}")
        return None


def _call_ollama(prompt: str) -> str | None:
    """Tier 3: Local Ollama hermes3:8b."""
    try:
        body = json.dumps({
            "model": "hermes3:8b-llama3.1-q8_0",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096}
        }).encode()
        req = urllib.request.Request(
            "http://192.168.1.31:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
            return result.get("response", "")
    except Exception as e:
        print(f"  [Ollama] {e}")
        return None


def _call_llm(prompt: str) -> str | None:
    """Tiered LLM chain: DeepSeek V4 Pro → Gemini → Ollama."""
    result = _call_deepseek(prompt, model="deepseek-chat")
    if result:
        return result
    print("  DeepSeek failed, trying Gemini...")
    result = _call_gemini(prompt)
    if result:
        return result
    print("  Gemini failed, trying Ollama...")
    return _call_ollama(prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# JSON helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response, stripping markdown wrappers."""
    if not text:
        return None
    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        # Find the first code block
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside code block
                if part.startswith("json"):
                    part = part[4:]
                try:
                    return json.loads(part.strip())
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None


def _load_json(path: Path) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_json_list(path: Path) -> list:
    """Load a JSON array, returning [] on failure."""
    data = _load_json(path)
    return data if isinstance(data, list) else []


def _save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# PDF Discovery
# ═══════════════════════════════════════════════════════════════════════════════

def discover_manuals() -> list[dict]:
    """Walk the manuals directory and return unique PDFs with metadata."""
    if not MANUALS_DIR.exists():
        print(f"ERROR: Manuals dir not found: {MANUALS_DIR}")
        return []

    seen = set()
    manuals = []
    for f in sorted(MANUALS_DIR.rglob("*.pdf")):
        title = f.stem.replace("_", " ").replace("  ", " ").strip()
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        # Derive short slug/label
        slug = _derive_slug(title)

        manuals.append({
            "title": title,
            "filename": f.name,
            "path": str(f.relative_to(MANUALS_DIR)),
            "abs_path": str(f),
            "slug": slug,
            "size_kb": f.stat().st_size // 1024,
            "mtime": f.stat().st_mtime,
        })

    return manuals


def _derive_slug(title: str) -> str:
    """Derive a short label/slug from a manual title."""
    title_lower = title.lower()
    for kw, slug in [
        ("player's handbook", "PHB"),
        ("dungeon master's guide", "DMG"),
        ("monster manual", "MM"),
        ("xanathar's guide", "XGE"),
        ("volo's guide", "VGM"),
        ("mordenkainen's tome", "MTF"),
        ("sword coast", "SCAG"),
        ("elemental evil", "EEPC"),
        ("guildmasters' guide", "GGR"),
        ("wayfinders guide", "WGE"),
        ("tortle package", "TTP"),
        ("lost mine", "LMoP"),
        ("tomb of annihilation", "ToA"),
        ("dragon heist", "WDH"),
        ("hoard of the dragon queen", "HotDQ"),
        ("rise of tiamat", "RoT"),
        ("wild sheep", "WSC"),
        ("ancestral weapon", "AW"),
    ]:
        if kw in title_lower:
            return slug
    # Fallback: first letters
    return "".join(w[0] for w in title.split() if w[0].isalpha())[:4].upper()


# ═══════════════════════════════════════════════════════════════════════════════
# Text Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text(manual: dict) -> str | None:
    """Extract text from a PDF manual. Cached to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{manual['slug']}.txt"

    pdf_path = Path(manual["abs_path"])
    if not pdf_path.exists():
        print(f"  ERROR: PDF not found: {pdf_path}")
        return None

    # Use cache if valid AND has page markers (pdftotext caches lack markers)
    if cache_path.exists():
        pdf_mtime = pdf_path.stat().st_mtime
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= pdf_mtime:
            text = cache_path.read_text(encoding="utf-8", errors="replace")
            if "--- PAGE " in text:
                print(f"  Text cached ({len(text):,} chars)")
                return text
            else:
                print(f"  Cache has no page markers (old pdftotext cache) — re-extracting with pymupdf")

    print(f"  Extracting text...")
    text = _extract_pymupdf(str(pdf_path), str(cache_path))
    if not text:
        text = _extract_pdftotext(str(pdf_path), str(cache_path))
    if not text:
        return None
    return text


def _extract_pymupdf(pdf_path: str, cache_path: str) -> str | None:
    """Try pymupdf (fitz) for better multi-column text extraction.
    Injects page markers for chapter detection."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages = []
        for i, page in enumerate(doc):
            page_text = page.get_text("text").strip()
            if page_text:
                pages.append(f"--- PAGE {i + 1} ---\n{page_text}")
        doc.close()
        text = "\n\n".join(pages)
        if len(text) > 500:
            Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
            print(f"  Extracted {len(text):,} chars ({len(pages)} pages) via pymupdf → {Path(cache_path).name}")
            return text
    except ImportError:
        pass
    except Exception as e:
        print(f"  pymupdf error: {e}")
    return None


def _extract_pdftotext(pdf_path: str, cache_path: str) -> str | None:
    """Fallback: pdftotext extraction."""
    print(f"  Extracting text via pdftotext...")
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, cache_path],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            print(f"  pdftotext error: {result.stderr[:200]}")
            return None
        text = Path(cache_path).read_text(encoding="utf-8", errors="replace")
        print(f"  Extracted {len(text):,} chars → {Path(cache_path).name}")
        return text
    except FileNotFoundError:
        print("  ERROR: pdftotext not installed. Install poppler-utils.")
        return None
    except subprocess.TimeoutExpired:
        print("  ERROR: pdftotext timed out after 3 min")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Chunking
# ═══════════════════════════════════════════════════════════════════════════════

def chunk_text(text: str, chunk_size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Split text into overlapping chunks on natural boundaries.

    Returns [{index, offset, length, text}].
    """
    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{3,}', '  ', text)

    # Split on paragraph breaks
    paragraphs = re.split(r'\n\s*\n', text)
    
    chunks = []
    current = ""
    offset = 0
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 > chunk_size and current:
            # Save current chunk
            chunks.append({
                "index": idx,
                "offset": offset,
                "length": len(current),
                "text": current.strip(),
            })
            # Start new chunk with overlap from end of previous
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + "\n\n" + para
            offset += len(current) - len(para) - 2
            idx += 1
        else:
            if current:
                current += "\n\n" + para
            else:
                current = para

    # Don't forget the last chunk
    if current.strip():
        chunks.append({
            "index": idx,
            "offset": offset,
            "length": len(current),
            "text": current.strip(),
        })

    # Merge tiny final chunks
    if len(chunks) >= 2 and len(chunks[-1]["text"]) < 500:
        chunks[-2]["text"] += "\n\n" + chunks[-1]["text"]
        chunks[-2]["length"] += chunks[-1]["length"]
        chunks.pop()

    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Extraction
# ═══════════════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """You are a D&D 5e rules parser. Below is text from a sourcebook.
Your job: extract EVERY game-mechanical element as structured JSON.
DO NOT summarize. DO NOT skip details. Copy trait/spell/item text VERBATIM.

CRITICAL RULES:
- RACES: You MUST extract EVERY trait with its FULL description text. Include ASI
  (every ability bonus), speed, darkvision, ALL languages, size. A race with empty
  traits[] and empty asi{} is a FAILED extraction — don't return one.
- MONSTERS: Only extract monsters with COMPLETE stat blocks (must have armor_class,
  hit_points, AND at least 3 ability_scores). Skip name-only mentions, skip
  creatures described narratively without stats.
- NPCS: Extract named characters with stat blocks. Skip name-only references.
- SPELLS: Must have level, school, AND description. Skip spell name mentions
  without full descriptions.
- ITEMS: Must have a name AND description. If garbled/truncated, still extract.
- Use VERBATIM text from the source for ALL descriptions. DO NOT paraphrase.
- For source, include page number when determinable.

Return ONLY this JSON (empty arrays where nothing found):

{
  "races": [{
    "name": "Race Name",
    "source": "Book Name or page",
    "asi": {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0},
    "speed": 30,
    "darkvision": 0,
    "languages": ["Common"],
    "size": "Medium",
    "traits": [{"name": "Trait Name", "description": "Full trait text", "uses": 1, "recharge": "long rest"}],
    "subraces": [{"name": "Subrace Name", "asi": {}, "traits": [{"name": "Trait Name", "description": "...", "uses": 1, "recharge": "short rest"}], "description": "..."}],
    "description": "1-2 sentence description"
  }],
  "spells": [{
    "name": "Spell Name",
    "level": 1,
    "school": "evocation",
    "casting_time": "1 action",
    "range": "120 feet",
    "components": "V, S, M (component description)",
    "duration": "Instantaneous",
    "description": "Full spell description",
    "higher_levels": "At Higher Levels text or empty",
    "classes": ["Wizard", "Sorcerer"],
    "ritual": false,
    "concentration": false
  }],
  "magic_items": [{
    "name": "Item Name",
    "type": "Wondrous item",
    "rarity": "rare",
    "requires_attunement": false,
    "description": "Full item description",
    "source": "DMG p.200"
  }],
  "equipment": [{
    "name": "Equipment Name",
    "type": "Weapon",
    "subtype": "martial melee",
    "damage": "1d8 slashing",
    "properties": ["versatile (1d10)"],
    "cost": "15 gp",
    "weight": 3
  }],
  "monsters": [{
    "name": "Monster Name",
    "size": "Medium",
    "type": "humanoid (goblinoid)",
    "alignment": "neutral evil",
    "armor_class": 15,
    "ac_type": "chain shirt",
    "hit_points": "27 (5d8+5)",
    "speed": "30 ft.",
    "ability_scores": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
    "saving_throws": {},
    "skills": {"Stealth": 6},
    "damage_resistances": [],
    "damage_immunities": [],
    "condition_immunities": [],
    "senses": "darkvision 60 ft., passive Perception 9",
    "languages": ["Common", "Goblin"],
    "challenge_rating": "1/4",
    "xp": 50,
    "features": [{"name": "Nimble Escape", "description": "..."}],
    "actions": [{"name": "Scimitar", "description": "Melee Weapon Attack: +4 to hit, reach 5 ft., one target. Hit: 5 (1d6+2) slashing damage."}],
    "reactions": [],
    "legendary_actions": [],
    "source": "MM p.166"
  }],
  "npcs": [{
    "name": "NPC Name",
    "race": "Human",
    "class_name": "Druid",
    "subclass": "",
    "level": 4,
    "alignment": "Lawful Good",
    "armor_class": 15,
    "hit_points": "45 (6d8+12)",
    "speed": "30 ft.",
    "ability_scores": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 11, "cha": 10},
    "saving_throws": {},
    "skills": {},
    "features": [{"name": "...", "description": "..."}],
    "actions": [{"name": "...", "description": "..."}],
    "equipment": [],
    "spellcasting": null,
    "description": "Brief description",
    "role": "Ally",
    "is_enemy": false,
    "xp_reward": 0,
    "source": "Adventure Name p.12"
  }],
  "feats": [{
    "name": "Feat Name",
    "prerequisite": "Prerequisite text or empty",
    "description": "Full feat benefit text",
    "source": "XGE p.74"
  }],
  "backgrounds": [{
    "name": "Background Name",
    "skill_proficiencies": ["Skill1", "Skill2"],
    "tool_proficiencies": ["Tool or empty"],
    "languages": ["Language or empty"],
    "equipment": ["Item 1", "Item 2"],
    "feature": {"name": "Feature Name", "description": "..."},
    "description": "Brief description"
  }],
  "subclasses": [{
    "name": "Subclass Name",
    "class": "Parent Class",
    "description": "Brief description",
    "features": [
      {"name": "Feature Name", "level": 3, "description": "Full feature text verbatim", "uses": 1, "recharge": "long rest"},
      {"name": "Another Feature", "level": 3, "description": "Full feature text verbatim"},
      {"name": "Higher-Level Feature", "level": 7, "description": "Full feature text verbatim"}
    ],
    "source": "XGE p.50"
  }]

LIMITED-USE FIELDS (uses, recharge):
- "uses": how many times per rest this feature can be used (integer). 0 means at-will/unlimited. Omit if not use-limited.
- "recharge": "short rest" or "long rest". Omit for at-will features.
- Applies to race traits AND subclass features. Examples: Breath Weapon → uses:1, recharge:"short rest"; Rage → uses:2, recharge:"long rest"; Darkvision → omit uses/recharge.

CRITICAL — SUBCLASSES:
- Extract EVERY feature at EVERY level. A subclass section lists features at
  multiple levels (1st, 2nd, 3rd, 6th, 10th, 14th, 17th, etc.) — capture ALL.
  The example above shows 3 features at 2 levels as a minimum; real entries
  should have 4–7 features across 4–5 levels.
- If a feature references a spell or table, include that reference verbatim.
- If the subclass has domain/oath/pact spells, extract them as a feature
  named "Death Domain Spells" (or similar) at the level they're gained.
}

Rules:
1. Extract EVERY instance — do NOT summarize or skip any that match the criteria above.
2. Copy descriptions VERBATIM from the source. Paraphrasing = failure.
3. Ability score keys: "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma".
4. Challenge ratings as fractions: "1/4", "1/2", "2", "17".
5. If a monster stat block or race entry spans multiple pages/chunks, extract what IS complete in this chunk.
6. Skip table-of-contents, index entries, page headers — only real game content.
7. If unsure between extracting or skipping: EXTRACT. We filter quality later.
8. SIEGE WEAPONS & VEHICLES (Ballista, Cannon, Airship, etc.): Extract in BOTH categories —
   monsters[] with full stat block (AC, HP, attacks, type: "object"),
   AND equipment[] with type: "Vehicle", subtype: "Siege Equipment".

Text to extract from:
---BEGIN TEXT---
{text}
---END TEXT---

Return ONLY the JSON object (no markdown, no explanation)."""


def extract_from_chunk(chunk: dict, book_slug: str) -> dict:
    """Send a text chunk to the LLM for structured extraction."""
    prompt = EXTRACTION_PROMPT.replace("{text}", chunk["text"])
    print(f"    Chunk {chunk['index']} ({chunk['length']:,} chars) → ", end="", flush=True)

    start = time.time()
    raw = _call_llm(prompt)
    elapsed = time.time() - start

    if not raw:
        print(f"FAILED ({elapsed:.1f}s)")
        return _empty_result()

    result = _extract_json(raw)
    if result is None:
        # Try one more time with a shorter prompt on failure
        print(f"BAD JSON, retrying... ", end="", flush=True)
        raw2 = _call_ollama(prompt[:len(prompt)//2] + prompt[len(prompt)//2:])
        result = _extract_json(raw2) if raw2 else None

    if result:
        counts = {k: len(v) for k, v in result.items() if isinstance(v, list) and v}
        total = sum(counts.values())
        print(f"{total} items {counts} ({elapsed:.1f}s)")
    else:
        print(f"NO JSON ({elapsed:.1f}s)")

    return result if result else _empty_result()


# ═══════════════════════════════════════════════════════════════════════════════
# Race Second-Pass Extraction
# ═══════════════════════════════════════════════════════════════════════════════

RACE_DETAIL_PROMPT = """You are extracting COMPLETE mechanical details for a D&D 5e race.
Below is text from a sourcebook containing the "{race_name}" race entry.
Extract EVERY mechanical detail — this is the ONLY race you need to focus on.

Return ONLY this JSON:
{{
  "name": "{race_name}",
  "asi": {{"strength": 0, "dexterity": 0, "constitution": 0, "intelligence": 0, "wisdom": 0, "charisma": 0}},
  "speed": 30,
  "darkvision": 0,
  "size": "Medium",
  "languages": ["Common"],
  "traits": [
    {{
      "name": "Trait Name",
      "description": "FULL verbatim description text from the source"
    }}
  ],
  "subraces": [],
  "description": "1-2 sentence physical description of the race"
}}

CRITICAL:
- Extract EVERY trait — a D&D race typically has 3-8 traits. Finding only 0-1 means FAILURE.
- Copy trait descriptions VERBATIM. Do not summarize or truncate.
- ASI: find the "Ability Score Increase" section. Even if text is garbled/corrupted,
  look for ability names (Strength, Dexterity, etc.) and nearby numbers.
  Common patterns: "Your X score increases by N" or "+N to X".
  ALWAYS extract ASI — every race has one. Empty asi{{}} = FAILURE.
- Languages: include ALL listed languages, not just Common.
- If the race has natural armor (e.g. "Your base AC is 17"), note it in the relevant trait.
- If the race has natural weapons (e.g. claws dealing 1d4+Str), include them as a trait.
- If the race grants skill proficiencies, include them in the relevant trait description.

Text containing the {race_name} race:
---BEGIN TEXT---
{text}
---END TEXT---

Return ONLY the JSON object (no markdown, no explanation)."""


def _extract_race_details(race_name: str, full_text: str) -> dict | None:
    """Second-pass extraction: find the race entry in the full text and extract
    ALL mechanical details with a focused prompt."""
    # Find race name in text (case-insensitive, whole word)
    pattern = re.compile(r'(.{0,200}' + re.escape(race_name) + r'.{0,200})', re.IGNORECASE | re.DOTALL)
    matches = pattern.findall(full_text)
    if not matches:
        return None

    # Extract a large context window around the first substantial match
    # Find the best match — the one closest to a race stat block pattern
    best_idx = 0
    best_score = 0
    for i, m in enumerate(matches):
        score = 0
        if 'ability score' in m.lower(): score += 10
        if 'speed' in m.lower(): score += 5
        if 'trait' in m.lower(): score += 3
        if score > best_score:
            best_score = score
            best_idx = i

    # Get position of the best match
    match_text = matches[best_idx]
    pos = full_text.find(match_text)
    if pos < 0:
        pos = max(0, full_text.lower().find(race_name.lower()) - 200)

    # Extract a 12000-char window around the match
    start = max(0, pos - 1000)
    end = min(len(full_text), pos + len(match_text) + 10000)
    context = full_text[start:end]

    print(f"    Race second-pass: {race_name} ({len(context):,} chars context) → ", end="", flush=True)

    prompt = RACE_DETAIL_PROMPT.format(race_name=race_name, text=context)
    raw = _call_llm(prompt)

    if not raw:
        print("FAILED")
        return None

    result = _extract_json(raw)
    if result:
        n_traits = len(result.get("traits", []))
        n_asi = sum(1 for v in result.get("asi", {}).values() if v)
        print(f"{n_traits} traits, {n_asi} ASI bonuses")
    else:
        print("NO JSON")
    return result


def _merge_race_details(race: dict, details: dict | None) -> dict:
    """Merge second-pass race details into the race entry. Details override
    the original where they have more data."""
    if not details:
        return race

    # ASI: merge, preferring non-zero values from details
    if details.get("asi"):
        orig_asi = race.get("asi", {})
        for k, v in details["asi"].items():
            if v or k not in orig_asi:
                orig_asi[k] = v
        race["asi"] = orig_asi

    # Traits: replace if details has more
    if details.get("traits") and len(details["traits"]) > len(race.get("traits", [])):
        race["traits"] = details["traits"]

    # Languages: replace if details has more
    if details.get("languages") and len(details["languages"]) > len(race.get("languages", [])):
        race["languages"] = details["languages"]

    # Simple fields: take if non-default and more specific
    for field in ("speed", "darkvision", "size", "description"):
        dv = details.get(field)
        if dv and (field not in race or not race[field] or dv != race[field]):
            if field in ("speed", "darkvision") and isinstance(dv, (int, float)) and dv:
                race[field] = dv
            elif field in ("size", "description") and isinstance(dv, str) and dv:
                race[field] = dv

    return race


# ═══════════════════════════════════════════════════════════════════════════════
# Trait Effects Auto-Wiring
# ═══════════════════════════════════════════════════════════════════════════════

TRAIT_EFFECTS_PROMPT = """Classify the mechanical effects of these D&D 5e racial traits.
Return JSON mapping trait_name → effects object.

Effects object fields (all optional, empty arrays if none):
- armor_profs: ["Light armor", "Medium armor", "Heavy armor", "Shields"]
- weapon_profs: ["Longsword", "Shortsword", ...]
- tool_profs: ["Tinker's tools", "Thieves' tools", ...]
- skill_profs: ["Perception", "Stealth", "Survival", ...]
- damage_resist: ["Poison", "Fire", "Cold", ...]
- damage_immune: []
- condition_immune: ["Charmed", "Frightened", "Sleep", ...]
- speed: null or number (e.g. 35)
- darkvision: null or number (e.g. 120)
- hp_per_level: 0 or 1
- natural_armor: null or {"base_ac": 17, "max_dex": null, "allow_shield": true}
- notes: "any other mechanical effects not captured above"

Rules:
- "You have proficiency with..." → armor_profs, weapon_profs, or tool_profs
- "You gain proficiency in the... skill" → skill_profs
- "You have resistance to... damage" → damage_resist
- "You have advantage on saving throws against being..." → condition_immune if it prevents the condition
- "Your base walking speed increases to X" → speed: X
- "Your darkvision has a radius of X" → darkvision: X
- "Your hit point maximum increases by 1 per level" → hp_per_level: 1
- "Your base AC is X" or "natural armor of X" → natural_armor
- For natural weapons (claws, bite, etc.), put in notes

Traits:
{traits_json}

Return ONLY: {{"Trait Name": {{effects}}, ...}}"""


def _wire_trait_effects(race: dict) -> dict | None:
    """Auto-wire extracted traits into mechanical effects (RACIAL_TRAIT_EFFECTS format).
    Uses LLM to classify each trait's game mechanics."""
    traits = race.get("traits", [])
    if not traits:
        return None

    # Build a compact traits summary for the LLM
    traits_summary = []
    for t in traits:
        name = t.get("name", "")
        desc = t.get("description", "")
        if name and desc:
            traits_summary.append({"name": name, "description": desc})

    if not traits_summary:
        return None

    prompt = TRAIT_EFFECTS_PROMPT.replace("{traits_json}", json.dumps(traits_summary, indent=2))
    print(f"      Wiring {len(traits_summary)} traits → ", end="", flush=True)
    raw = _call_llm(prompt)

    if not raw:
        print("FAILED")
        return None

    effects = _extract_json(raw)
    if effects:
        wired_count = sum(1 for v in effects.values() if any(v.values()))
        print(f"{wired_count} wired")
    else:
        print("NO JSON")
    return effects


def _empty_result() -> dict:
    return {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [], "subclasses": []
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_ability_key(k: str) -> str:
    """Normalize ability score key: 'str'/'strength' → 'strength'."""
    k = k.lower().strip()
    mapping = {
        "str": "strength", "dex": "dexterity", "con": "constitution",
        "int": "intelligence", "wis": "wisdom", "cha": "charisma",
        "strength": "strength", "dexterity": "dexterity",
        "constitution": "constitution", "intelligence": "intelligence",
        "wisdom": "wisdom", "charisma": "charisma",
    }
    return mapping.get(k, k)


def validate_extraction(data: dict, book_slug: str) -> dict:
    """Post-process and validate extracted data. Removes invalid entries."""
    cleaned = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [], "subclasses": []
    }
    issues = []

    def _ensure_dict(item, default_name="Unknown") -> dict:
        """Guard: convert string entries to dicts."""
        if isinstance(item, str):
            return {"name": item}
        if not isinstance(item, dict):
            return None
        return item

    # ── Races ──
    for r in data.get("races", []):
        r = _ensure_dict(r)
        if not r or not r.get("name"):
            continue
        # Normalize ASI keys
        if "asi" in r and isinstance(r["asi"], dict):
            r["asi"] = {_normalize_ability_key(k): v for k, v in r["asi"].items()
                        if _normalize_ability_key(k) in VALID_ABILITIES}
        # Validate numeric fields
        for fld in ("speed", "darkvision"):
            if fld in r and not isinstance(r.get(fld), (int, float)):
                try:
                    r[fld] = int(r[fld])
                except (ValueError, TypeError):
                    issues.append(f"Race {r['name']}: invalid {fld}={r.get(fld)}")
        # Flag races with zero traits AND zero subraces (likely incomplete)
        traits = r.get("traits", [])
        subraces = r.get("subraces", [])
        if not traits and not subraces:
            issues.append(f"Race {r['name']}: no traits or subraces — may be incomplete")
        r.setdefault("source", book_slug)
        cleaned["races"].append(r)

    # ── Spells ──
    for s in data.get("spells", []):
        s = _ensure_dict(s)
        if not s or not s.get("name"):
            continue
        # Reject spells with empty descriptions (partial/chunked entries)
        if not s.get("description", "").strip():
            issues.append(f"Spell {s['name']}: empty description — rejected")
            continue
        if "level" in s and not isinstance(s.get("level"), int):
            try:
                s["level"] = int(s["level"])
            except (ValueError, TypeError):
                issues.append(f"Spell {s['name']}: invalid level={s.get('level')}")
        if s.get("school", "").lower() not in VALID_SPELL_SCHOOLS:
            issues.append(f"Spell {s['name']}: unknown school={s.get('school')}")
        s.setdefault("source", book_slug)
        cleaned["spells"].append(s)

    # ── Magic Items ──
    for item in data.get("magic_items", []):
        item = _ensure_dict(item)
        if not item or not item.get("name"):
            continue
        # Clean up garbled item names
        name = item.get("name", "")
        if len(name) > 60:
            # Try to extract a shorter name from the first sentence of description
            desc = item.get("description", "")
            if desc and "." in desc:
                item["name"] = desc.split(".")[0].strip()[:60]
                issues.append(f"Item name cleaned: '{name[:50]}...' → '{item['name']}'")
            else:
                item["name"] = name[:60].rsplit(" ", 1)[0]  # Truncate to last full word
                issues.append(f"Item name truncated: '{name[:50]}...'")
        if item.get("rarity", "").lower() not in VALID_RARITIES:
            issues.append(f"Item {item['name']}: unknown rarity={item.get('rarity')}")
        item.setdefault("source", book_slug)
        cleaned["magic_items"].append(item)

    # ── Equipment ──
    for eq in data.get("equipment", []):
        if isinstance(eq, str):
            eq = {"name": eq, "type": "adventuring gear"}
        if not eq.get("name"):
            continue
        # Flag equipment with no cost or weight (likely incomplete extraction)
        if not eq.get("cost") and eq.get("weight", 0) == 0:
            issues.append(f"Equipment {eq['name']}: missing cost/weight")
        eq.setdefault("source", book_slug)
        cleaned["equipment"].append(eq)

    # ── Monsters ──
    for m in data.get("monsters", []):
        m = _ensure_dict(m)
        if not m or not m.get("name"):
            continue
        # Tag siege equipment / vehicles by their type
        mtype = str(m.get("type", "")).lower()
        if mtype in ("object", "vehicle", "siege equipment", "siege weapon"):
            m["type"] = "vehicle (siege)"
        # Reject monsters without stat blocks (name-only mentions)
        ac = m.get("armor_class")
        hp = m.get("hit_points")
        abilities = m.get("ability_scores", {})
        if not ac or not hp:
            issues.append(f"Monster {m['name']}: rejected (missing AC={ac}, HP={hp})")
            continue
        if not isinstance(abilities, dict) or len(abilities) < 3:
            issues.append(f"Monster {m['name']}: rejected (insufficient ability scores)")
            continue
        if m.get("size") and m["size"] not in VALID_SIZES:
            issues.append(f"Monster {m['name']}: unknown size={m.get('size')}")
        # Normalize abilities
        if "ability_scores" in m and isinstance(m["ability_scores"], dict):
            m["ability_scores"] = {_normalize_ability_key(k): v
                                   for k, v in m["ability_scores"].items()
                                   if _normalize_ability_key(k) in VALID_ABILITIES}
        m.setdefault("source", book_slug)
        cleaned["monsters"].append(m)

    # ── NPCs ──
    for npc in data.get("npcs", []):
        npc = _ensure_dict(npc)
        if not npc or not npc.get("name"):
            continue
        if "ability_scores" in npc and isinstance(npc["ability_scores"], dict):
            npc["ability_scores"] = {_normalize_ability_key(k): v
                                     for k, v in npc["ability_scores"].items()
                                     if _normalize_ability_key(k) in VALID_ABILITIES}
        npc.setdefault("source", book_slug)
        cleaned["npcs"].append(npc)

    # ── Feats ──
    for f in data.get("feats", []):
        f = _ensure_dict(f)
        if not f or not f.get("name"):
            continue
        f.setdefault("source", book_slug)
        cleaned["feats"].append(f)

    # ── Backgrounds ──
    for bg in data.get("backgrounds", []):
        bg = _ensure_dict(bg)
        if not bg or not bg.get("name"):
            continue
        bg.setdefault("source", book_slug)
        cleaned["backgrounds"].append(bg)

    # ── Subclasses ──
    for sc in data.get("subclasses", []):
        sc = _ensure_dict(sc)
        if not sc or not sc.get("name"):
            continue
        sc.setdefault("source", book_slug)
        cleaned["subclasses"].append(sc)

    if issues:
        print(f"    ⚠ Validation issues: {len(issues)}")
        for issue in issues[:5]:
            print(f"      - {issue}")

    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# Quality scoring for duplicate resolution
# ═══════════════════════════════════════════════════════════════════════════════

# D&D 5e domain-specific keywords — weighted by signal strength
_QUALITY_KEYWORDS: dict[str, float] = {
    # Mechanical terms (strong signal — real rules text)
    "saving throw": 2.0, "attack roll": 2.0, "bonus action": 2.0,
    "ability check": 1.5, "damage": 1.0, "resistance": 1.5,
    "immunity": 1.5, "vulnerability": 1.5, "concentration": 1.5,
    "spell slot": 1.5, "proficiency": 1.0, "advantage": 1.5,
    "disadvantage": 1.5, "hit points": 1.0, "armor class": 1.0,
    "challenge": 1.0, "initiative": 1.0, "reaction": 1.5,
    "ritual": 1.0, "cantrip": 1.0, "melee": 0.5,
    "ranged": 0.5, "touch": 0.5, "self": 0.3,
    # Structural signals (boilerplate = weak signal)
    "you can": 0.3, "you gain": 0.5, "at higher levels": 1.0,
    "per rest": 1.5, "per day": 1.0, "once per": 1.5,
    "you must": 0.5, "until you": 0.5,
}

# Penalty keywords — things that indicate LLM hallucination or generic filler
_QUALITY_PENALTY: dict[str, float] = {
    "i don't": 5.0, "i cannot": 5.0, "as an ai": 10.0,
    "might be": 3.0, "probably": 3.0, "typically": 2.0,
    "generally": 2.0, "in some cases": 2.0, "often": 1.5,
}


def _quality_score(entry: dict, category: str) -> float:
    """Score an extracted entry on detail/completeness. Higher = better.

    Categories: races, spells, magic_items, equipment, monsters, npcs,
                feats, backgrounds, subclasses.
    """
    score = 0.0
    text_fields: list[str] = []

    # ── 1. Gather all textual content ──
    desc = entry.get("description", "") or ""
    if isinstance(desc, str):
        text_fields.append(desc)

    # Sub-entity descriptions
    for key in ("traits", "features", "actions", "reactions", "legendary_actions"):
        for sub in entry.get(key, []) or []:
            if isinstance(sub, dict):
                sd = sub.get("description", "") or ""
                if isinstance(sd, str) and sd:
                    text_fields.append(sd)

    # Subraces carry their own nested content
    for sr in entry.get("subraces", []) or []:
        if isinstance(sr, dict):
            sd = sr.get("description", "") or ""
            if isinstance(sd, str) and sd:
                text_fields.append(sd)
            for st in sr.get("traits", []) or []:
                if isinstance(st, dict):
                    td = st.get("description", "") or ""
                    if isinstance(td, str) and td:
                        text_fields.append(td)

    combined = " ".join(text_fields).lower()

    # ── 2. Description length (log-scale, caps at ~2000 chars) ──
    total_len = len(" ".join(text_fields))
    if total_len == 0:
        score -= 20.0  # Heavy penalty for empty content
    else:
        score += min(total_len / 200, 10.0)  # 200 chars = 1 pt, max 10 pts

    # ── 3. Keyword density (D&D mechanics) ──
    for keyword, weight in _QUALITY_KEYWORDS.items():
        count = combined.count(keyword)
        if count:
            score += min(count * weight, 5.0)  # Cap per-keyword contribution

    # ── 4. Penalty for LLM hallmarks ──
    for penalty_word, penalty in _QUALITY_PENALTY.items():
        if penalty_word in combined:
            score -= penalty

    # ── 5. Category-specific structural completeness ──
    score += _structural_bonus(entry, category)

    return score


def _structural_bonus(entry: dict, category: str) -> float:
    """Reward entries that have all expected structural fields for their category."""
    bonus = 0.0
    fields = {
        "races": ["asi", "speed", "size", "languages", "traits"],
        "spells": ["level", "school", "casting_time", "range", "components", "duration"],
        "magic_items": ["type", "rarity"],
        "equipment": ["type", "cost", "weight"],
        "monsters": ["size", "type", "armor_class", "hit_points", "ability_scores", "challenge_rating"],
        "npcs": ["race", "armor_class", "hit_points", "ability_scores"],
        "feats": ["prerequisite"],
        "backgrounds": ["skill_proficiencies", "equipment"],
        "subclasses": ["class", "features"],
    }
    expected = fields.get(category, [])
    if not expected:
        return 0.0

    present = sum(1 for f in expected if entry.get(f))
    bonus += (present / len(expected)) * 3.0  # Max 3 pts for full structure

    # Sub-entities bonus
    sub_lists = {
        "races": "subraces",
        "subclasses": "features",
        "monsters": "actions",
        "backgrounds": "skill_proficiencies",
    }
    sub_key = sub_lists.get(category)
    if sub_key:
        sub_val = entry.get(sub_key)
        if isinstance(sub_val, list) and len(sub_val) > 0:
            bonus += 2.0  # Has sub-entities

    return bonus


def _pick_better(existing: dict, new: dict, category: str) -> dict:
    """Compare two entries and return the higher-quality one.
    If new is measurably better (>15% score delta), return new.
    Otherwise keep existing (stability bias)."""
    existing_score = _quality_score(existing, category)
    new_score = _quality_score(new, category)

    # If new is significantly better (>15% margin), use it
    if existing_score <= 0 and new_score > 0:
        return new
    if new_score > existing_score * 1.15:
        return new
    return existing


# ═══════════════════════════════════════════════════════════════════════════════
# Dedup against known base data
# ═══════════════════════════════════════════════════════════════════════════════

def _load_base_names() -> dict[str, set]:
    """Load known names from SRD cache and hardcoded data to avoid duplicates."""
    names = {k: set() for k in ["races", "spells", "magic_items", "equipment",
                                  "monsters", "npcs", "feats", "backgrounds", "subclasses"]}

    srd_dir = HERE / "data" / "srd_cache"

    # Races from SRD cache
    for fname in ["races", "subraces"]:
        data = _load_json_list(srd_dir / f"{fname}.json")
        for entry in data:
            names["races"].add(entry.get("name", "").lower())

    # Also hardcoded RACES from main.py
    hardcoded_races = ["Dwarf", "Elf", "Halfling", "Human", "Dragonborn",
                       "Gnome", "Half-Elf", "Half-Orc", "Tiefling"]
    for r in hardcoded_races:
        names["races"].add(r.lower())

    # Spells from SRD
    spells = _load_json_list(srd_dir / "spells.json")
    for s in spells:
        names["spells"].add(s.get("name", "").lower())

    # Monsters from SRD
    monsters = _load_json_list(srd_dir / "monsters.json")
    for m in monsters:
        names["monsters"].add(m.get("name", "").lower())

    # Magic items from SRD
    items = _load_json_list(srd_dir / "magic-items.json")
    for i in items:
        names["magic_items"].add(i.get("name", "").lower())

    # Equipment from SRD
    equip = _load_json_list(srd_dir / "equipment.json")
    for e in equip:
        names["equipment"].add(e.get("name", "").lower())

    # Feats from SRD
    feats = _load_json_list(srd_dir / "feats.json")
    for f in feats:
        names["feats"].add(f.get("name", "").lower())

    # Backgrounds from SRD
    bgs = _load_json_list(srd_dir / "backgrounds.json")
    for bg in bgs:
        names["backgrounds"].add(bg.get("name", "").lower())

    return names


def _normalize_name(name: str) -> str:
    """Normalize for dedup: lowercase, strip parentheticals, normalize dashes,
    strip trailing 's' (singular/plural), remove punctuation."""
    n = name.lower().strip()
    # Strip parenthetical content: "Fireball (5th-level)" → "fireball"
    n = re.sub(r'\s*\([^)]*\)', '', n).strip()
    # Normalize dashes to spaces: "Half-Elf" → "half elf"
    n = n.replace('-', ' ').replace('–', ' ').replace('—', ' ')
    # Collapse multiple spaces
    n = re.sub(r'\s+', ' ', n).strip()
    # Strip trailing punctuation
    n = n.rstrip('.,;:!?')
    # Strip trailing 's' for plural dedup (Geonid/Geonids, Cannibal/Cannibals)
    # But not if it would leave a too-short word or the word ends in 'ss'
    if n.endswith('s') and not n.endswith('ss') and len(n) > 4:
        n = n[:-1]
    return n


def dedup_extraction(data: dict, base_names: dict[str, set]) -> dict:
    """Remove entries whose name already exists in base data. Uses fuzzy matching.
    Does NOT mutate base_names — works on a copy to prevent cross-chapter contamination."""
    new_data = {}
    total_removed = 0

    for category in data:
        items = data[category]
        # Copy to avoid mutating the caller's base_names (critical for chapter-by-chapter mode)
        known = set(base_names.get(category, set()))
        known_normalized = {_normalize_name(n) for n in known}
        new_items = []
        for item in items:
            name = item.get("name", "")
            name_lower = name.lower()
            name_norm = _normalize_name(name)
            # Exact match
            if name_lower and name_lower in known:
                total_removed += 1
                continue
            # Fuzzy match (singular/plural)
            if name_norm and name_norm in known_normalized:
                total_removed += 1
                continue
            new_items.append(item)
            if name_lower:
                known.add(name_lower)
                known_normalized.add(name_norm)
        new_data[category] = new_items

    if total_removed:
        print(f"    Deduped {total_removed} entries already in base data")
    return new_data


def _dedup_within_extraction(data: dict) -> dict:
    """Remove duplicates *within* a single extraction (same book, different chunks).
    Uses the same _normalize_name logic for fuzzy matching.
    When two entries for the same name exist, keeps the higher-quality one."""
    deduped = {}
    total_removed = 0
    total_upgraded = 0
    for category, items in data.items():
        if category.startswith("_"):
            continue
        seen: dict[str, tuple[dict, int]] = {}  # name_norm → (entry, index)
        new_items = []
        for item in items:
            name = item.get("name", "")
            name_norm = _normalize_name(name)
            if not name_norm:
                new_items.append(item)
                continue
            if name_norm in seen:
                existing, _ = seen[name_norm]
                better = _pick_better(existing, item, category)
                if better is item:
                    # New entry is better — replace the old one
                    total_upgraded += 1
                    # Find and replace the old entry in new_items
                    for i, ni in enumerate(new_items):
                        if _normalize_name(ni.get("name", "")) == name_norm:
                            new_items[i] = item
                            seen[name_norm] = (item, i)
                            break
                    total_removed += 1  # Count the old one as removed
                else:
                    total_removed += 1  # Discard new, keep existing
                continue
            idx = len(new_items)
            seen[name_norm] = (item, idx)
            new_items.append(item)
        deduped[category] = new_items
    if total_removed:
        parts = [f"removed {total_removed} duplicate(s)"]
        if total_upgraded:
            parts.append(f"upgraded {total_upgraded} with better version")
        print(f"    Intra-book dedup: {', '.join(parts)}")
    return deduped


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter detection
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_chapters(text: str, manual: dict) -> list[dict]:
    """Detect chapter/section boundaries using fitz TOC + page markers in text.

    Returns [{title, start_page, end_page, text}].
    Falls back to a single 'Full Book' chapter if no TOC or page markers.
    """
    slug = manual["slug"]
    pdf_path = Path(manual["abs_path"])

    # Try fitz TOC
    chapters = _detect_from_toc(pdf_path)
    if chapters:
        # Split text by page markers
        page_texts = _split_by_page(text)
        for ch in chapters:
            ch["text"] = _extract_chapter_text(page_texts, ch["start_page"], ch["end_page"])
        # Filter out chapters with no extractable text (maps, image-only pages)
        chapters = [ch for ch in chapters if ch["text"] and len(ch["text"]) >= 100]
        if chapters:
            print(f"  {len(chapters)} chapter(s) with text retained")
            return chapters

    # Fallback: check for text-based chapter markers
    chapters = _detect_from_text(text)
    if chapters and len(chapters) > 1:
        return chapters

    # Ultimate fallback: single chapter
    return [{"title": manual["title"], "start_page": 1, "end_page": 9999, "text": text}]


def _detect_from_toc(pdf_path: Path) -> list[dict] | None:
    """Use fitz Table of Contents to get chapter boundaries."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        toc = doc.get_toc()
        doc.close()

        if not toc or len(toc) < 2:
            return None

        # Collect top-level (L1) entries as chapters
        chapters = []
        for i, (level, title, page) in enumerate(toc):
            if level != 1:
                continue
            # Clean title
            title = title.strip()
            if not title or title.lower() in ("credits", "table of contents", "contents", "back cover"):
                continue
            chapters.append({"title": title, "start_page": page, "end_page": 9999})

        if not chapters:
            return None

        # Set end pages (each chapter ends at the next chapter's start - 1)
        for i in range(len(chapters) - 1):
            chapters[i]["end_page"] = chapters[i + 1]["start_page"] - 1

        print(f"  Detected {len(chapters)} chapters from TOC:")
        for ch in chapters:
            print(f"    p{ch['start_page']}-{ch['end_page']}: {ch['title']}")
        return chapters

    except Exception as e:
        print(f"  TOC detection skipped: {e}")
        return None


def _detect_from_text(text: str) -> list[dict] | None:
    """Fallback: use regex chapter markers in text."""
    marker_pattern = re.compile(
        r'^--- PAGE (\d+) ---.*?\n'           # page number
        r'(?:CHAPTER|Ch\.|Chapter)\s+(\d+)[:\s]',  # chapter marker
        re.MULTILINE | re.IGNORECASE
    )
    matches = list(marker_pattern.finditer(text))
    if not matches:
        return None

    chapters = []
    for i, m in enumerate(matches):
        page = int(m.group(1))
        ch_num = m.group(2)
        title = f"Chapter {ch_num}"
        chapters.append({"title": title, "start_page": page, "end_page": 9999})

    for i in range(len(chapters) - 1):
        chapters[i]["end_page"] = chapters[i + 1]["start_page"] - 1

    print(f"  Detected {len(chapters)} chapters from text markers")
    return chapters


def _split_by_page(text: str) -> dict[int, str]:
    """Split text with page markers into {page_num: page_text}."""
    pages = {}
    pattern = re.compile(r'--- PAGE (\d+) ---\n(.*?)(?=\n--- PAGE \d+ ---|\Z)', re.DOTALL)
    for m in pattern.finditer(text):
        page_num = int(m.group(1))
        page_text = m.group(2).strip()
        pages[page_num] = page_text
    return pages


def _extract_chapter_text(page_texts: dict[int, str], start: int, end: int) -> str:
    """Extract text for page range [start, end]."""
    parts = []
    for p in range(start, end + 1):
        if p in page_texts:
            parts.append(page_texts[p])
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Per-chapter processing
# ═══════════════════════════════════════════════════════════════════════════════

def _process_chapter(chapter: dict, slug: str, base_names: dict) -> dict:
    """Run the full extraction pipeline on a single chapter's text.
    Returns {chapter_title, new_entries: {category: [items]}, issues, stats}."""
    ch_title = chapter["title"]
    ch_text = chapter["text"]

    if not ch_text or len(ch_text) < 200:
        return {
            "chapter": ch_title,
            "new_entries": {},
            "issues": [f"Chapter text too short ({len(ch_text)} chars), skipped"],
            "stats": {"chunks": 0, "extracted": 0, "valid": 0, "new": 0,
                      "intra_deduped": 0, "srd_deduped": 0, "rejected": 0},
        }

    # 1. Chunk
    chunks = chunk_text(ch_text)
    if not chunks:
        return {
            "chapter": ch_title,
            "new_entries": {},
            "issues": ["No chunkable text"],
            "stats": {"chunks": 0, "extracted": 0, "valid": 0, "new": 0,
                      "intra_deduped": 0, "srd_deduped": 0, "rejected": 0},
        }

    # 2. Extract from each chunk
    accumulated = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [], "subclasses": []
    }

    for chunk in chunks:
        result = extract_from_chunk(chunk, slug)
        for cat in accumulated:
            if cat in result and result[cat]:
                accumulated[cat].extend(result[cat])
        time.sleep(0.5)

    # 3. Intra-chapter dedup
    before_counts = {cat: len(items) for cat, items in accumulated.items()}
    accumulated = _dedup_within_extraction(accumulated)
    after_counts = {cat: len(items) for cat, items in accumulated.items()}
    intra_deduped = sum(before_counts[c] - after_counts[c] for c in before_counts)

    # 4. Validate
    validated = validate_extraction(accumulated, slug)
    total_extracted = sum(len(v) for v in accumulated.values())
    total_validated = sum(len(v) for v in validated.values())
    rejected = total_extracted - total_validated

    # 5. Dedup vs SRD/base
    new_data = dedup_extraction(validated, base_names)
    total_new = sum(len(v) for v in new_data.values())
    srd_deduped = total_validated - total_new

    # 6. Build readout & return
    return {
        "chapter": ch_title,
        "new_entries": new_data,
        "stats": {
            "chunks": len(chunks),
            "extracted": total_extracted,
            "valid": total_validated,
            "new": total_new,
            "intra_deduped": intra_deduped,
            "srd_deduped": srd_deduped,
            "rejected": rejected,
        },
        "issues": [],
    }


def _print_chapter_readout(result: dict):
    """Print a human-readable chapter result readout + notify Telegram."""
    ch = result["chapter"]
    stats = result["stats"]
    new_data = result["new_entries"]

    lines = []
    lines.append(f"\n── Chapter Readout: {ch} ──")
    lines.append(f"Stats: {stats['chunks']} chunks → {stats['extracted']} raw → "
                 f"{stats['valid']} valid → {stats['new']} new")
    if stats["intra_deduped"]:
        lines.append(f"  {stats['intra_deduped']} intra-chapter duplicates removed")
    if stats["srd_deduped"]:
        lines.append(f"  {stats['srd_deduped']} SRD/base duplicates removed")
    if stats["rejected"]:
        lines.append(f"  {stats['rejected']} entries rejected by validation")

    if stats["new"] == 0:
        lines.append("No new entries in this chapter.")
        _send_readout("\n".join(lines))
        return

    for cat, items in sorted(new_data.items()):
        if not items:
            continue
        names = [i.get("name", "?") for i in items[:8]]
        extra = f" (+{len(items) - 8} more)" if len(items) > 8 else ""
        lines.append(f"  {cat}: {len(items)} — {', '.join(names)}{extra}")

    flags = _check_chapter_quality(new_data)
    if flags:
        for f in flags:
            lines.append(f"  ⚠️ {f}")

    _send_readout("\n".join(lines))


def _check_chapter_quality(data: dict) -> list[str]:
    """Quick quality checks on extracted data."""
    flags = []
    for race in data.get("races", []):
        if not race.get("asi"):
            flags.append(f"Race '{race['name']}' missing ASI")
        if not race.get("traits"):
            flags.append(f"Race '{race['name']}' has no traits")
    for spell in data.get("spells", []):
        if not spell.get("level") and spell.get("level") != 0:
            flags.append(f"Spell '{spell['name']}' missing level")
        if not spell.get("school"):
            flags.append(f"Spell '{spell['name']}' missing school")
    for monster in data.get("monsters", []):
        if not monster.get("armor_class"):
            flags.append(f"Monster '{monster['name']}' missing AC")
        if not monster.get("hit_points"):
            flags.append(f"Monster '{monster['name']}' missing HP")
    return flags


# ═══════════════════════════════════════════════════════════════════════════════
# Main extraction pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def process_manual(manual: dict) -> dict | None:
    """Full extraction pipeline for one manual — chapter by chapter."""
    slug = manual["slug"]
    print(f"\n{'='*60}")
    print(f"Processing: {manual['title']} ({slug})")
    print(f"  PDF: {manual['path']} ({manual['size_kb']} KB)")

    if slug in SKIP_EXTRACTION:
        print(f"  SKIPPED: {slug} is covered by SRD base data")
        extract_text(manual)
        return None

    # 1. Extract text (with page markers)
    text = extract_text(manual)
    if not text:
        return None

    # 2. Detect chapters
    chapters = _detect_chapters(text, manual)
    base_names = _load_base_names()

    # 3. Check for existing extraction (chapter-aware state)
    extracted_path = CACHE_DIR / f"{slug}_extracted.json"
    existing = _load_json(extracted_path) if extracted_path.exists() else {}
    if not isinstance(existing, dict):
        existing = {}

    # Detect if old-style extraction exists (flat chunks, no chapters)
    is_old_format = "_chunks_processed" in existing and "_chapters_completed" not in existing
    if is_old_format:
        print(f"  Old-format extraction found (flat chunks). Starting fresh with chapter mode.\n")
        existing = {}

    chapters_completed = set(existing.get("_chapters_completed", []))
    chapter_errors = existing.get("_chapter_errors", {})

    # Crash recovery: if a chapter was in progress when script died, flag it
    stalled_chapter = existing.get("_current_chapter", "")
    if stalled_chapter and stalled_chapter not in chapters_completed:
        print(f"  ⚠️ Detected interrupted chapter: '{stalled_chapter}' — will re-process\n")
        chapter_errors[stalled_chapter] = "Interrupted (crash/timeout) — re-processing"
        # Remove any partial data from the stalled chapter from accumulated state
        # (since we can't track per-chapter items, we just note it for the readout)

    print(f"  {len(chapters)} chapter(s), {len(chapters_completed)} completed, "
          f"{len(chapter_errors)} with errors\n")

    # 4. Process each chapter
    all_results = []
    total_new = 0
    base_accumulated = {
        "races": existing.get("races", []),
        "spells": existing.get("spells", []),
        "magic_items": existing.get("magic_items", []),
        "equipment": existing.get("equipment", []),
        "monsters": existing.get("monsters", []),
        "npcs": existing.get("npcs", []),
        "feats": existing.get("feats", []),
        "backgrounds": existing.get("backgrounds", []),
        "subclasses": existing.get("subclasses", []),
    }

    for i, chapter in enumerate(chapters):
        ch_title = chapter["title"]
        if ch_title in chapters_completed:
            print(f"  [{i+1}/{len(chapters)}] {ch_title} — already completed, skipping")
            continue

        print(f"  [{i+1}/{len(chapters)}] {ch_title} "
              f"({len(chapter['text']):,} chars)")

        # Save current-chapter marker BEFORE processing (crash detection)
        _save_json(extracted_path, {**base_accumulated,
                   "_chapters_completed": list(chapters_completed),
                   "_current_chapter": ch_title,
                   "_total_chapters": len(chapters),
                   "_book_slug": slug, "_book_title": manual["title"],
                   "_completed": False, "_timestamp": time.time()})

        try:
            result = _process_chapter(chapter, slug, base_names)
        except Exception as e:
            print(f"  ✖ ERROR processing chapter: {e}")
            chapter_errors[ch_title] = str(e)[:200]
            # Save error state and continue to next chapter
            saved = {**base_accumulated,
                     "_chapters_completed": list(chapters_completed),
                     "_chapter_errors": chapter_errors,
                     "_current_chapter": "",
                     "_total_chapters": len(chapters),
                     "_book_slug": slug, "_book_title": manual["title"],
                     "_completed": False, "_timestamp": time.time()}
            _save_json(extracted_path, saved)
            continue

        all_results.append(result)
        _print_chapter_readout(result)

        # Accumulate
        for cat, items in result["new_entries"].items():
            if cat in base_accumulated:
                base_accumulated[cat].extend(items)

        total_new += result["stats"]["new"]
        chapters_completed.add(ch_title)

        # Save incremental state after each chapter
        saved = {**base_accumulated,
                 "_chapters_completed": list(chapters_completed),
                 "_chapter_errors": chapter_errors,
                 "_current_chapter": "",  # cleared: chapter completed successfully
                 "_total_chapters": len(chapters),
                 "_book_slug": slug,
                 "_book_title": manual["title"],
                 "_completed": False,
                 "_timestamp": time.time()}
        _save_json(extracted_path, saved)

        if len(chapters) > 1:
            time.sleep(1)  # breathe between chapters

    if not chapters_completed:
        return None

    # 5. Race second-pass extraction (on full book text, catches multi-chapter races)
    races_found = base_accumulated.get("races", [])
    if races_found and text:
        print(f"\n  Race detail extraction for {len(races_found)} race(s)...")
        for i, race in enumerate(races_found):
            race_name = race.get("name", "")
            if not race_name:
                continue
            details = _extract_race_details(race_name, text)
            if details:
                races_found[i] = _merge_race_details(race, details)
        base_accumulated["races"] = races_found

    # 6. Final cross-chapter dedup
    base_accumulated = _dedup_within_extraction(base_accumulated)

    # 7. Final validate
    validated = validate_extraction(base_accumulated, slug)

    # 8. Final dedup vs SRD/base
    new_data = dedup_extraction(validated, base_names)

    # 9. Trait effects auto-wiring
    races_to_wire = new_data.get("races", [])
    if races_to_wire:
        print(f"\n  Auto-wiring trait effects for {len(races_to_wire)} race(s)...")
        for race in races_to_wire:
            wired = _wire_trait_effects(race)
            if wired:
                race["_effects"] = wired

    # 10. Final save
    final = {**new_data,
             "_chapters_completed": list(chapters_completed),
             "_chapter_errors": chapter_errors,
             "_current_chapter": "",  # all done
             "_total_chapters": len(chapters),
             "_book_slug": slug,
             "_book_title": manual["title"],
             "_completed": True,
             "_timestamp": time.time()}
    _save_json(extracted_path, final)

    # 11. Final report
    total_new = sum(len(v) for v in new_data.values())
    print(f"\n  ✓ Extraction complete: {total_new} new entries across {len(chapters)} chapter(s)")
    for cat, items in sorted(new_data.items()):
        if items:
            names = [i.get("name", "?") for i in items[:5]]
            suffix = f" (+{len(items)-5} more)" if len(items) > 5 else ""
            print(f"    {cat}: {len(items)} — {', '.join(names)}{suffix}")

    return new_data


# ═══════════════════════════════════════════════════════════════════════════════
# Merge all extractions into app-loadable files
# ═══════════════════════════════════════════════════════════════════════════════

def merge_all_extractions():
    """Scan all per-manual extractions and merge into data/manual_data/*.json."""
    print("\n" + "="*60)
    print("Merging all manual extractions...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    merged = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [], "subclasses": []
    }

    seen = {cat: set() for cat in merged}
    raw_names = {cat: set() for cat in merged}  # track actual names for source reporting
    sources = {cat: {} for cat in merged}

    for ext_file in sorted(CACHE_DIR.glob("*_extracted.json")):
        data = _load_json(ext_file)
        if not isinstance(data, dict) or not data.get("_completed"):
            continue

        slug = data.get("_book_slug", ext_file.stem.replace("_extracted", ""))
        for cat in merged:
            for item in data.get(cat, []):
                name = item.get("name", "")
                name_norm = _normalize_name(name)
                if name_norm and name_norm not in seen[cat]:
                    seen[cat].add(name_norm)
                    raw_names[cat].add(name.lower())
                    merged[cat].append(item)
                    sources[cat][name_norm] = slug

    # Save merged files
    for cat, items in merged.items():
        path = OUTPUT_DIR / f"{cat}.json"
        _save_json(path, items)

    # Save metadata
    meta = {
        "merged_at": time.time(),
        "source_manuals": list(set(
            data.get("_book_slug", "")
            for ext_file in sorted(CACHE_DIR.glob("*_extracted.json"))
            if (data := _load_json(ext_file)) and data.get("_completed")
        )),
        "totals": {cat: len(items) for cat, items in merged.items()},
    }
    _save_json(OUTPUT_DIR / "_meta.json", meta)

    print(f"\nMerged data written to {OUTPUT_DIR}/")
    for cat, items in sorted(merged.items()):
        if items:
            print(f"  {cat}.json: {len(items)} entries")


# ═══════════════════════════════════════════════════════════════════════════════
# Status / listing
# ═══════════════════════════════════════════════════════════════════════════════

def list_manuals():
    """List all manuals with extraction status."""
    manuals = discover_manuals()
    if not manuals:
        print("No manuals found.")
        return

    print(f"{'Status':10s} {'Slug':6s} {'Manual':48s} {'Size':>8s}  Chunks")
    print("-" * 90)

    for m in manuals:
        ext_path = CACHE_DIR / f"{m['slug']}_extracted.json"
        if ext_path.exists():
            data = _load_json(ext_path)
            if data.get("_completed"):
                total = sum(len(v) for k, v in data.items()
                           if isinstance(v, list) and not k.startswith("_"))
                chapters_done = len(data.get('_chapters_completed', []))
                chapters_total = data.get('_total_chapters', '?')
                errors = len(data.get('_chapter_errors', {}))
                extra = f"{chapters_done}/{chapters_total} chapters, {total} entries"
                if errors:
                    extra += f", {errors} errors"
                status = f"✅ done"
                chunks = extra
            else:
                chapters_done = len(data.get("_chapters_completed", []))
                chapters_total = data.get("_total_chapters", "?")
                errors = len(data.get("_chapter_errors", {}))
                current = data.get("_current_chapter", "")
                status = f"🔄 {chapters_done}/{chapters_total}ch"
                if current:
                    status += f" @{current[:12]}"
                if errors:
                    status += f" ⚠️{errors}"
                chunks = ""
        elif m["slug"] in SKIP_EXTRACTION:
            status = "⏭️ SKIP"
            chunks = "(SRD base)"
        else:
            status = "⬜ NEW"
            chunks = ""

        print(f"{status:10s} {m['slug']:6s} {m['title'][:46]:48s} {m['size_kb']:>5} KB  {chunks}")

    print(f"\n{len(manuals)} manuals total")
    print(f"Run with a manual name to ingest, or --all for all new, or --merge to rebuild merged data.")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return

    cmd = sys.argv[1]

    if cmd == "--list":
        list_manuals()
        return

    if cmd == "--merge":
        merge_all_extractions()
        return

    if cmd == "--all":
        manuals = discover_manuals()
        for m in manuals:
            if m["slug"] in SKIP_EXTRACTION:
                continue
            ext_path = CACHE_DIR / f"{m['slug']}_extracted.json"
            if ext_path.exists():
                data = _load_json(ext_path)
                if data.get("_completed"):
                    print(f"Skipping {m['title']} — already extracted")
                    continue
            process_manual(m)
        merge_all_extractions()
        return

    # Process a specific manual by name/slug
    query = cmd.lower()
    manuals = discover_manuals()
    for m in manuals:
        if (query in m["title"].lower() or
            query in m["slug"].lower() or
            query in m["filename"].lower()):
            process_manual(m)
            merge_all_extractions()
            return

    print(f"Manual '{cmd}' not found. Use --list to see available manuals.")


if __name__ == "__main__":
    import subprocess  # used in extract_text
    main()
