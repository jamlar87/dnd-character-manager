"""AI generation routes — generation, portrait, background, history."""

"""Character routes — create, sheet, level-up, spells, combat, relationships."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import asyncio, json, random, os, time, httpx

from main import (
    get_db, require_user, _require_owned,
)
from main import RACES, CLASSES
from main import BACKGROUNDS, ALIGNMENTS
from main import get_racial_trait_effects
from data import (
    ALL_SKILLS, SKILL_ABILITIES,
)

# ── PHB grounding tables ────────────────────────────────────────────────
# Aliases so the AI prompts can name them the way the rules text does.
PHB_BACKGROUNDS = BACKGROUNDS  # PHB p.125-141
PHB_ALIGNMENTS = ALIGNMENTS    # PHB p.122

# Level / spell-slot helpers. services.leveling imports only main + data, so a
# module-scope import here is cycle-free.
from services.leveling import (
    PROFICIENCY_BONUS, modifier, get_class_features, get_feats_for_level,
    enrich_features, get_spell_slots,
)

router = APIRouter()

# ── AI Model Config ─────────────────────────────
# Every model id and endpoint is an env override with the measured default baked in: a provider
# retiring a model (gemini-2.0-flash was retired under us once) must be a config change, not a
# code edit, and the two model families here are used by other modules too.
AI_MODEL = os.environ.get("AI_OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.1.31:11434/api/generate")

# Tier 1 is PINNED, not the `openrouter/free` lottery. That router hands each request to a random
# free model, and on this app's own trap prompt (16 keys) it measured 0/3 complete: it picked a
# content-safety classifier (which answered "User Safety: safe") and reasoning models that spent the
# entire token budget thinking — 900/900 and 1048/1048 reasoning tokens — and returned EMPTY content.
# A pinned small non-reasoning model managed 2/3 on the same prompt. Ordered: first the one that
# measured best, then the general router as a second opinion.
OPENROUTER_MODELS = [
    m.strip() for m in os.environ.get(
        "AI_OPENROUTER_MODELS", "poolside/laguna-xs-2.1:free,openrouter/free"
    ).split(",") if m.strip()
]
# Gemini's free quota is PER MODEL, so a 429 on one model is not a dead tier: when
# gemini-3.6-flash answered "limit: 20 ... retry in 23h", gemini-2.5-flash on the same key
# answered 200 with clean JSON (measured Oct 2026).
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "AI_GEMINI_MODELS", "gemini-3.6-flash,gemini-2.5-flash"
    ).split(",") if m.strip()
]

# One tier's own wait, and the budget for the whole chain: the Ollama tier used to allow 120s, so a
# reachable-but-hung box could hold a request for ~160s across the three tiers before falling back.
AI_TIER_TIMEOUT = float(os.environ.get("AI_TIER_TIMEOUT", "25"))
AI_CHAIN_BUDGET = float(os.environ.get("AI_CHAIN_BUDGET", "70"))
# The cap must fit the biggest thing any caller asks for: the trap prompt wants 16 keys with
# multi-sentence values, and a reasoning model's thinking is billed against this too.
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "1400"))
# ────────────────────────────────────────────────


def _provider_error(body: dict, raw: str) -> str:
    """The provider's own words. A 429's message names the quota and when it resets."""
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)[:200]
    if isinstance(err, str):
        return err[:200]
    return (raw or "no body")[:200]


async def _post_json(url: str, payload: dict, headers: dict | None = None,
                     timeout: float = 20.0) -> tuple[int, dict, str]:
    """The one place a tier talks HTTP, so tests can drive status/body without a network.

    Status 0 means the transport failed. An HTTP error status is returned, not raised: the
    tiers decide what a 429 or a 404 means, and the body carries the reason that used to be
    discarded (a 429 read as `KeyError('candidates')` tells a human nothing).
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers or {})
        try:
            body = resp.json()
        except Exception:
            body = {}
        return resp.status_code, body, resp.text[:400]
    except Exception as exc:
        return 0, {}, f"{type(exc).__name__}: {exc}"


async def _call_gemini(prompt: str) -> str | None:
    """Tier 2: Google Gemini (free). Requires GOOGLE_API_KEY.

    Walks GEMINI_MODELS because the free quota is per model: a 429 names the quota and its
    reset ("limit: 20 ... retry in 23h7m") and that is a fact about ONE model, not the tier.
    """
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    for model in GEMINI_MODELS:
        status, body, raw = await _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            {"contents": [{"parts": [{"text": prompt}]}]},
            timeout=AI_TIER_TIMEOUT,
        )
        if status != 200:
            print(f"[Gemini:{model}] HTTP {status}: {_provider_error(body, raw)}")
            continue
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            print(f"[Gemini:{model}] answered without candidates: {raw[:200]}")
            continue
        if text and text.strip():
            return text
        print(f"[Gemini:{model}] empty candidate text")
    return None


