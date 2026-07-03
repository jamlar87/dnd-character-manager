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

import json, os, re, sys, time, hashlib, subprocess, urllib.request, urllib.error
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
MANUALS_DIR = (HERE / "manuals").resolve()
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
SKIP_EXTRACTION: set[str] = set()  # All manuals processed — nothing skipped

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
    """Extract JSON from LLM response, stripping markdown wrappers.
    Includes repair logic for common LLM JSON errors: control chars,
    trailing commas, truncated output, unclosed brackets."""
    if not text:
        return None

    # Strip markdown code fences
    candidates = []
    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside code block
                if part.startswith("json"):
                    part = part[4:]
                candidates.append(part.strip())
    else:
        candidates.append(text.strip())

    for candidate in candidates:
        result = _try_parse_json(candidate)
        if result is not None:
            return result

    # Last resort: regex for JSON object anywhere in raw text
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        result = _try_parse_json(m.group())
        if result is not None:
            return result

    return None


def _try_parse_json(raw: str) -> dict | None:
    """Try to parse JSON with progressive repair attempts."""
    # 0. Fix OCR artifacts BEFORE parsing — prevents corrupted text from
    #    surviving into the data at all. Applied to raw LLM output.
    raw = _fix_ocr_artifacts(raw)

    # 1. Straight parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Remove control characters (0x00-0x1F except \t, \n, \r)
    cleaned = _clean_json_controls(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Fix trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 4. Try to close truncated JSON (add missing brackets/quotes)
    truncated = _close_truncated_json(fixed)
    if truncated != fixed:
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    # 5. Aggressive: extract the largest valid JSON sub-object
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw):
        try:
            candidate = _clean_json_controls(m.group())
            candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def _clean_json_controls(text: str) -> str:
    """Remove control characters that break JSON, preserving \t, \n, \r in strings.
    Strategy: replace raw control chars inside string values with spaces."""
    result = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == '\\':
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            # Inside a string value: replace raw control chars with space
            if ord(ch) < 0x20 and ch not in ('\t', '\n', '\r'):
                result.append(' ')
            else:
                result.append(ch)
        else:
            result.append(ch)
    return ''.join(result)


# ── OCR artifact correction ──
# These patterns come from real PDF→text extraction where serif fonts
# cause systematic misreads: Y→V, t→l, m→rn, etc.
_OCR_FIXES: list[tuple[str, str]] = [
    # V↔Y confusion (serif capital Y reads as V)
    (r'\bVou\b', 'You'), (r'\bvou\b', 'you'), (r'\bVour\b', 'Your'),
    # l↔t confusion (lowercase t reads as l)
    (r'\blhe\b', 'the'), (r'\blhal\b', 'that'), (r'\blhis\b', 'this'),
    (r'\blhan\b', 'than'), (r'\blhen\b', 'then'), (r'\blhere\b', 'there'),
    (r'\blhose\b', 'those'), (r'\blhing\b', 'thing'), (r'\blhink\b', 'think'),
    (r'\blhree\b', 'three'), (r'\blhrough\b', 'through'), (r'\blhrow\b', 'throw'),
    (r'\blhrone\b', 'throne'), (r'\blurn\b', 'turn'),
    # l↔1 confusion in dice notation
    (r'\bld(\d+)\b', r'1d\1'), (r'\bId(\d+)\b', r'1d\1'),
    # rn→m / ll→m (OCR misreads)
    (r'\bllaximum\b', 'maximum'), (r'\bllinimum\b', 'minimum'),
    (r'\bllove(s|d|ment)?\b', r'move\1'), (r'\bllagic(al)?\b', r'magic\1'),
    (r'\bllodifier\b', 'modifier'), (r'\bllake\b', 'make'),
    (r'\bllay\b', 'may'), (r'\bllust\b', 'must'), (r'\bllore\b', 'more'),
    (r'\bllonth\b', 'month'), (r'\bllinute\b', 'minute'),
    (r'\bllaterial\b', 'material'), (r'\bllaster\b', 'master'),
    (r'\bIllaster\b', 'master'),
    (r'\brnaximum\b', 'maximum'), (r'\brnagic\b', 'magic'), (r'\brnake\b', 'make'),
    # Common mangled words
    (r'\bcrealure\b', 'creature'), (r'\bcrealures\b', 'creatures'),
    (r'\bdalllage\b', 'damage'), (r'\baclion\b', 'action'),
    (r'\bbeffecl\b', 'effect'), (r'\bbeffecls\b', 'effects'),
    (r'\breaclion\b', 'reaction'), (r'\bbenelils?\b', 'benefits'),
    (r'\bproleclion\b', 'protection'), (r'\bRolI\b', 'Roll'),
    (r'\bnotjust\b', 'not just'), (r'\bbdore\b', 'before'),
    (r'\bWhcn\b', 'When'), (r'\bbeeornes\b', 'becomes'),
    (r'\bdiscordam\b', 'discordant'), (r'\bfillthe\b', 'fill the'),
    (r'\blevei\b', 'level'), (r'\bleveI\b', 'level'),
    (r'\bproliciency\b', 'proficiency'), (r'\bproticiency\b', 'proficiency'),
    (r"olheI'", "other"), (r"ralheI'", 'rather'),
    (r'\blry\b', 'try'), (r'\bwilhin\b', 'within'), (r'\bwilh\b', 'with'),
    (r'\bvalnerable\b', 'vulnerable'),
    # Mixed case / spacing
    (r'lesse r\b', 'lesser'), (r'\bof20\b', 'of 20'),
]


def _fix_ocr_artifacts(text: str) -> str:
    """Apply OCR correction patterns to a string. Idempotent — safe to call
    on already-clean text (no double-correction)."""
    if not text:
        return text
    for pattern, replacement in _OCR_FIXES:
        text = re.sub(pattern, replacement, text)
    return text


