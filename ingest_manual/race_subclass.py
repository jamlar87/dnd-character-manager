from __future__ import annotations
import json
import re
from .jsonio import _extract_json
from .llm import _call_llm


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