async def _call_openrouter(prompt: str) -> str | None:
    """Tier 1: OpenRouter (free models only, never charges). Requires OPENROUTER_API_KEY.

    Tries each configured model. Whether an answer is USABLE is decided by _call_ai, not here:
    this function's job is to get text and to say honestly what came back (model, finish reason,
    reasoning tokens) — the three ways this tier fails all look alike from the outside.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://characters.jamlarnet.stream",
        "X-OpenRouter-Title": "D&D Character Manager",
    }
    for model in OPENROUTER_MODELS:
        status, body, raw = await _post_json(
            "https://openrouter.ai/api/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}],
             "max_tokens": AI_MAX_TOKENS},
            headers=headers, timeout=AI_TIER_TIMEOUT,
        )
        if status != 200:
            print(f"[OR:{model}] HTTP {status}: {_provider_error(body, raw)}")
            continue
        try:
            choice = body["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError, TypeError):
            print(f"[OR:{model}] answered without choices: {raw[:200]}")
            continue

        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning") or "").strip()
        usage = body.get("usage") or {}
        rtok = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        # Log every answer's shape: a reasoning model that spent the budget thinking and a
        # truncation at the cap both arrive as "no content", and they need different fixes.
        print(f"[OR:{model}] finish={choice.get('finish_reason')} "
              f"completion={usage.get('completion_tokens')} reasoning={rtok} content={len(content)}B")
        if content:
            return content
        if choice.get("finish_reason") == "length":
            print(f"[OR:{model}] truncated at max_tokens={AI_MAX_TOKENS} "
                  f"(reasoning used {rtok}) — no answer left")
        elif reasoning and "{" in reasoning:
            # Some models put the answer itself in 'reasoning' (laguna-m.1 did). Only worth
            # taking when the text contains an object; _call_ai still has to accept it.
            return reasoning
        else:
            print(f"[OR:{model}] empty content and no object in reasoning")
    return None


async def _call_ollama(prompt: str) -> str | None:
    """Tier 3: the local model. No API key; dead whenever that box is off (it was, Oct 2026)."""
    status, body, raw = await _post_json(
        OLLAMA_URL,
        {"model": AI_MODEL, "prompt": prompt, "stream": False, "temperature": 0.9,
         "num_predict": AI_MAX_TOKENS, "seed": time.time_ns() % 1000000},
        timeout=AI_TIER_TIMEOUT,
    )
    if status != 200:
        print(f"[Ollama:{AI_MODEL}] unreachable (HTTP {status}): {raw[:160]}")
        return None
    text = (body.get("response") or "").strip()
    if not text:
        print(f"[Ollama:{AI_MODEL}] answered with nothing")
    return text or None

async def _call_ai(prompt: str, label: str = "gen", expect_json: bool | None = None) -> str | None:
    """OpenRouter → Gemini → Ollama, returning the first answer the CALLER can use.

    "Can use" is the whole point. Every caller either parses the reply as a JSON object or (two
    of them) takes it as prose, so a tier's answer is checked against that contract before it is
    accepted, and a failure means the next tier runs.

    This is what the chain used to get wrong: it accepted anything non-empty. Measured on this
    app's own NPC prompt in Oct 2026, 0 of 5 calls produced usable text — the replies were a
    chain-of-thought echo, a safety classifier's "User Safety: safe", and an answer truncated at
    the token cap — yet four of the five were returned as tier-1 SUCCESSES, so Gemini was never
    tried and the caller silently substituted "Human NPC" + an empty personality. A per-tier
    rejection log now names what came back and why it was refused.

    `expect_json=None` means "infer from the prompt", which is what the old brace heuristic was
    groping for; callers that genuinely want prose pass expect_json=False.
    """
    if expect_json is None:
        expect_json = "{" in (prompt or "")
    started = time.monotonic()
    rejects: list[str] = []
    tiers = (("openrouter", _call_openrouter), ("gemini", _call_gemini), ("ollama", _call_ollama))

    for name, fn in tiers:
        left = AI_CHAIN_BUDGET - (time.monotonic() - started)
        if left <= 1:
            rejects.append(f"{name}: skipped, chain budget spent")
            continue
        t0 = time.monotonic()
        try:
            text = await asyncio.wait_for(fn(prompt), timeout=min(AI_TIER_TIMEOUT, left))
        except asyncio.TimeoutError:
            rejects.append(f"{name}: no answer in {min(AI_TIER_TIMEOUT, left):.0f}s")
            print(f"[AI {label}] tier={name} TIMEOUT")
            continue
        except Exception as exc:                       # a tier must never break the chain
            rejects.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[AI {label}] tier={name} raised {type(exc).__name__}: {exc}")
            continue

        took = time.monotonic() - t0
        if not text:
            rejects.append(f"{name}: empty answer")
            if name == "openrouter":
                print(f"[AI {label}] tier=openrouter rejected: empty answer ({len(text or '')}B)")
            continue
        if expect_json and _extract_json(text) is None:
            why = "no JSON object in the answer"
            rejects.append(f"{name}: {why} ({len(text)}B)")
            print(f"[AI {label}] tier={name} rejected: {why} ({len(text)}B)")
            continue
        print(f"[AI {label}] tier={name} ok {took:.1f}s {len(text)}B")
        return text

    print(f"[AI {label}] all tiers failed | " + " | ".join(rejects))
    return None


def _first_json_object(text: str) -> str | None:
    """The first balanced {...} span, aware of strings and escapes. None if there is none."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _extract_json(text: str) -> dict | None:
    """The JSON OBJECT in an LLM response, or None — never anything else.

    Two rules, both learned the hard way:
    - **dict or nothing.** Callers immediately do `ai.get(...)`, so handing back whatever
      json.loads produced meant a model answering a "return JSON" prompt with `[1,2,3]` (or
      `true`, or `42`) turned into an AttributeError and a 500 instead of the caller's fallback.
    - **find the object inside prose.** Free models write "Here is the JSON: {...}" constantly.
      A code fence is honoured first, then the bare text, then the first balanced object, so a
      usable answer is extracted rather than thrown away as unparseable.
    """
    if not text:
        return None
    stripped = text.strip()
    candidates = []
    if "```" in stripped:
        block = stripped.split("```")[1]
        if block[:4].lower().strip() == "json":
            block = block[4:]
        candidates.append(block.strip())
    candidates.append(stripped)
    embedded = _first_json_object(stripped)
    if embedded:
        candidates.append(embedded)
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None

def _validate_and_fix(ai: dict, race: str = "", class_name: str = "", name: str = "", abilities: dict = None, alignment: str = "") -> dict:
    """Ground AI output in PHB data. Fix hallucinations silently."""
    # Validate background against PHB list (p.125-141)
    if ai.get("background") not in PHB_BACKGROUNDS:
        ai["background"] = random.choice(PHB_BACKGROUNDS)
    # Validate alignment against PHB list (p.122) — prefer user choice
    if ai.get("alignment") not in PHB_ALIGNMENTS:
        ai["alignment"] = alignment if alignment in PHB_ALIGNMENTS else random.choice(PHB_ALIGNMENTS)
    # Ensure name exists. creation.py imports THIS module at import time, so
    # random_name must be imported lazily (module scope = circular import).
    if not ai.get("name"):
        from routes.characters.creation import random_name
        ai["name"] = name or random_name(race)["name"]
    # Ensure personality + backstory exist
    if not ai.get("personality"):
        # Use top ability score for personality flavor
        top_ab = max(abilities, key=abilities.get) if abilities else ""
        ab_traits = {
            "strength": "Physically imposing and direct.",
            "dexterity": "Nimble and quick-witted.",
            "constitution": "Tough and resilient.",
            "intelligence": "Studious and analytical.",
            "wisdom": "Perceptive and intuitive.",
            "charisma": "Magnetic and persuasive.",
        }
        trait = ab_traits.get(top_ab, "Brave but reckless.")
        ai["personality"] = f"{trait} Loyal to friends. Distrusts authority."
    if not ai.get("backstory"):
        bg = ai.get("background", "adventurer").lower()
        ai["backstory"] = f"A {race} {class_name} who grew up as a {bg}. They seek adventure and glory."
    return ai