def _close_truncated_json(text: str) -> str:
    """Close truncated JSON by adding missing closing brackets and quotes.
    Uses a stack to track nesting so inner brackets close before outer ones."""
    stack = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch == '}' and stack and stack[-1] == '}':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == ']':
            stack.pop()

    result = text
    if in_string:
        result += '"'
    # Close in reverse order (inner-most first)
    result += ''.join(reversed(stack))
    return result


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
    seen_slugs = {}  # slug → count, for uniqueness
    manuals = []
    # Non-content patterns to skip
    # Patterns that apply to the PDF's own filename only (not parent directory)
    _skip_filename_only = [
        r'(?i)screen\\b',           # GM/loremaster screens
        r'(?i)character.?sheet',   # blank or pre-filled character sheets
        r'(?i)jpg.?map.?pack',     # JPG-based map bundles
    ]
    # Patterns that apply to filename OR parent directory
    _skip_filename_or_parent = [
        r'(?i)\\bmap[s]?\\b',      # map packs, battle maps, region maps
        r'(?i)endpaper',           # decorative endpapers
        r'(?i)\\bcover\\b',         # standalone cover art
    ]
    for f in sorted(MANUALS_DIR.rglob("*.pdf", recurse_symlinks=True)):
        title = f.stem.replace("_", " ").replace("-", " ").replace("  ", " ").strip()
        parent = str(f.parent.name)
        # Skip non-content PDFs (maps, endpapers, character sheets, covers, screens)
        if any(re.search(pat, title) for pat in _skip_filename_only):
            continue
        if any(re.search(pat, title) or re.search(pat, parent) for pat in _skip_filename_or_parent):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        # Derive short slug/label, guarantee uniqueness
        slug = _derive_slug(title)
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0

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
        # ── Kobold Press ──
        ("creature codex", "CC"),
        ("tome of beasts", "ToB"),
        ("book of ebon tides", "EBT"),
        ("courts of the shadow fey", "CSF"),
        ("deep magic", "DPM"),
        ("tales from the shadows", "TFS"),
        ("tales of the margreve", "TOM"),
        ("margreve players guide", "MPG"),
        ("wrath of the river king", "WRK"),
        ("expanding the ranger", "ETR"),
        ("shadows of the dusk queen", "SDQ"),
        ("marauders of the margreve", "MOM"),
        ("encounters in avernus", "EIA"),
        ("saltmarsh encounters", "SME"),
        ("ratatosk", "RAT"),
        ("warlock lair", "WLL"),
        ("warlock lairs", "WLL"),
        ("warlock bestiary", "WLB"),
        # ── TLOTR / Adventures in Middle-earth ──
        ("adventuresinmiddle earthloremastersguide", "LMG"),
        ("adventuresinmiddle earthplayersguide", "AIPG"),
        ("adventures in middle-earth", "AIME"),
        ("adventuresinmiddle", "AIME"),
        ("bree land", "BLRG"),
        ("erebor adventures", "EREA"),
        ("ereboradventures", "EREA"),
        ("eriador adventures", "ERIA"),
        ("eriadoradventures", "ERIA"),
        ("lonely mountain", "LMRG"),
        ("lonelymountain", "LMRG"),
        ("loremaster", "LMG"),
        ("mirkwood campaign", "MWC"),
        ("mirkwoodcampaign", "MWC"),
        ("player's guide", "AIPG"),
        ("playersguide", "AIPG"),
        ("rhovanion", "RRG"),
        ("rivendell", "RVR"),
        ("the road goes ever on", "RGEO"),
        ("theroadgoeseveron", "RGEO"),
        ("wilderland adventures", "WLA"),
        ("wilderlandadventures", "WLA"),
        ("eaves of mirkwood", "EOM"),
        ("eavesofmirkwood", "EOM"),
        # ── DM's Guild / AL modules ──
        ("defiance in phlan", "DDP"),
        ("secrets of sokol keep", "SSK"),
        ("shadows over the moonsea", "SOM"),
        ("dues for the dead", "DFD"),
        # ── Critical Role ──
        ("call of the netherdeep", "CotN"),
        ("wildemount", "EGW"),
        ("taldorei", "TCSR"),
    ]:
        if kw in title_lower:
            return slug
    # Fallback: camel-case abbreviation from first letters, up to 6 chars
    return "".join(w[0] for w in title.split() if w[0].isalpha())[:6].upper()


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
    Falls back to OCR for scanned/image-only PDFs.
    Injects page markers for chapter detection."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages = []
        for i, page in enumerate(doc):
            page_text = page.get_text("text").strip()
            if page_text:
                pages.append(f"--- PAGE {i + 1} ---\n{page_text}")
        doc.close()
        text = "\n\n".join(pages)

        # If pymupdf extracted very little text for many pages, try OCR
        if len(text) < 500 and total_pages > 5:
            ocr_text = _ocr_pymupdf(pdf_path)
            if ocr_text and len(ocr_text) > len(text):
                text = ocr_text

        if len(text) > 500:
            # Fix OCR artifacts before caching — downstream consumers
            # (LLM prompts, chapter extraction) all get clean text.
            text = _fix_ocr_artifacts(text)
            Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
            print(f"  Extracted {len(text):,} chars ({total_pages} pages) via pymupdf → {Path(cache_path).name}")
            return text
    except ImportError:
        pass
    except Exception as e:
        print(f"  pymupdf error: {e}")
    return None


def _ocr_pymupdf(pdf_path: str) -> str | None:
    """Fallback OCR for scanned/image-only PDFs using tesseract."""
    print("  PDF appears scanned — running OCR (this may take a while)...")
    try:
        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(pdf_path)
        total = len(doc)
        all_pages = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            page_text = pytesseract.image_to_string(img).strip()
            if page_text:
                all_pages.append(f"--- PAGE {i + 1} ---\n{page_text}")
            if (i + 1) % 10 == 0:
                print(f"    OCR progress: {i+1}/{total} pages")
        doc.close()
        text = "\n\n".join(all_pages)
        print(f"  OCR complete: {len(text):,} chars from {len(all_pages)} pages")
        return text
    except ImportError as e:
        print(f"  OCR not available: {e}. Install pytesseract + tesseract-ocr.")
        return None
    except Exception as e:
        print(f"  OCR error: {e}")
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
        # Fix OCR artifacts in pdftotext output too
        text = _fix_ocr_artifacts(text)
        Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
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
  without full descriptions. Cantrips are level 0 — ALWAYS include \"level\": 0 for cantrips.
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

CRITICAL — FEATS:
- Feats often have prerequisites. Look for text like "Prerequisite: Dexterity 13 or higher"
  or "Prerequisite: Proficiency with martial weapons" at the start of the feat entry.
