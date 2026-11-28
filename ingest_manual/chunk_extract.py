from __future__ import annotations
import time
from .jsonio import _extract_json
from .llm import _call_llm, _call_ollama
from .validate import _empty_result


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
        print("BAD JSON, retrying... ", end="", flush=True)
        raw2 = _call_ollama(prompt[:len(prompt)//2] + prompt[len(prompt)//2:])
        result = _extract_json(raw2) if raw2 else None

    if result:
        counts = {k: len(v) for k, v in result.items() if isinstance(v, list) and v}
        total = sum(counts.values())
        print(f"{total} items {counts} ({elapsed:.1f}s)")
    else:
        print(f"NO JSON ({elapsed:.1f}s)")

    return result if result else _empty_result()