@router.post("/api/ai/generate", response_class=JSONResponse)
async def ai_generate(request: Request):
    """Generate character flavor (name/bg/alignment/personality/backstory).
    All mechanical data is hardcoded from PHB. Only creative text is generated.
    Model chain: OpenRouter → Gemini → Ollama → deterministic fallback."""
    require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    name = data.get("name", "")
    abilities = data.get("abilities", {})
    skills = data.get("skills", [])
    alignment = data.get("alignment", "")

    # Build ability summary for AI
    ab_summary = ", ".join(f"{k[:3].upper()} {v}" for k, v in sorted(abilities.items(), key=lambda x: x[1], reverse=True)) if abilities else "Standard array"
    skill_summary = ", ".join(skills) if skills else "Class defaults"

    # Prompt is constrained to PHB-approved options only
    bg_list = ", ".join(PHB_BACKGROUNDS)
    al_list = ", ".join(PHB_ALIGNMENTS)
    prompt = f"""Generate a D&D 5e character concept using ONLY these official Player's Handbook options.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Ability scores: {ab_summary}
Proficient skills: {skill_summary}
Preferred alignment: {alignment or 'Any'}
PHB Backgrounds: {bg_list}
PHB Alignments: {al_list}
{'Player name suggestion: ' + name if name else 'Generate a race-appropriate name.'}

Use ability scores to inform personality and backstory. A high-INT character might have a scholarly name and background; a high-STR character might have a martial background. Pick a background and alignment that fit the class and ability scores.

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Firstname Lastname", "background": "one from PHB list above", "alignment": "one from PHB list above", "personality": "2-3 personality traits", "backstory": "2-3 sentence backstory connecting race, class, and background"}}"""

    text = await _call_ai(prompt, label="generate")

    ai = _extract_json(text) if text else None
    if ai:
        ai = _validate_and_fix(ai, race, class_name, name, abilities, alignment)
    else:
        ai = _fallback_generate(race, class_name, subclass, name, abilities, skills, alignment)
    # Say which it was. Every caller renders these as if the model wrote them, and a deterministic
    # placeholder that reads like AI output is how a total outage went unnoticed for weeks.
    ai["ai_generated"] = bool(text)
    return JSONResponse(ai)

def _fallback_generate(race: str, class_name: str, subclass: str, name: str, abilities: dict = None, skills: list = None, alignment: str = "") -> dict:
    """Deterministic fallback when AI is unavailable. Uses abilities for flavor."""
    if not name:
        from routes.characters.creation import random_name  # lazy: see _validate_and_fix
        name = random_name(race)["name"]
    if alignment and alignment in PHB_ALIGNMENTS:
        al = alignment
    else:
        al = random.choice(ALIGNMENTS)
    bg = random.choice(BACKGROUNDS)

    # Use highest ability for personality flavor
    top_ability = ""
    if abilities:
        top_ability = max(abilities, key=abilities.get) if abilities else ""
    ab_flavors = {
        "strength": "Physically imposing and direct in their methods.",
        "dexterity": "Nimble and quick-witted, always one step ahead.",
        "constitution": "Tough and resilient, unafraid of hardship.",
        "intelligence": "Studious and analytical, always seeking knowledge.",
        "wisdom": "Perceptive and intuitive, guided by instinct.",
        "charisma": "Magnetic and persuasive, a natural leader.",
    }
    personality = ab_flavors.get(top_ability, "Brave but reckless. Loyal to friends. Distrusts authority.")
    personality += " They are driven by a desire to prove themselves to the world."

    return {
        "name": name,
        "background": bg,
        "alignment": al,
        "personality": personality,
        "backstory": f"A {race} {class_name}{' of the ' + subclass if subclass else ''} who grew up as a {bg.lower()}. They seek adventure and glory, driven by a desire to prove themselves to the world."
    }

# ── Custom Background Generation ──────────────────────────────────────────

# Background-appropriate items (common magic + mundane equipment from SRD/PHB)
# Format: "Name (SRD reference)" for magic items, plain name for mundane
BACKGROUND_ITEM_POOL = [
    # Potions (common)
    "Potion of Healing (SRD: Potion of Healing)",
    "Potion of Climbing (SRD: Potion of Climbing)",
    # Scrolls (common)
    "Spell Scroll — Cantrip (SRD: Spell Scroll Cantrip)",
    "Spell Scroll — 1st level (SRD: Spell Scroll 1st)",
    # Minor magic items (uncommon, not overpowered)
    "Hat of Disguise (SRD: Hat of Disguise)",
    "Goggles of Night (SRD: Goggles of Night)",
    "Rope of Climbing (SRD: Rope of Climbing)",
    "Helm of Comprehending Languages (SRD: Helm of Comprehending Languages)",
    "Lantern of Revealing (SRD: Lantern of Revealing)",
    "Potion of Animal Friendship (SRD: Potion of Animal Friendship)",
    "Potion of Water Breathing (SRD: Potion of Water Breathing)",
    "Potion of Growth (SRD: Potion of Growth)",
    "Oil of Slipperiness (SRD: Oil of Slipperiness)",
    "Dust of Disappearance (SRD: Dust of Disappearance)",
    "Restorative Ointment (SRD: Restorative Ointment)",
    # Mundane equipment / tools (PHB)
    "Healer's Kit",
    "Climber's Kit",
    "Disguise Kit",
    "Herbalism Kit",
    "Navigator's Tools",
    "Thieves' Tools",
    "Hunting Trap",
    "Magnifying Glass",
    "Spyglass",
    "Signal Whistle",
    "Grappling Hook",
    "Crowbar",
    "Block and Tackle",
    "Abacus",
    "Sealing Wax",
    "Merchant's Scale",
    "Fishing Tackle",
    "Hourglass",
    "Steel Mirror",
    "Vial of Perfume",
    "Vial of Acid",
    "Vial of Alchemist's Fire",
    "Vial of Antitoxin",
    "Flask of Holy Water",
    "Blanket",
    "Tinderbox",
    "Waterskin",
    "Rations (2 days)",
    "Chalk (5 pieces)",
    "String (10 feet)",
    "Bell",
    "Candle",
    "Soap",
    "Iron Pot",
    "Shovel",
    "Whetstone",
    "Two-Person Tent",
    # Trinkets and flavor
    "Silver locket with a portrait inside",
    "Small mechanical bird that chirps",
    "Bag of polished river stones",
    "Bone dice set with a worn leather cup",
    "Lucky rabbit's foot",
    "Old brass compass that doesn't point north",
    "Glass orb filled with swirling smoke",
    "Carved wooden whistle shaped like a dragon",
    "Sealed letter with a wax sigil",
    "Worn journal with cryptic entries",
]