|- ALWAYS extract the prerequisite. If there is none, use an empty string "".
|- A feat without a prerequisite field is INCOMPLETE — the field must always be present.

CRITICAL — EQUIPMENT:
|- Equipment items (especially weapons and armor) have cost AND weight in the source.
|- Look for entries like "Cost: 15 gp" and "Weight: 3 lb." in the equipment table.
|- ALWAYS extract cost (as a string like "15 gp") and weight (as a number in pounds).
|- Without cost/weight the equipment data is incomplete for inventory management.
}

CRITICAL — TRAPS:
|- Extract traps with COMPLETE mechanical details: trigger, detection DC/skill, disarm DC/method, effect, save, damage.
|- A trap without a clear trigger, effect, and detection DC is incomplete.
|- The "type" field is one of: "mechanical", "magical", or "hazard".
|- The "danger" field is one of: "setback", "dangerous", or "deadly".
|- Always extract source with page number when determinable.

Traps JSON template:
{
  "name": "Trap Name",
  "type": "mechanical",
  "danger": "dangerous",
  "trigger": "How the trap is triggered",
  "detection": {"dc": 15, "skill": "Perception", "detail": "How to spot it"},
  "disarm": {"dc": 15, "method": "Dexterity (thieves' tools)", "detail": "How to disarm"},
  "effect": "What happens when triggered",
  "save_dc": 15,
  "save_ability": "Dexterity",
  "damage": "22 (4d10)",
  "damage_type": "bludgeoning",
  "area": "Affected area description",
  "description": "Brief 1-2 sentence description of the trap",
  "source": "DMG p.122"
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

DISAMBIGUATION — CRITICAL:
- The text may contain MULTIPLE races or subraces appearing close together.
- ONLY extract traits and ASI for \"{race_name}\" specifically.
- If you see traits from a neighboring race or subrace, DO NOT include them.
- Verify: does each trait you extracted genuinely belong to \"{race_name}\"?

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


# ── Subclass second-pass extraction ──────────────────────────────────────────
# Like the race second-pass: after initial chunk-based extraction, feed the full
# book text with a focused prompt to get ALL features for each subclass.

SUBCLASS_DETAIL_PROMPT = """You are extracting COMPLETE features for a D&D 5e subclass.
Below is text from a sourcebook containing the "{subclass_name}" subclass entry.
Extract EVERY feature at EVERY level — this is the ONLY subclass you need to focus on.

Return ONLY this JSON:
{{
  "name": "{subclass_name}",
  "class": "Parent Class Name",
  "description": "Brief 1-2 sentence description of the subclass",
  "features": [
    {{
      "name": "Feature Name",
      "level": 3,
      "description": "FULL verbatim description text from the source",
      "uses": 1,
      "recharge": "long rest"
    }}
  ]
}}

CRITICAL:
- Extract EVERY feature at EVERY level. Subclasses typically have 4-7 features across
  4-5 levels (e.g. 1st, 2nd, 3rd, 6th, 10th, 14th, 17th).
- Finding only 1-2 features is FAILURE — the subclass entry lists more.
- If the subclass has domain spells, oath spells, or expanded spell lists, extract
  them as a named feature at the level they're gained.
- Copy descriptions VERBATIM. Do not summarize or truncate.
- Include uses/recharge for limited-use features (uses: integer, recharge: "short rest"
  or "long rest"). Omit both for at-will features.
- The "class" field must be the parent class name (e.g. "Cleric", "Paladin", "Wizard").

DISAMBIGUATION — CRITICAL:
- The text may contain MULTIPLE subclasses from the same class appearing close together
  (e.g., Nature Domain next to Knowledge Domain, Light Domain, Tempest Domain, etc.)
- ONLY extract features that belong to \"{subclass_name}\" specifically.
- If you see features from a neighboring subclass (e.g., Warding Flare from Light
  Domain when extracting Nature Domain, or Totem Spirit from Path of the Totem Warrior
  when extracting Path of the Berserker), DO NOT include them.
- Verify: does each feature you extracted genuinely belong to \"{subclass_name}\"?
  If a feature name contains a different subclass name, it's the wrong one.

Text containing the {subclass_name} subclass:
---BEGIN TEXT---
{text}
---END TEXT---

Return ONLY the JSON object (no markdown, no explanation)."""


def _extract_subclass_details(subclass_name: str, parent_class: str, full_text: str) -> dict | None:
    """Second-pass extraction: find the subclass entry in the full text and extract
    ALL features with a focused prompt."""
    import re as _re

    # Find the subclass section in the text (class name + subclass name)
    # Strategy: scan for parent class section then the subclass name within it
    text_lower = full_text.lower()
    sc_lower = subclass_name.lower()

    # Try multiple match strategies
    matches = []
    for m in _re.finditer(_re.escape(sc_lower), text_lower):
        matches.append((m.start(), m.group()))

    if not matches:
        print(f"      ⚠ Subclass '{subclass_name}' not found in text")
        return None

    # Use the best match (prefer matches near the parent class name)
    best_idx = 0
    parent_lower = parent_class.lower()
    best_dist = float("inf")
    for i, (pos, _) in enumerate(matches):
        # Search for parent class within 2000 chars before this match
        search_start = max(0, pos - 2000)
        search_text = text_lower[search_start:pos]
        parent_pos = search_text.rfind(parent_lower)
        if parent_pos >= 0 and (pos - (search_start + parent_pos)) < best_dist:
            best_dist = pos - (search_start + parent_pos)
            best_idx = i

    pos = matches[best_idx][0]

    # Extract a 12000-char window around the match
    start = max(0, pos - 1500)
    end = min(len(full_text), pos + 10500)
    context = full_text[start:end]

    print(f"      Subclass second-pass: {subclass_name} ({len(context):,} chars) → ", end="", flush=True)

    prompt = SUBCLASS_DETAIL_PROMPT.format(subclass_name=subclass_name, text=context)
    raw = _call_llm(prompt)

    if not raw:
        print("FAILED")
        return None

    result = _extract_json(raw)
    if result:
        n_features = len(result.get("features", []))
        print(f"{n_features} features")
    else:
        print("NO JSON")
    return result


def _merge_subclass_details(subclass: dict, details: dict | None) -> dict:
    """Merge second-pass subclass details into the subclass entry.
    Details override the original where they have more features."""
    if not details:
        return subclass

    # Description: take if details has a longer one
    if details.get("description") and len(details.get("description", "")) > len(subclass.get("description", "")):
        subclass["description"] = details["description"]

    # Features: replace if details has more features
    orig_features = subclass.get("features", [])
    new_features = details.get("features", [])
    if new_features and len(new_features) > len(orig_features):
        subclass["features"] = new_features

    # Parent class: take if missing in original
    if details.get("class") and not subclass.get("class"):
        subclass["class"] = details["class"]

    return subclass


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
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
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


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-contamination guard — feature → correct subclass mapping
# ═══════════════════════════════════════════════════════════════════════════════

# Maps feature names to their CORRECT subclass. Any feature appearing in a
# different subclass will be stripped during validation. Covers the most common
# LLM cross-contamination patterns (adjacent subclasses of the same class).
_FEATURE_OWNER: dict[str, str] = {
    # Cleric domains (PHB)
    "warding flare": "Light Domain",
    "radiance of the dawn": "Light Domain",
    "improved flare": "Light Domain",
    "corona of light": "Light Domain",
    "acolyte of nature": "Nature Domain",
    "charm animals and plants": "Nature Domain",
    "dampen elements": "Nature Domain",
    "master of nature": "Nature Domain",
    "blessings of knowledge": "Knowledge Domain",
    "knowledge of the ages": "Knowledge Domain",
    "read thoughts": "Knowledge Domain",
    "visions of the past": "Knowledge Domain",
    "wrath of the storm": "Tempest Domain",
    "destructive wrath": "Tempest Domain",
    "thunderbolt strike": "Tempest Domain",
    "stormborn": "Tempest Domain",
    "blessing of the trickster": "Trickery Domain",
    "invoke duplicity": "Trickery Domain",
    "cloak of shadows": "Trickery Domain",
    "improved duplicity": "Trickery Domain",
    "war priest": "War Domain",
    "guided strike": "War Domain",
    "war god's blessing": "War Domain",
    "avatar of battle": "War Domain",
    # Barbarian paths (PHB)
    "frenzy": "Path of the Berserker",
    "mindless rage": "Path of the Berserker",
    "intimidating presence": "Path of the Berserker",
    "retaliation": "Path of the Berserker",
    "totem spirit": "Path of the Totem Warrior",
    "aspect of the beast": "Path of the Totem Warrior",
    "spirit walker": "Path of the Totem Warrior",
    "totemic attunement": "Path of the Totem Warrior",
    # Wizard schools — features that clearly identify their school
    "arcane ward": "School of Abjuration",
    "projected ward": "School of Abjuration",
    "improved abjuration": "School of Abjuration",
    "spell resistance": "School of Abjuration",
    "minor conjuration": "School of Conjuration",
    "benign transposition": "School of Conjuration",
    "focused conjuration": "School of Conjuration",
    "durable summons": "School of Conjuration",
    "portent": "School of Divination",
    "expert divination": "School of Divination",
    "the third eye": "School of Divination",
    "greater portent": "School of Divination",
    "hypnotic gaze": "School of Enchantment",
    "instinctive charm": "School of Enchantment",
    "split enchantment": "School of Enchantment",
    "alter memories": "School of Enchantment",
    "sculpt spells": "School of Evocation",
    "potent cantrip": "School of Evocation",
    "empowered evocation": "School of Evocation",
    "overchannel": "School of Evocation",
    "improved minor illusion": "School of Illusion",
    "malleable illusions": "School of Illusion",
    "illusory self": "School of Illusion",
    "illusory reality": "School of Illusion",
    "grim harvest": "School of Necromancy",
    "undead thralls": "School of Necromancy",
    "inured to undeath": "School of Necromancy",
    "command undead": "School of Necromancy",
    "minor alchemy": "School of Transmutation",
    "transmuter's stone": "School of Transmutation",
    "shapechanger": "School of Transmutation",
    "master transmuter": "School of Transmutation",
}


def _strip_wrong_features(subclass_name: str, parent_class: str, features: list) -> list:
    """Remove features that clearly belong to a different subclass of the same class.
    Uses _FEATURE_OWNER mapping + heuristic checks."""
    if not features:
        return features

    cleaned = []
    sc_lower = subclass_name.lower()
    removed = []

    for feat in features:
        if not isinstance(feat, dict):
            continue
        fname = feat.get("name", "").lower()

        # Check: does this feature belong to a DIFFERENT subclass?
        owner = _FEATURE_OWNER.get(fname)
        if owner and owner.lower() != sc_lower:
            removed.append(feat.get("name", fname))
            continue

        # Heuristic: feature name contains a different domain/path/school name
        # e.g., "Channel Divinity: Radiance of the Dawn" in Nature Domain
        if "channel divinity:" in fname and parent_class.lower() == "cleric":
            # Known cleric CD options — check if they match any domain
            cd_check = fname.replace("channel divinity:", "").strip()
            cd_owner = _FEATURE_OWNER.get(cd_check)
            if cd_owner and cd_owner.lower() != sc_lower:
                removed.append(feat.get("name", fname))
                continue

        cleaned.append(feat)

    if removed:
        print(f"      ⚠ Stripped {len(removed)} cross-contaminated feature(s) from {subclass_name}: {removed}")

    return cleaned


def validate_extraction(data: dict, book_slug: str) -> dict:
    """Post-process and validate extracted data. Removes invalid entries."""
    cleaned = {
        "races": [], "spells": [], "magic_items": [], "equipment": [],
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
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
        # Validate XP matches CR per the 5e XP table
        cr = m.get("challenge_rating", "")
        xp = m.get("xp", 0)
        _XP_TABLE = {
            "0": 10, "1/8": 25, "1/4": 50, "1/2": 100, "1": 200, "2": 450,
            "3": 700, "4": 1100, "5": 1800, "6": 2300, "7": 2900, "8": 3900,
            "9": 5000, "10": 5900, "11": 7200, "12": 8400, "13": 10000,
            "14": 11500, "15": 13000, "16": 15000, "17": 18000, "18": 20000,
            "19": 22000, "20": 25000, "21": 33000, "22": 41000, "23": 50000,
            "24": 62000, "25": 75000, "26": 90000, "27": 105000, "28": 120000,
            "29": 135000, "30": 155000,
        }
        if cr and xp and str(cr) in _XP_TABLE:
            expected = _XP_TABLE[str(cr)]
            ratio = xp / expected if expected else 0
            if ratio < 0.5 or ratio > 2.0:
                issues.append(f"Monster {m['name']}: XP ({xp}) doesn't match CR {cr} (expected {expected})")
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
        # Validate prerequisite field presence (LLM sometimes omits it)
        prereq = f.get("prerequisite")
        if prereq is None:
            issues.append(f"Feat {f['name']}: missing prerequisite field (added empty)")
            f["prerequisite"] = ""
        f.setdefault("source", book_slug)
        cleaned["feats"].append(f)

    # ── Backgrounds ──
    for bg in data.get("backgrounds", []):
        bg = _ensure_dict(bg)
        if not bg or not bg.get("name"):
            continue
        # Validate skill proficiencies against known D&D skills
        raw_skills = bg.get("skill_proficiencies", [])
        if raw_skills:
            cleaned_skills = [s for s in raw_skills if s in VALID_SKILLS]
            invalid = len(raw_skills) - len(cleaned_skills)
            if invalid:
                bad = [s for s in raw_skills if s not in VALID_SKILLS]
                issues.append(f"Background {bg['name']}: {invalid} unknown skill(s) stripped: {bad}")
            bg["skill_proficiencies"] = cleaned_skills
        bg.setdefault("source", book_slug)
        cleaned["backgrounds"].append(bg)

    # ── Subclasses ──
    for sc in data.get("subclasses", []):
        sc = _ensure_dict(sc)
        if not sc or not sc.get("name"):
            continue
        sc.setdefault("source", book_slug)
        # Strip features that belong to a different subclass (cross-contamination guard)
        sc["features"] = _strip_wrong_features(sc.get("name", ""), sc.get("class", ""), sc.get("features", []))
        # Flag subclasses with unusually few features (likely incomplete extraction)
        feat_count = len(sc.get("features", []))
        if feat_count < 3 and feat_count > 0:
            issues.append(f"Subclass {sc['name']}: only {feat_count} features — may be incomplete")
        cleaned["subclasses"].append(sc)

    # ── Traps ──
    for t in data.get("traps", []):
        t = _ensure_dict(t)
        if not t or not t.get("name"):
            continue
        # Validate required fields for traps
        if not t.get("trigger"):
            issues.append(f"Trap {t['name']}: missing trigger")
        if not t.get("effect") and not t.get("damage"):
            issues.append(f"Trap {t['name']}: missing effect and damage (may be incomplete)")
        # Validate danger level
        valid_dangers = {"setback", "dangerous", "deadly"}
        if t.get("danger") and t["danger"].lower() not in valid_dangers:
            issues.append(f"Trap {t['name']}: unknown danger={t.get('danger')}")
        # Validate type
        valid_types = {"mechanical", "magical", "hazard"}
        if t.get("type") and t["type"].lower() not in valid_types:
            issues.append(f"Trap {t['name']}: unknown type={t.get('type')}")
        t.setdefault("source", book_slug)
        cleaned["traps"].append(t)

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
        "traps": ["trigger", "effect", "damage", "save_dc"],
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
                                  "monsters", "npcs", "feats", "backgrounds",
                                  "subclasses", "traps"]}

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
    """Normalize for dedup: lowercase, normalize dashes,
    strip trailing 's' (singular/plural), remove punctuation.
    Parentheticals are NOT stripped — handled by dedup logic at a higher level
    so "Criminal" and "Criminal (Myriad Operative)" stay distinct."""
    n = name.lower().strip()
    # Strip leading "the " for dedup: "The Great Old One" ≈ "Great Old One"
    if n.startswith('the '):
        n = n[4:]
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
                # False-positive guard: if current name is plain (no parenthetical)
                # and every matching known entry HAS a parenthetical, keep it.
                # e.g. "Criminal" (WGE) is distinct from "Criminal (Myriad Operative)" (EGW)
                if '(' not in name:
                    matching_known = [kn for kn in known if _normalize_name(kn) == name_norm]
                    if matching_known and all('(' in kn for kn in matching_known):
                        pass  # false positive — keep as a distinct plain-name entry
                    else:
                        total_removed += 1
                        continue
                else:
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
        page_texts = _split_by_page(text)
        for ch in chapters:
            ch["text"] = _extract_chapter_text(page_texts, ch["start_page"], ch["end_page"])
        chapters = [ch for ch in chapters if ch.get("text") and len(ch["text"]) >= 100]
        if chapters:
            print(f"  {len(chapters)} chapter(s) from text markers retained")
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
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
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
        "traps": existing.get("traps", []),
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
        base_accumulated['races'] = races_found

    # 5b. Subclass second-pass extraction (catches features split across chunks)
    subclasses_found = base_accumulated.get('subclasses', [])
    if subclasses_found and text:
        print(f'\n  Subclass detail extraction for {len(subclasses_found)} subclass(es)...')
        for i, sc in enumerate(subclasses_found):
            sc_name = sc.get('name', '')
            sc_class = sc.get('class', '')
            if not sc_name:
                continue
            details = _extract_subclass_details(sc_name, sc_class, text)
            if details:
                subclasses_found[i] = _merge_subclass_details(sc, details)
        base_accumulated['subclasses'] = subclasses_found

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
        "monsters": [], "npcs": [], "feats": [], "backgrounds": [],
        "subclasses": [], "traps": [],
    }

    seen = {cat: set() for cat in merged}         # _normalize_name (keeps parens)
    seen_core = {cat: set() for cat in merged}     # stripped of parens (for Fireball ≈ Fireball (5th-level))
    raw_names = {cat: set() for cat in merged}     # track actual names for source reporting
    sources = {cat: {} for cat in merged}

    for ext_file in sorted(CACHE_DIR.glob("*_extracted.json")):
        data = _load_json(ext_file)
        if not isinstance(data, dict) or not data.get("_completed"):
            continue

        slug = data.get("_book_slug", ext_file.stem.replace("_extracted", ""))
        for cat in merged:
            for item in data.get(cat, []):
                name = item.get("name", "")
                name_norm = _normalize_name(name)       # keeps parentheticals
                name_core = re.sub(r'\s*\([^)]*\)', '', name.lower()).strip()  # strips them
                if not name_norm:
                    continue

                # Exact normalized match (keeps parens) → real duplicate
                if name_norm in seen[cat]:
                    # False-positive guard: plain name (no paren) matching only
                    # variant entries like "Criminal" vs "Criminal (Myriad Operative)"
                    if '(' not in name:
                        # Check if a plain-core version already exists
                        if name_core in seen_core[cat]:
                            continue  # real duplicate: plain name already exists
                        # else: only variants exist → keep plain name
                    else:
                        continue  # current has parens → real duplicate

                # Tertiary check: decorated name matching a plain core entry
                # e.g. "Fireball (5th-level)" from a module matching PHB's "Fireball"
                if '(' in name and name_core in seen_core[cat]:
                    continue

                seen[cat].add(name_norm)
                seen_core[cat].add(name_core)
                raw_names[cat].add(name.lower())
                item["_source_manual"] = slug  # Record which PDF this came from
                merged[cat].append(item)
                sources[cat][name_norm] = slug

    # ── Preserve existing data for categories with no new extractions ──
    # If a category has no entries from PDF extractions but a file already
    # exists on disk, load it to prevent accidental data loss (e.g. traps
    # that were created outside the ingestion pipeline).
    for cat in list(merged.keys()):
        if not merged[cat]:
            existing_path = OUTPUT_DIR / f"{cat}.json"
            if existing_path.exists():
                existing_data = _load_json(existing_path)
                if isinstance(existing_data, list) and existing_data:
                    merged[cat] = existing_data
                    for item in existing_data:
                        name = item.get("name", "")
                        name_norm = _normalize_name(name)
                        if name_norm:
                            seen[cat].add(name_norm)
                    print(f"  Preserved {len(existing_data)} existing {cat} entries (no new extractions)")

    # ── Spell classes cross-reference: if extracted spells lack classes,
    #     backfill from SRD cache (which has per-class spell lists) ────────
    srd_spells = _load_json_list(HERE / 'data' / 'srd_cache' / 'spells.json')
    if srd_spells:
        srd_by_name: dict[str, list[str]] = {}
        for ss in srd_spells:
            sname = ss.get('name', '').lower()
            sclasses = [c.get('name', '') for c in ss.get('classes', [])]
            if sname and sclasses:
                srd_by_name[sname] = sclasses

        backfilled = 0
        for spell in merged['spells']:
            if spell.get('classes'):
                continue  # Already has classes
            key = spell.get('name', '').lower()
            if key in srd_by_name:
                spell['classes'] = srd_by_name[key]
                backfilled += 1
        if backfilled:
            print(f'  Backfilled classes for {backfilled} spell(s) from SRD')

    # ── Source normalization (inline during merge) ──────────────────────
    _normalize_merged_sources(merged)
    
    # Save merged files
    for cat, items in merged.items():
        path = OUTPUT_DIR / f"{cat}.json"
        _save_json(path, items)

    # ── Build pdf_map for downstream tooling ────────────────────────────
    pdf_map = _build_pdf_map()

    # Save metadata
    meta = {
        "merged_at": time.time(),
        "source_manuals": list(set(
            data.get("_book_slug", "")
            for ext_file in sorted(CACHE_DIR.glob("*_extracted.json"))
            if (data := _load_json(ext_file)) and data.get("_completed")
        )),
        "totals": {cat: len(items) for cat, items in merged.items()},
        "pdf_map": pdf_map,
    }
    _save_json(OUTPUT_DIR / "_meta.json", meta)

    print(f"\nMerged data written to {OUTPUT_DIR}/")
    for cat, items in sorted(merged.items()):
        if items:
            print(f"  {cat}.json: {len(items)} entries")

    # ── Post-merge fixups: clean garbled data, fill known gaps ───────────
    _apply_post_merge_fixups(OUTPUT_DIR)


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


