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

# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

HERE = Path(__file__).parent
MANUALS_DIR = Path("/media/james/SlowDisk1tb/home-move/DnD-Manuals")
CACHE_DIR = HERE / "data" / "manual_cache"
OUTPUT_DIR = HERE / "data" / "manual_data"
STATE_FILE = HERE / "data" / "ingest_state.json"

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
SKIP_EXTRACTION = {"PHB", "DMG", "MM"}  # SRD covers these

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

    # Use cache if valid
    if cache_path.exists():
        pdf_mtime = pdf_path.stat().st_mtime
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= pdf_mtime:
            text = cache_path.read_text(encoding="utf-8", errors="replace")
            print(f"  Text cached ({len(text):,} chars)")
            return text

    print(f"  Extracting text...")
    text = _extract_pymupdf(str(pdf_path), str(cache_path))
    if not text:
        text = _extract_pdftotext(str(pdf_path), str(cache_path))
    if not text:
        return None
    return text


def _extract_pymupdf(pdf_path: str, cache_path: str) -> str | None:
    """Try pymupdf (fitz) for better multi-column text extraction."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n"
        doc.close()
        if len(text) > 500:
            Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
            print(f"  Extracted {len(text):,} chars via pymupdf → {Path(cache_path).name}")
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
    "traits": [{"name": "Trait Name", "description": "Full trait text"}],
    "subraces": [{"name": "Subrace Name", "asi": {}, "traits": [], "description": "..."}],
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
    "features": [{"name": "Feature Name", "level": 3, "description": "..."}],
    "source": "XGE p.50"
  }]
}

Rules:
1. Extract EVERY instance — do NOT summarize or skip any that match the criteria above.
2. Copy descriptions VERBATIM from the source. Paraphrasing = failure.
3. Ability score keys: "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma".
4. Challenge ratings as fractions: "1/4", "1/2", "2", "17".
5. If a monster stat block or race entry spans multiple pages/chunks, extract what IS complete in this chunk.
6. Skip table-of-contents, index entries, page headers — only real game content.
7. If unsure between extracting or skipping: EXTRACT. We filter quality later.

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
            traits_summary.append({"name": name, "description": desc[:300]})

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
        r.setdefault("source", book_slug)
        cleaned["races"].append(r)

    # ── Spells ──
    for s in data.get("spells", []):
        s = _ensure_dict(s)
        if not s or not s.get("name"):
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
        eq.setdefault("source", book_slug)
        cleaned["equipment"].append(eq)

    # ── Monsters ──
    for m in data.get("monsters", []):
        m = _ensure_dict(m)
        if not m or not m.get("name"):
            continue
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
    """Normalize for dedup: lowercase, strip trailing 's' (singular/plural)."""
    n = name.lower().strip()
    # Strip trailing 's' for plural dedup (Geonid/Geonids, Cannibal/Cannibals)
    if n.endswith('s') and not n.endswith('ss') and len(n) > 4:
        n = n[:-1]
    return n


def dedup_extraction(data: dict, base_names: dict[str, set]) -> dict:
    """Remove entries whose name already exists in base data. Uses fuzzy matching."""
    new_data = {}
    total_removed = 0

    for category in data:
        items = data[category]
        known = base_names.get(category, set())
        # Also build normalized-version set for fuzzy matching
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


# ═══════════════════════════════════════════════════════════════════════════════
# Main extraction pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def process_manual(manual: dict) -> dict | None:
    """Full extraction pipeline for one manual."""
    slug = manual["slug"]
    print(f"\n{'='*60}")
    print(f"Processing: {manual['title']} ({slug})")
    print(f"  PDF: {manual['path']} ({manual['size_kb']} KB)")

    if slug in SKIP_EXTRACTION:
        print(f"  SKIPPED: {slug} is covered by SRD base data")
        # Still cache text for search
        extract_text(manual)
        return None

    # 1. Extract text
    text = extract_text(manual)
    if not text:
        return None

    # 2. Chunk
    chunks = chunk_text(text)
    print(f"  Split into {len(chunks)} chunks")

    # 3. Check for existing partial extraction
    extracted_path = CACHE_DIR / f"{slug}_extracted.json"
    existing = _load_json(extracted_path) if extracted_path.exists() else {}
    if not isinstance(existing, dict):
        existing = {}

    # Accumulate results
    accumulated = {
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

    # 4. Extract from each chunk
    base_names = _load_base_names()
    processed = existing.get("_chunks_processed", [])

    for chunk in chunks:
        if chunk["index"] in processed:
            print(f"    Chunk {chunk['index']}: already processed, skipping")
            continue

        result = extract_from_chunk(chunk, slug)

        # Accumulate
        for cat in accumulated:
            if cat in result and result[cat]:
                accumulated[cat].extend(result[cat])

        processed.append(chunk["index"])

        # Save intermediate progress after each chunk
        accumulated["_chunks_processed"] = processed
        accumulated["_last_chunk"] = chunk["index"]
        accumulated["_total_chunks"] = len(chunks)
        accumulated["_book_slug"] = slug
        accumulated["_book_title"] = manual["title"]
        _save_json(extracted_path, accumulated)

        # Brief pause between chunks to avoid rate limits
        time.sleep(0.5)

    # 5. Race second-pass extraction
    races_found = accumulated.get("races", [])
    if races_found and text:
        print(f"\n  Race detail extraction for {len(races_found)} race(s)...")
        for i, race in enumerate(races_found):
            race_name = race.get("name", "")
            if not race_name:
                continue
            details = _extract_race_details(race_name, text)
            if details:
                races_found[i] = _merge_race_details(race, details)
        accumulated["races"] = races_found

    # 6. Validate
    validated = validate_extraction(accumulated, slug)

    # 7. Dedup
    new_data = dedup_extraction(validated, base_names)

    # 8. Trait effects auto-wiring
    races_to_wire = new_data.get("races", [])
    if races_to_wire:
        print(f"\n  Auto-wiring trait effects for {len(races_to_wire)} race(s)...")
        for race in races_to_wire:
            wired = _wire_trait_effects(race)
            if wired:
                race["_effects"] = wired

    # 9. Final save
    final = {**new_data,
             "_chunks_processed": processed,
             "_total_chunks": len(chunks),
             "_book_slug": slug,
             "_book_title": manual["title"],
             "_completed": True,
             "_timestamp": time.time()}
    _save_json(extracted_path, final)

    # 8. Report
    total_new = sum(len(v) for v in new_data.values())
    print(f"\n  ✓ Extraction complete: {total_new} new entries")
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
    sources = {cat: {} for cat in merged}  # track which book each entry came from

    for ext_file in sorted(CACHE_DIR.glob("*_extracted.json")):
        data = _load_json(ext_file)
        if not isinstance(data, dict) or not data.get("_completed"):
            continue

        slug = data.get("_book_slug", ext_file.stem.replace("_extracted", ""))
        for cat in merged:
            for item in data.get(cat, []):
                name = item.get("name", "").lower()
                if name and name not in seen[cat]:
                    seen[cat].add(name)
                    merged[cat].append(item)
                    sources[cat][name] = slug

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
                chunks = f"{data.get('_total_chunks', '?')} chunks"
                status = f"✅ {total}e"
            else:
                done = len(data.get("_chunks_processed", []))
                total_c = data.get("_total_chunks", "?")
                status = f"🔄 {done}/{total_c}"
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