@router.post("/api/ai/generate-background", response_class=JSONResponse)
async def ai_generate_background(request: Request):
    """Generate a unique custom background based on character choices.
    Model chain: OpenRouter → Gemini → Ollama → deterministic fallback."""
    require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    abilities = data.get("abilities", {})
    skills = data.get("skills", [])
    alignment = data.get("alignment", "")

    prompt = f"""Create a UNIQUE D&D 5e custom background for this character. Do NOT use any of the {len(BACKGROUNDS)} standard backgrounds. Invent something new and specific to this character.

Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Ability scores: {abilities}
Skills: {skills}
Alignment: {alignment or 'Any'}

Choose exactly 3 items from this approved list. You may add flavor/rename them (e.g., "Granny's Healer's Kit"), but ALWAYS include the SRD reference in parentheses — e.g., "Granny's Kit (SRD: Healer's Kit)". Mundane items don't need an SRD reference. Do NOT invent items not on this list. Do NOT include armor, weapons, or powerful magic items.

Available items:
{chr(10).join('- ' + it for it in BACKGROUND_ITEM_POOL)}

Return ONLY valid JSON (no markdown, no explanation):
{{"name": "Background Name (2-4 words, creative and unique)", "description": "2-3 sentence description of this character's background and how it shaped them", "items": ["Flavored Name (SRD: Reference)", "Item 2", "Item 3"], "gp": 15}}"""

    text = await _call_ai(prompt, label="bg")

    ai = _extract_json(text) if text else None
    # A usable answer needs name AND description; anything less substitutes the deterministic text,
    # so the flag has to follow the branch, not merely "did the model reply".
    ai_generated = bool(ai and ai.get("name") and ai.get("description"))
    if ai and ai.get("name") and ai.get("description"):
        # Ensure items and gp exist
        if "items" not in ai or not isinstance(ai.get("items"), list):
            ai["items"] = _random_items(class_name)
        if "gp" not in ai:
            ai["gp"] = random.randint(10, 25)
        # Validate uniqueness — reject if it matches a PHB background name
        if ai["name"] in BACKGROUNDS:
            ai = _fallback_background(race, class_name, subclass)
            ai_generated = False          # what is returned is not the model's text
    else:
        ai = _fallback_background(race, class_name, subclass)
        ai_generated = False
    ai["ai_generated"] = ai_generated
    return JSONResponse(ai)

def _random_items(class_name: str) -> list:
    """Pick 3 random background-appropriate items from the pool."""
    return random.sample(BACKGROUND_ITEM_POOL, min(3, len(BACKGROUND_ITEM_POOL)))