# ── Source normalization helpers ───────────────────────────────────────

def _normalize_merged_sources(merged: dict) -> None:
    """Normalize source strings to (Manual, pg#) format during merge."""
    import re

    # Complete slug → display name map (must mirror _get_source_slug_map in main.py)
    book_title_map = {
        "AIPG": "Adventures in Middle-earth Player's Guide",
        "AW": "Ancestral Weapons", "BLRG": "Bree-land Region Guide",
        "CC": "Creature Codex", "CSF": "Courts of the Shadow Fey",
        "CotN": "Call of the Netherdeep", "DD": "Dues for the Dead",
        "DDP": "Defiance in Phlan", "DMG": "Dungeon Master's Guide",
        "DPM": "Deep Magic: Elven High Magic", "DPM1": "Deep Magic: Ley Lines",
        "DTCOE": "Tasha's Cauldron of Everything", "EBT": "Book of Ebon Tides",
        "EEPC": "Elemental Evil Player's Companion", "EGW": "Explorer's Guide to Wildemount",
        "EIA": "Encounters in Avernus", "EREA": "Erebor Adventures",
        "ERIA": "Eriador Adventures", "ETR": "Expanding the Ranger",
        "GGR": "Guildmasters' Guide to Ravnica", "GoS": "Ghosts of Saltmarsh",
        "HotDQ": "Hoard of the Dragon Queen", "KW": "Kobold Quarterly 20",
        "LMG": "Adventures in Middle-earth Loremaster's Guide",
        "LMRG": "Lonely Mountain Region Guide", "LMoP": "Lost Mine of Phandelver",
        "MM": "Monster Manual", "MOM": "Marauders of the Margreve",
        "MPG": "Margreve Player's Guide", "MTF": "Mordenkainen's Tome of Foes",
        "MWC": "Mirkwood Campaign", "PHB": "Player's Handbook",
        "RAT": "Ratatosk", "RGEO": "The Road Goes Ever On",
        "RRG": "Rhovanion Region Guide", "RVR": "Rivendell Region Guide",
        "RoT": "The Rise of Tiamat", "SCAG": "Sword Coast Adventurer's Guide",
        "SDQ": "Shadows of the Dusk Queen", "SME": "Saltmarsh Encounters",
        "SOM": "Shadows over the Moonsea", "SSK": "Secrets of Sokol Keep",
        "TCE": "Tasha's Cauldron of Everything", "TCSR": "Tal'Dorei Campaign Setting Reborn",
        "TFS": "Tales from the Shadows", "TLT": "The Tortured Land",
        "TMFRV": "Tales of the Margreve", "TTP": "The Tortle Package",
        "ToA": "Tomb of Annihilation", "VGM": "Volo's Guide to Monsters",
        "W": "Wrath of the Bramble King", "W1": "Pride of the Mushroom Queen",
        "W2": "Warlock 7", "W3": "Warlock 17", "W4": "Warlock 22: Druids",
        "W5": "Warlock 32", "W6": "Warlock 34", "W7": "Warlock Bestiary",
        "W8": "Warlock Lair: The Returners' Tower", "W9": "Warlock Lair: The Dark Aerie",
        "WDH": "Waterdeep: Dragon Heist", "WGE": "Wayfinder's Guide to Eberron",
        "WLA": "Wilderland Adventures", "WLL": "Warlock Lairs: Into the Wilds",
        "WRKF": "Wrath of the River King", "WS": "Shadows Envy",
        "WSC": "The Wild Sheep Chase", "XGE": "Xanathar's Guide to Everything",
        "BGDIA": "Baldur's Gate: Descent into Avernus",
    }
    title_lookup = {k.upper(): v for k, v in book_title_map.items()}
    
    # Garbage patterns — sources that should be dropped
    garbage_prefixes = [
        "unknown", "introductory text", "homebrew", "fragment",
        "james larsen", "text provided", "page not determinable",
        "sourcebook (page", "adventure p.?", "unknown page",
    ]
    
    page_re = re.compile(r'(?:p\.?\s*|page\s+)(\d+)', re.IGNORECASE)
    
    fixed = 0
    for cat, items in merged.items():
        for item in items:
            src = (item.get("source") or "").strip()
            slug = (item.get("_source_manual") or "").strip()
            
            display = title_lookup.get(slug.upper()) if slug else None
            
            # Extract page number if present
            page = None
            if src:
                m = page_re.search(src)
                if m:
                    page = m.group(1)
            
            # Check if source is garbage
            is_garbage = False
            if src:
                sl = src.lower()
                is_garbage = any(sl.startswith(g) or g in sl for g in garbage_prefixes)
            
            if display and page:
                item["source"] = f"({display}, p.{page})"
                fixed += 1
            elif display and not is_garbage and src and src.startswith("("):
                pass  # Already formatted, keep as-is
            elif display:
                item["source"] = f"({display})"
                fixed += 1
            elif is_garbage or not src:
                item["source"] = ""
                fixed += 1
            # else: no slug, no page, keep as-is but flag
    
    if fixed:
        print(f"  Normalized {fixed} source string(s) to (Manual, pg#) format")