def _fallback_background(race: str, class_name: str, subclass: str = "") -> dict:
    """Deterministic fallback — pool of unique backgrounds by class."""
    bg_pool = {
        "Barbarian": [
            {"name":"Tribal Outcast","desc":"Exiled from their clan for refusing to follow a corrupt chieftain. They wander the wilds, honing their rage into a weapon of justice.","items":["Clan Hunter's Trap (SRD: Hunting Trap)","Bone Dice Set (SRD: Bone dice set with a worn leather cup)","Whetstone"],"gp":12},
            {"name":"Spirit-Blessed Wanderer","desc":"Touched by ancestral spirits during a near-death experience. They follow visions across the land, guided by voices only they can hear.","items":["Spirit Ward Pouch (SRD: Restorative Ointment)","Blanket","Tinderbox"],"gp":10},
            {"name":"Arena Champion","desc":"Fought for survival in underground fighting pits. They earned their freedom through blood and now seek a greater purpose.","items":["Victor's Brew (SRD: Potion of Healing)","Iron Pot","Soap"],"gp":18},
        ],
        "Bard": [
            {"name":"Traveling Minstrel","desc":"Performed in taverns and courts across the realm. Their songs carry secrets overheard from nobles and commoners alike.","items":["Disguise Kit","Vial of Perfume","Signal Whistle"],"gp":14},
            {"name":"Disgraced Courtier","desc":"Once a trusted advisor who uncovered a conspiracy. Framed for treason, they now travel under a new identity, seeking the truth.","items":["Courtier's Veil (SRD: Hat of Disguise)","Sealing Wax","Sealed letter with a wax sigil"],"gp":20},
        ],
        "Cleric": [
            {"name":"Pilgrim of the Lost","desc":"Received a divine vision during a plague that wiped out their village. They now travel, healing the sick and seeking answers.","items":["Sacred Salve (SRD: Restorative Ointment)","Healer's Kit","Candle"],"gp":11},
            {"name":"Heretic Reformer","desc":"Cast out from their temple for questioning doctrine. Their faith remains unshaken, but they now serve their god outside the clergy's walls.","items":["Forbidden Scroll (SRD: Spell Scroll — 1st level)","Flask of Holy Water","Worn journal with cryptic entries"],"gp":15},
        ],
        "Druid": [
            {"name":"Grove Warden","desc":"Sworn protector of an ancient forest grove. The trees whisper warnings to them, and animals treat them as kin.","items":["Herbalism Kit","Fishing Tackle","Carved wooden whistle shaped like a dragon"],"gp":10},
        ],
        "Fighter": [
            {"name":"Broken Legionnaire","desc":"The sole survivor of a massacre that destroyed their company. They carry the unit's standard, seeking to restore its honor.","items":["Whetstone","Crowbar","Blanket"],"gp":13},
            {"name":"Village Guardian","desc":"A small-town militia captain who stood against a monster incursion. They now seek proper training to protect others.","items":["Healer's Kit","Signal Whistle","Iron Pot"],"gp":16},
        ],
        "Monk": [
            {"name":"Mountain Acolyte","desc":"Trained in a remote monastery hidden among the peaks. They descend to the world below on a mission of enlightenment.","items":["Meditation Scroll (SRD: Spell Scroll — Cantrip)","Hourglass","Bell"],"gp":10},
        ],
        "Paladin": [
            {"name":"Oathsworn Avenger","desc":"Witnessed their mentor fall to corruption. They swore an oath over the mentor's grave to root out evil wherever it hides.","items":["Blessed Vial (SRD: Flask of Holy Water)","Steel Mirror","Candle"],"gp":14},
        ],
        "Ranger": [
            {"name":"Frontier Scout","desc":"Mapped uncharted wilderness for a frontier settlement. They know every trail, every danger, and every safe haven for 50 miles.","items":["Climber's Kit","Old brass compass that doesn't point north","Tinderbox"],"gp":12},
        ],
        "Rogue": [
            {"name":"Reformed Smuggler","desc":"Ran contraband through city sewers before a close call with the law. They now use their skills for more honest — but equally thrilling — work.","items":["Thieves' Tools","Grappling Hook","Chalk (5 pieces)"],"gp":15},
        ],
        "Sorcerer": [
            {"name":"Wild Magic Prodigy","desc":"Their powers erupted during a childhood accident, destroying their home. They've spent years learning to control the chaos within.","items":["Arcanic Spark (SRD: Spell Scroll — Cantrip)","Glass orb filled with swirling smoke","Chalk (5 pieces)"],"gp":12},
        ],
        "Warlock": [
            {"name":"Reluctant Pact-Bearer","desc":"Made a desperate bargain to save a loved one. Now bound to a mysterious patron, they seek a way to break free — or at least understand the terms.","items":["Patron's Token (SRD: Sealed letter with a wax sigil)","Candle","Silver locket with a portrait inside"],"gp":10},
        ],
        "Wizard": [
            {"name":"Arcane Archivist","desc":"Apprenticed to an eccentric librarian who guarded forbidden knowledge. They inherited the library's secrets when the old mage vanished.","items":["Scholar's Scroll (SRD: Spell Scroll — 1st level)","Magnifying Glass","Worn journal with cryptic entries"],"gp":14},
            {"name":"Failed Academy Student","desc":"Expelled from a prestigious magical academy for an experiment gone wrong. They continue their studies independently, determined to prove the academy wrong.","items":["Practitioner's Lens (SRD: Magnifying Glass)","Vial of Acid","Abacus"],"gp":11},
        ],
    }
    pool = bg_pool.get(class_name, [
        {"name":"Wandering Adventurer","desc":"Grew up hearing tales of heroes and monsters. When tragedy struck their hometown, they took up arms and never looked back.","items":["Healer's Kit","Tinderbox","Waterskin"],"gp":10}
    ])
    bg = random.choice(pool)
    return {"name": bg["name"], "description": bg["desc"], "items": list(bg["items"]), "gp": bg["gp"]}

# ── Character History Generation ─────────────────────────────────────────