def _apply_post_merge_fixups(output_dir: Path) -> None:
    """Apply data quality fixups to merged files after each merge.
    
    These fill known gaps (missing costs, garbled entries, race ASIs, etc.)
    that the LLM extraction can't reliably produce from PDF text.
    Edit the fixup data below as new gaps are discovered.
    """
    import re
    
    # ── Equipment: remove garbled entries, fill known costs ──────────
    equip_path = output_dir / "equipment.json"
    if equip_path.exists():
        equipment = _load_json(equip_path)
        if isinstance(equipment, list):
            # Remove garbled entries (weapon properties extracted as items)
            garbled_patterns = ["ammunition (range", "unarmed strike"]
            before = len(equipment)
            equipment = [e for e in equipment 
                        if not any(p in e.get("name", "").lower() for p in garbled_patterns)]
            removed = before - len(equipment)
            
            # Known equipment costs (not reliably extractable from PDFs)
            known_costs = {
                # DMG siege weapons
                "ballista": "50 gp", "cannon": "500 gp", "cauldron, suspended": "150 gp",
                "mangonel": "100 gp", "ram": "100 gp", "siege tower": "1500 gp", "trebuchet": "500 gp",
                "bomb": "150 gp", "gunpowder": "100 gp", "dynamite": "150 gp",
                "grenade, fragmentation": "200 gp", "grenade, smoke": "100 gp",
                # PHB armor
                "padded": "5 gp", "leather": "10 gp", "studded leather": "45 gp",
                "arcane focus": "5 gp", "druidic focus": "5 gp", "holy symbol": "5 gp",
                "potion of healing": "50 gp", "bottle": "2 gp",
                # SCAG instruments
                "birdpipes": "12 gp", "glaur": "3 gp", "hand drum": "6 gp",
                "longhorn": "6 gp", "songhorn": "6 gp", "tantan": "6 gp",
                "thelarr": "3 gp", "tocken": "6 gp", "wargong": "3 gp",
                "yarting": "3 gp", "zulkoon": "3 gp",
            }
            cost_filled = 0
            for e in equipment:
                name = e.get("name", "").lower()
                if (not e.get("cost") or e["cost"] == "") and name in known_costs:
                    e["cost"] = known_costs[name]
                    cost_filled += 1
                elif not e.get("cost") or e["cost"] == "":
                    e["cost"] = "\u2014"  # em dash for "not purchasable"
            
            _save_json(equip_path, equipment)
            if removed or cost_filled:
                print(f"  Equipment fixups: {removed} garbled removed, {cost_filled} costs filled")
    
    # ── Spells: fix known corrupt names ─────────────────────────────
    spells_path = output_dir / "spells.json"
    if spells_path.exists():
        spells = _load_json(spells_path)
        if isinstance(spells, list):
            fixed = 0
            for s in spells:
                if s.get("name", "").lower() == "jlaming sphere":
                    s["name"] = "Flaming Sphere"
                    s["school"] = "evocation"
                    fixed += 1
            if fixed:
                _save_json(spells_path, spells)
                print(f"  Spell fixups: {fixed} corrupt name(s) corrected")
    
    # ── Races: fill known missing ASI/traits ─────────────────────────
    races_path = output_dir / "races.json"
    if races_path.exists():
        races = _load_json(races_path)
        if isinstance(races, list):
            fixed = 0
            for r in races:
                name = r.get("name", "")
                if name == "Windrunner Elf":
                    if not r.get("asi") or len(r.get("asi", {})) == 0:
                        r["asi"] = {"dexterity": 2, "wisdom": 1}
                        fixed += 1
                    if not r.get("traits") or len(r.get("traits", [])) == 0:
                        r["traits"] = [{"name": "Fleet of Foot"}, {"name": "Mask of the Wild"}]
                if name == "Tlincalli":
                    if not r.get("traits") or len(r.get("traits", [])) == 0:
                        r["traits"] = [{"name": "Natural Armor"}]
                        fixed += 1
            if fixed:
                _save_json(races_path, races)
                print(f"  Race fixups: {fixed} gap(s) filled")
    
    # ── Feats: fix OCR-garbled PHB descriptions ──────────────────────
    feats_path = output_dir / "feats.json"
    if feats_path.exists():
        feats = _load_json(feats_path)
        if isinstance(feats, list):
            _feat_fixes = {
                "SHARPSHOOTER": {
                    "name": "Sharpshooter", "prerequisite": "",
                    "description": (
                        "You have mastered ranged weapons and can make shots that "
                        "others find impossible. You gain the following benefits:\n"
                        "• Attacking at long range doesn't impose disadvantage on "
                        "your ranged weapon attack rolls.\n"
                        "• Your ranged weapon attacks ignore half cover and "
                        "three-quarters cover.\n"
                        "• Before you make an attack with a ranged weapon that you "
                        "are proficient with, you can choose to take a -5 penalty "
                        "to the attack roll. If the attack hits, you add +10 to "
                        "the attack's damage."
                    ),
                    "source": "PHB 2014 p.170", "_source_manual": "PHB",
                },
                "SPELL SNIPER": {
                    "name": "Spell Sniper",
                    "prerequisite": "The ability to cast at least one spell",
                    "description": (
                        "You have learned techniques to enhance your attacks with "
                        "certain kinds of spells, gaining the following benefits:\n"
                        "• When you cast a spell that requires you to make an "
                        "attack roll, the spell's range is doubled.\n"
                        "• Your ranged spell attacks ignore half cover and "
                        "three-quarters cover.\n"
                        "• You learn one cantrip that requires an attack roll. "
                        "Choose the cantrip from the bard, cleric, druid, sorcerer, "
                        "warlock, or wizard spell list. Your spellcasting ability "
                        "for this cantrip depends on the spell list you chose from: "
                        "Charisma for bard, sorcerer, or warlock; Wisdom for cleric "
                        "or druid; or Intelligence for wizard."
                    ),
                    "source": "PHB 2014 p.170", "_source_manual": "PHB",
                },
                "Crossbow Expert": {
                    "name": "Crossbow Expert", "prerequisite": "",
                    "description": (
                        "Thanks to extensive practice with the crossbow, you gain "
                        "the following benefits:\n"
                        "• You ignore the loading quality of crossbows with which "
                        "you are proficient.\n"
                        "• Being within 5 feet of a hostile creature doesn't impose "
                        "disadvantage on your ranged attack rolls.\n"
                        "• When you use the Attack action and attack with a "
                        "one-handed weapon, you can use a bonus action to attack "
                        "with a hand crossbow you are holding."
                    ),
                    "source": "PHB 2014 p.166", "_source_manual": "PHB",
                },
            }
            fixed = 0
            for i, f in enumerate(feats):
                name = f.get("name", "")
                # Match both OCR (ALL CAPS) and clean (Title Case) versions
                fix = _feat_fixes.get(name) or _feat_fixes.get(name.upper())
                if fix:
                    feats[i] = fix
                    fixed += 1
            if fixed:
                _save_json(feats_path, feats)
                print(f"  Feat fixups: {fixed} OCR-garbled entry(s) replaced")

    # ── Magic items: fill missing rarities ──────────────────────────
    items_path = output_dir / "magic_items.json"
    if items_path.exists():
        items = _load_json(items_path)
        if isinstance(items, list):
            fixed = 0
            for i in items:
                if not i.get("rarity") or i["rarity"] == "":
                    i["rarity"] = "varies"
                    fixed += 1
            if fixed:
                _save_json(items_path, items)
                print(f"  Magic item fixups: {fixed} rarity gap(s) filled")

    # ── Cross-reference validation ──────────────────────────────────
    # Check subclass→class references, spell→class references for known class names
    _xref_known_classes = {
        "Barbarian","Bard","Cleric","Druid","Fighter","Monk","Paladin",
        "Ranger","Rogue","Sorcerer","Warlock","Wizard",
        # AiME / homebrew classes
        "Scholar","Slayer","Warden","Troubadour","Marauder",
        "Spirit Dancer","Skald","Treasure Hunter",
    }
    _xref_issues = 0
    # Subclasses: check parent class exists
    _sc_path = output_dir / "subclasses.json"
    if _sc_path.exists():
        for _sc in _load_json(_sc_path):
            _paren = _sc.get("class", "")
            if _paren and _paren not in _xref_known_classes:
                _xref_issues += 1
                if _xref_issues <= 5:
                    print(f"  ⚠ Cross-ref: subclass '{_sc.get('name','?')}' references unknown class '{_paren}'")
    # Spells: check class references are valid
    _spell_path = output_dir / "spells.json"
    if _spell_path.exists():
        for _s in _load_json(_spell_path):
            for _c in _s.get("classes", []):
                if isinstance(_c, dict):
                    _c = _c.get("name", "")
                if _c and _c not in _xref_known_classes:
                    _xref_issues += 1
                    if _xref_issues <= 5:
                        print(f"  ⚠ Cross-ref: spell '{_s.get('name','?')}' references unknown class '{_c}'")
    if _xref_issues:
        print(f"  ({_xref_issues} total cross-reference issues flagged)")


def _build_pdf_map() -> dict:
    """Build a mapping of book slug → display name + PDF path for downstream tooling."""
    manuals = discover_manuals()
    pdf_map = {}
    for m in manuals:
        pdf_map[m["slug"]] = {
            "title": m["title"],
            "filename": m["filename"],
            "path": m["path"],
        }
    return pdf_map


if __name__ == "__main__":
    import subprocess  # used in extract_text
    main()