@router.post("/api/character/{char_id}/generate-history", response_class=JSONResponse)
async def generate_character_history(char_id: int, request: Request):
    """Generate a comprehensive character backstory from childhood to present.
    Weaves race, class, subclass, background, skills, feats, ASI choices,
    and any user keywords into a rich narrative history.
    Model chain: OpenRouter → Gemini → Ollama → deterministic fallback."""
    user = require_user(request)
    data = await request.json()
    keywords = (data.get("keywords") or "").strip()

    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)

    char = dict(row)
    db.close()

    # Parse JSON fields
    for f in ("skills","features","expertise_skills","asi_history","metamagic_history",
              "metamagic","invocations","maneuvers","magical_secrets","infusions",
              "feature_data"):
        try:
            char[f] = json.loads(char[f])
        except (json.JSONDecodeError, TypeError):
            char[f] = [] if f != "feature_data" else []

    race = char.get("race","")
    subrace = char.get("subrace","")
    class_name = char.get("class_name","")
    subclass = char.get("subclass","")
    bg = char.get("background","")
    alignment = char.get("alignment","")
    name = char.get("name","")
    level = char.get("level",1)
    fighting_style = char.get("fighting_style","")

    # Race/class data
    race_data = RACES.get(race, {})
    class_data = CLASSES.get(class_name, {})
    race_desc = race_data.get("desc","")
    class_desc = class_data.get("desc","")
    subclass_desc = (class_data.get("subclass_descs",{}) or {}).get(subclass,"")

    # Ability scores
    abilities = {a: char.get(a,10) for a in ("strength","dexterity","constitution","intelligence","wisdom","charisma")}

    # Skills
    skills = char.get("skills",[])
    expertise = char.get("expertise_skills",[])

    # Feats from feature_data
    feat_names = []
    for f in char.get("feature_data",[]):
        fn = f.get("name","") if isinstance(f,dict) else ""
        if "Ability Score Improvement" in fn and char.get("asi_history"):
            # Look up what was chosen
            lvl = int(f.get("level","L1").replace("L",""))
            for ae in char["asi_history"]:
                if ae.get("level") == lvl:
                    if ae.get("type") == "feat":
                        feat_names.append(ae.get("feat","").replace("_"," "))
                    else:
                        choices = ae.get("choices",{})
                        parts = [f"+{v} {k[:3].title()}" for k,v in choices.items()]
                        feat_names.append(", ".join(parts))
                    break

    # Choice systems
    metamagic = char.get("metamagic",[])
    invocations = char.get("invocations",[])
    maneuvers = char.get("maneuvers",[])
    magical_secrets = char.get("magical_secrets",[])
    infusions = char.get("infusions",[])

    prompt_parts = [f"""Write a rich, compelling backstory for this D&D 5e character. Write in third-person past tense, covering childhood origins through to the present day (level {level}). Weave in details about their race, class, background, skills, and key abilities. Make it feel like a lived life — include formative events, mentors, failures, and triumphs. Write 3-5 paragraphs.

CHARACTER PROFILE
Name: {name}
Race: {race}{' (' + subrace + ')' if subrace else ''}
Class: {class_name}{' — ' + subclass if subclass else ''}
Level: {level}
Background: {bg}
Alignment: {alignment}
Fighting Style: {fighting_style.replace('_',' ').title() if fighting_style else 'None'}"""]

    if race_desc:
        prompt_parts.append(f"\nRACE DESCRIPTION\n{race_desc[:400]}")
    if class_desc:
        prompt_parts.append(f"\nCLASS DESCRIPTION\n{class_desc[:400]}")
    if subclass_desc:
        prompt_parts.append(f"\nSUBCLASS DESCRIPTION\n{subclass_desc[:400]}")

    prompt_parts.append("\nABILITY SCORES\n" + ", ".join(f"{k.title()}: {v}" for k,v in abilities.items()))

    if skills:
        prompt_parts.append(f"\nSkill Proficiencies: {', '.join(skills)}")
    if expertise:
        prompt_parts.append(f"\nExpertise: {', '.join(expertise)}")

    if feat_names:
        prompt_parts.append(f"\nASI/Feat Choices: {', '.join(feat_names)}")
    if metamagic:
        prompt_parts.append(f"\nMetamagic: {', '.join(metamagic)}")
    if invocations:
        prompt_parts.append(f"\nEldritch Invocations: {', '.join(invocations)}")
    if maneuvers:
        prompt_parts.append(f"\nManeuvers: {', '.join(maneuvers)}")
    if magical_secrets:
        prompt_parts.append(f"\nMagical Secrets: {', '.join(magical_secrets)}")
    if infusions:
        prompt_parts.append(f"\nInfusions: {', '.join(infusions)}")

    if keywords:
        prompt_parts.append(f"\nPLAYER NOTES (incorporate these): {keywords}")

    prompt_parts.append("\nReturn ONLY the backstory text — no JSON, no labels, no preamble. Just the narrative paragraphs.")

    prompt = "\n".join(prompt_parts)

    text = await _call_ai(prompt, label="history")
    ai_generated = bool(text)
    if not text:
        # Fallback
        text = _fallback_history(char, race_desc, class_desc, subclass_desc)

    return JSONResponse({"backstory": text.strip(), "ai_generated": ai_generated})


def _fallback_history(char: dict, race_desc: str, class_desc: str, subclass_desc: str) -> str:
    """Deterministic fallback when all AI tiers are unavailable."""
    name = char.get("name","the adventurer")
    race = char.get("race","")
    subrace = char.get("subrace","")
    class_name = char.get("class_name","")
    subclass = char.get("subclass","")
    bg = char.get("background","").lower()
    skills = char.get("skills",[])
    fighting_style = char.get("fighting_style","")
    level = char.get("level",1)

    race_str = f"{subrace} {race}" if subrace else race
    class_str = f"{subclass} {class_name}" if subclass else class_name
    skill_str = f"skilled in {', '.join(skills[:4])}" if skills else "eager to learn"

    paras = [
        f"{name} was born into a {race_str} community, raised from childhood as a {bg}. Early life taught them resilience and shaped their identity as a {skill_str}.",
    ]

    if fighting_style:
        fs = fighting_style.replace("_"," ").title()
        paras.append(f"As a young adult, they trained relentlessly, mastering the {fs} fighting style. Their reputation grew among allies and rivals alike.")

    paras.append(f"Now a level {level} {class_str}, {name} carries the weight of every battle fought and every lesson learned. Driven by purpose and hardened by experience, they stand ready for whatever adventure comes next.")

    return "\n\n".join(paras)


# ── Character Portrait Generation ────────────────────────────────────────────
# ONE prompt builder + ONE provider implementation live in services/portraits.py
# (the batch script uses them too). These aliases keep the historical private
# names working for the rest of this module and for its re-exports.
from services import portraits as _portraits

PORTRAIT_PROVIDER = _portraits.PORTRAIT_PROVIDER
_fetch_openrouter_image = _portraits.fetch_openrouter_image
_fetch_pollinations_image = _portraits.fetch_pollinations_image
_generate_portrait_image = _portraits.generate_portrait_image
_fallback_portrait_prompt = _portraits.portrait_prompt
from services import ref_portraits as _ref_portraits  # noqa: E402 - marker only

# Portrait image generation.
# Pollinations needs no key, no account and no card; it is the default. The
# OpenRouter path stays available (PORTRAIT_PROVIDER=openrouter) for when that
# key has budget — it had a $0.30 cap that was fully spent, which is why every
# portrait silently failed before this.
# The value reported to the caller must be the one that DISPATCHED the generation. This line
# used to re-read the env with a different default ("pollinations") than services/portraits.py
# ("horde"), so with no env var set every Horde failure was reported as a Pollinations failure —
# the diagnosis went to the wrong host, and the test that should have caught it compared the
# route's constant against itself. Keep the alias from services, one source of truth.
_PORTRAIT_PROVIDER_NOTE = "reported provider == the provider that ran"


@router.post("/api/ai/portrait", response_class=JSONResponse)
async def ai_portrait(request: Request):
    """Generate a high-fantasy character portrait prompt, and attempt image generation.
    Returns prompt immediately; image may be empty if generation is slow/unavailable.
    If custom_prompt provided, uses it directly.
    If character_id provided, persists the result to the DB."""
    user = require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    gender = (data.get("gender") or "").strip().lower()
    custom_prompt = (data.get("custom_prompt") or "").strip()
    character_id = data.get("character_id")
    # DM NPCs use the same endpoint: name + notes in, image out. Nothing is
    # written here — the NPC form owns the row and saves portrait_url itself.
    npc = data.get("npc") or {}

    # Build prompt — always use fast deterministic fallback (covers all 38 races)
    if npc:
        # The gender argument was simply not passed, so the DM's NPC path could not produce the
        # measured-good subject ("a female gnome, ...") that the reference path produces: the
        # pronoun is read from the notes, which is where a DM writes it.
        image_prompt = _portraits.npc_prompt(
            npc.get("name", ""), npc.get("notes", ""), npc.get("race", ""), npc.get("role", ""),
            gender=_ref_portraits.gender_from_text(npc.get("notes", "")))
        print(f"[AI portrait] npc {str(npc.get('name'))[:40]!r}")
    elif custom_prompt:
        clause = f", {gender} subject" if gender in {"male", "female"} else ""
        image_prompt = ("Bust portrait, upper body only, 3:4 aspect ratio" + clause
                        + f", close-up composition. High fantasy oil painting, dramatic lighting. {custom_prompt}")
        print(f"[AI portrait] custom_prompt race={race} class={class_name}")
    else:
        image_prompt = _portraits.genderize(
            _fallback_portrait_prompt(race, class_name, subclass), gender)
        # Kick off background tasks: AI enrichment + image generation (don't block response)
        # (The old enrichment task made one LLM call per Generate click and threw
        # the result away — its own docstring said "updates nothing". The stored
        # prompt is the deterministic one, so it only ever burned quota.)
        print(f"[AI portrait] fallback race={race} class={class_name}")

    # A prompt-only caller (the sheet's "Get Prompt" button) gets the text and nothing else: that
    # button previews the prompt, and running the provider for it spent one of the ~50/day keyless
    # generations and stood the reference-art backfill down — for text the server already had.
    if data.get("prompt_only"):
        return JSONResponse({"prompt": image_prompt, "image_url": "", "error": "",
                             "provider": PORTRAIT_PROVIDER, "prompt_only": True})

    # Generate inline so the caller learns the real outcome. As a background
    # task a failure could only be logged, and the wizard then claimed
    # "Generation queued… Free tier ~50/day" while every request was rejected.
    # Claim interactive priority: the reference-art backfill draws on the same
    # free quota and stands down while this is in flight (services.ref_portraits).
    with _ref_portraits.interactive():
        image_url, error = await _generate_portrait_image(image_prompt)
    if image_url:
        # Belt and braces: the write site normalises, not just the provider.
        from services.images import normalize_portrait
        image_url, _nerr = normalize_portrait(image_url, max_px=1024)
        if _nerr or not image_url:
            image_url, error = None, _nerr or "the generated image was unusable"

    if character_id:
        db = get_db()
        if image_url:
            db.execute("UPDATE characters SET portrait_url=?, portrait_prompt=? WHERE id=? AND user_id=?",
                       (image_url, image_prompt, character_id, user["id"]))
        else:
            # Keep any existing portrait — a failed generation is not a delete.
            db.execute("UPDATE characters SET portrait_prompt=? WHERE id=? AND user_id=?",
                       (image_prompt, character_id, user["id"]))
        db.commit()
        db.close()
        print(f"[AI portrait] char {character_id}: " +
              ("image saved" if image_url else f"no image ({error})"))

    return JSONResponse({"prompt": image_prompt, "image_url": image_url or "",
                         "error": error or "", "provider": PORTRAIT_PROVIDER})
@router.post("/api/character/portrait", response_class=JSONResponse)
async def save_portrait_image(request: Request):
    """Save a client-generated portrait image (base64 data URL) to the DB."""
    user = require_user(request)
    data = await request.json()
    character_id = data.get("character_id")
    image_data = data.get("image_data", "")  # base64 data URL from Puter.js

    if not character_id or not image_data:
        return JSONResponse({"error": "character_id and image_data required"}, status_code=400)

    # Single sanitising entry point: reject junk/oversized payloads and shrink
    # what we store (the sheet used to keep 1.9-3.4 MB originals).
    from services.images import normalize_portrait
    image_data, err = normalize_portrait(image_data, max_px=1024)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    db = get_db()
    db.execute("UPDATE characters SET portrait_url=? WHERE id=? AND user_id=?",
               (image_data, character_id, user["id"]))
    db.commit()
    db.close()
    return JSONResponse({"ok": True})

# ── Build Generation (PHB-grounded, level-aware) ────────────────────────────

@router.post("/api/ai/build", response_class=JSONResponse)
async def ai_build(request: Request):
    """Generate a complete optimized build for the given class/race/subclass/level."""
    require_user(request)
    data = await request.json()
    race = data.get("race", "Human")
    subrace = data.get("subrace", "")
    class_name = data.get("class_name", "Fighter")
    subclass = data.get("subclass", "")
    level = min(max(int(data.get("level", 1)), 1), 20)

    # Lazy import: routes.characters.sheet pulls in campaign/helpers, and
    # creation.py imports THIS module at import time — a module-scope import of
    # sheet here re-enters ai_routes mid-initialisation (partially initialized
    # module ImportError, which crash-loops the service).
    from routes.characters.sheet import (
        allocate_ability_scores, calc_hp, get_spells_for_level,
        get_equipment_for_level, pick_magic_items,
    )
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    abilities = allocate_ability_scores(class_name, race, subrace)
    mods = {ability: modifier(score) for ability, score in abilities.items()}
    pb = PROFICIENCY_BONUS.get(level, 2)

    con_mod = mods["constitution"]
    hp = calc_hp(class_name, level, con_mod)
    racial_eff = get_racial_trait_effects(race, subrace)
    ac = _calculate_ac(class_name, level, mods, racial_eff.get("natural_armor"))

    saves = {ability: mod + (pb if ability in class_data["saves"] else 0)
             for ability, mod in mods.items()}

    skills = _pick_skills(class_name, mods)
    equipment = get_equipment_for_level(class_name, level)
    magic_items = pick_magic_items(class_name, level)
    feats = get_feats_for_level(class_name, level)

    spell_data = get_spells_for_level(class_name, level)
    spell_slots = get_spell_slots(class_name, level)

    attacks = _calculate_attacks(class_name, level, mods, pb, equipment)

    raw_features = get_class_features(class_name, level, subclass)
    enriched_features = enrich_features(raw_features, class_name=class_name, level=level, mods=mods, subclass=subclass)

    race_data = RACES.get(race, RACES["Human"])
    build = {
        "class": class_name,
        "subclass": subclass or None,
        "race": race,
        "subrace": subrace or None,
        "level": level,
        "proficiency_bonus": pb,
        "hit_points": hp,
        "hit_dice": f"{level}d{class_data['hd']}",
        "armor_class": ac,
        "initiative": mods["dexterity"],
        "speed": race_data["speed"],
        "ability_scores": abilities,
        "modifiers": mods,
        "saving_throws": saves,
        "skills": skills,
        "equipment": equipment,
        "magic_items": magic_items,
        "feats": feats,
        "cantrips": spell_data.get("cantrips", []),
        "spells": spell_data.get("spells", {}),
        "spell_slots": spell_slots,
        "attacks": attacks,
        "features": enriched_features,
    }
    return JSONResponse(build)


def _calculate_ac(class_name: str, level: int, mods: dict, natural_armor: dict | None = None) -> int:
    # Natural armor (Tortle, Lizardfolk, Loxodon, etc.) overrides class-based AC
    if natural_armor:
        ac = natural_armor.get("base_ac", 17)
        # Determine which ability modifier to use
        stat = natural_armor.get("stat", "dexterity")
        ability_mod = mods[stat]
        # Uncapped: add full ability modifier (Lizardfolk: 13 + DEX)
        if natural_armor.get("uncapped"):
            ac += ability_mod
            return ac
        # Capped DEX bonus (e.g., medium armor)
        max_bonus = natural_armor.get("max_bonus")
        if max_bonus is not None:
            ac += min(ability_mod, max_bonus)
            return ac
        # Flat AC with no ability modifier (Tortle: 17 flat, Loxodon: 12 + CON via stat key)
        return ac

    dex = mods["dexterity"]
    if class_name == "Barbarian":
        con = mods["constitution"]
        base = 15 if level >= 20 else 14 if level >= 10 else 13 if level >= 5 else 12
        return base + dex + con
    if class_name == "Monk":
        wis = mods["wisdom"]
        return 10 + dex + wis
    if class_name in ("Wizard", "Sorcerer"):
        if level >= 5: return 13 + min(dex, 2)
        return 10 + dex
    if class_name == "Warlock":
        if level >= 20: return 12 + dex + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name in ("Bard", "Rogue", "Ranger"):
        if level >= 20: return 12 + min(dex, 5) + 3
        if level >= 15: return 12 + min(dex, 5) + 2
        if level >= 10: return 12 + min(dex, 5) + 1
        if level >= 5: return 12 + min(dex, 5)
        return 11 + min(dex, 5)
    if class_name == "Druid":
        if level >= 20: return 15 + min(dex, 2) + 3
        if level >= 10: return 12 + min(dex, 5) + 1
        return 11 + min(dex, 5)
    if class_name == "Cleric":
        if level >= 20: return 18 + 3 + 2
        if level >= 10: return 18 + 1 + 2
        return 14 + min(dex, 2) + 2
    if level >= 20: return 18 + 3
    if level >= 15: return 18 + 2
    if level >= 10: return 18 + 1
    if level >= 5: return 18
    return 16 + 2


def _calculate_attacks(class_name: str, level: int, mods: dict, pb: int, equipment: list) -> list:
    str_mod = mods["strength"]
    dex_mod = mods["dexterity"]
    magic = 3 if level >= 20 else 2 if level >= 10 else 1 if level >= 5 else 0

    if class_name == "Warlock":
        beams = 4 if level >= 17 else 3 if level >= 11 else 2 if level >= 5 else 1
        cha = mods["charisma"]
        return [{"name": f"Eldritch Blast ({beams} beam{'s' if beams > 1 else ''})",
                 "attack_bonus": cha + pb, "damage": f"1d10+{cha} per beam",
                 "type": "force", "range": "120 ft"}]

    if class_name == "Ranger":
        return [{"name": f"Longbow +{magic}" if magic else "Longbow",
                 "attack_bonus": dex_mod + pb + magic, "damage": f"1d8+{dex_mod + magic}",
                 "type": "piercing", "range": "150/600"}]

    if class_name in ("Rogue", "Monk"):
        atk = dex_mod
        weapon = "Shortsword" if class_name == "Monk" else "Rapier"
        die = "1d6" if class_name == "Monk" else "1d8"
        return [{"name": f"{weapon} +{magic}" if magic else weapon,
                 "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
                 "type": "piercing"}]

    atk = str_mod
    if class_name in ("Barbarian", "Fighter", "Paladin"):
        weapon = "Greatsword" if level >= 15 else "Longsword"
        die = "2d6" if level >= 15 else "1d8"
        if class_name == "Fighter" and dex_mod > str_mod:
            atk = dex_mod
    elif class_name == "Bard":
        atk = max(dex_mod, str_mod)
        weapon = "Rapier"
        die = "1d8"
    else:
        atk = max(str_mod, dex_mod)
        weapon = "Mace" if class_name == "Cleric" else "Quarterstaff"
        die = "1d6"

    return [{"name": f"{weapon} +{magic}" if magic else weapon,
             "attack_bonus": atk + pb + magic, "damage": f"{die}+{atk + magic}",
             "type": "slashing" if weapon in ("Greatsword", "Longsword") else "bludgeoning"}]


def _pick_skills(class_name: str, mods: dict) -> list[str]:
    class_data = CLASSES.get(class_name, CLASSES["Fighter"])
    available = class_data["skills"]
    if available == "all":
        available = ALL_SKILLS
    count = class_data["skill_count"]
    scored = [(skill, mods.get(SKILL_ABILITIES.get(skill, "intelligence"), 0))
              for skill in available]
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored[:count]]

