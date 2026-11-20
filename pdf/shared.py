"""Lazy lookups against the app data: spells, item descriptions, modifiers."""

import json
import os
import sys

# ═══════════════════════════════════════════════════════════════
#  SPELL CACHE (lightweight — only used for slot lookups)
# ═══════════════════════════════════════════════════════════════
_SPELL_CACHE = None


def _get_spell_cache():
    global _SPELL_CACHE
    if _SPELL_CACHE is not None:
        return _SPELL_CACHE
    _SPELL_CACHE = {}
    # Primary source: main.SRD_SPELLS — already merges SRD cache + manual
    # data/spells.json + class/school backfill at app startup. Using it
    # avoids the hardcoded campaign-expert path and duplicate loading.
    try:
        from core.data import SRD_SPELLS
        for s in SRD_SPELLS:
            _SPELL_CACHE[s.get("name", "").lower()] = s
    except Exception:
        pass
    if not _SPELL_CACHE:
        # Fallback when main is not importable (standalone use): load
        # from the campaign-expert engine, then manual spells.
        try:
            from engine.spells import _load_spell_cache
            raw = _load_spell_cache()
            for s in raw:
                _SPELL_CACHE[s["name"].lower()] = s
        except Exception:
            _SPELL_CACHE = {}
    # Also load from app's manual_data/spells.json (catches TCoE/SCAG spells not in SRD)
    try:
        manual_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "manual_data", "spells.json"
        )
        if os.path.exists(manual_path):
            with open(manual_path) as f:
                manual_spells = json.load(f)
            for s in manual_spells:
                name = s.get("name", "").lower()
                if name and name not in _SPELL_CACHE:
                    _SPELL_CACHE[name] = s
    except Exception:
        pass
    # Supplementary spells not in any data source (Fizban's, etc.)
    _SUPPLEMENTARY_SPELLS = {
        "absorb elements": {
            "name": "Absorb Elements", "level": 1, "school": "Abjuration",
            "casting_time": "1 reaction, which you take when you take acid, cold, fire, lightning, or thunder damage",
            "range": "Self",
            "components": "S",
            "duration": "1 round", "concentration": False,
            "description": "The spell captures some of the incoming energy, lessening its effect on you and storing it for your next melee attack. You have resistance to the triggering damage type until the start of your next turn. Also, the first time you hit with a melee attack on your next turn, the target takes an extra 1d6 damage of the triggering type, and the spell ends. At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the extra damage increases by 1d6 for each slot level above 1st.",
            "_source": "Xanathar's Guide to Everything",
        },
        "aganazzar's scorcher": {
            "name": "Aganazzar's Scorcher", "level": 2, "school": "Evocation",
            "casting_time": "1 action", "range": "30 feet",
            "components": "V, S, M (a red dragon's scale)",
            "duration": "Instantaneous", "concentration": False,
            "description": "A line of roaring flame 30 feet long and 5 feet wide emanates from you in a direction you choose. Each creature in the line must make a Dexterity saving throw. A creature takes 3d8 fire damage on a failed save, or half as much damage on a successful one. At Higher Levels. When you cast this spell using a spell slot of 3rd level or higher, the damage increases by 1d8 for each slot level above 2nd.",
            "_source": "Xanathar's Guide to Everything",
        },
        "beast bond": {
            "name": "Beast Bond", "level": 1, "school": "Divination",
            "casting_time": "1 action", "range": "Touch",
            "components": "V, S, M (a bit of fur wrapped in a cloth)",
            "duration": "Concentration, up to 10 minutes", "concentration": True,
            "description": "You establish a telepathic link with one beast you touch that is friendly to you or charmed by you. The spell fails if the beast's Intelligence score is 4 or higher. Until the spell ends, the link is active while you and the beast are within line of sight of each other. Through the link, the beast can understand your telepathic messages to it, and it can telepathically communicate simple emotions and concepts back to you. While the link is active, the beast gains advantage on attack rolls against any creature within 5 feet of you that you can see.",
            "_source": "Xanathar's Guide to Everything",
        },
        "bones of the earth": {
            "name": "Bones of the Earth", "level": 6, "school": "Transmutation",
            "casting_time": "1 action", "range": "120 feet",
            "components": "V, S",
            "duration": "Instantaneous", "concentration": False,
            "description": "You cause up to six pillars of stone to burst from places on the ground that you can see within range. Each pillar is a cylinder that has a 5-foot radius and is up to 30 feet tall. The specific shape of each pillar is up to you, but each must occupy a space on solid ground, and you can't create a pillar in the same space as a creature. The ground where a pillar appears must be able to support it. A creature in the area where a pillar appears must succeed on a Dexterity saving throw or be lifted by the pillar. A creature can choose to fail the save. If a pillar is prevented from reaching its full height because of a ceiling or other obstacle, a creature on the pillar takes 6d6 bludgeoning damage and is restrained, pinched between the pillar and the obstacle. The restrained creature can use an action to make a Strength or Dexterity check (its choice) against the spell's save DC. On a success, it is no longer restrained and must either move off the pillar or fall off it. At Higher Levels. When you cast this spell using a spell slot of 7th level or higher, you can create two additional pillars for each slot level above 6th.",
            "_source": "Elemental Evil / Xanathar's Guide to Everything",
        },
        "catapult": {
            "name": "Catapult", "level": 1, "school": "Transmutation",
            "casting_time": "1 action", "range": "60 feet",
            "components": "S",
            "duration": "Instantaneous", "concentration": False,
            "description": "Choose one object weighing 1 to 5 pounds within range that isn't being worn or carried. The object flies in a straight line up to 90 feet in a direction you choose before falling to the ground, stopping early if it impacts against a solid surface. If the object would strike a creature, that creature must make a Dexterity saving throw. On a failed save, the object strikes the target and stops moving. When the object strikes something, the object and what it strikes take 3d8 bludgeoning damage. At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the maximum weight of objects that you can target increases by 5 pounds, and the damage increases by 1d8, for each slot level above 1st.",
            "_source": "Xanathar's Guide to Everything",
        },
        "snilloc's snowball swarm": {
            "name": "Snilloc's Snowball Swarm", "level": 2, "school": "Evocation",
            "casting_time": "1 action", "range": "90 feet",
            "components": "V, S, M (a piece of ice or a small white rock chip)",
            "duration": "Instantaneous", "concentration": False,
            "description": "A swarm of magical snowballs erupts from a point you choose within range. Each creature in a 5-foot-radius sphere centered on that point must make a Dexterity saving throw. A creature takes 3d6 cold damage on a failed save, or half as much damage on a successful one. At Higher Levels. When you cast this spell using a spell slot of 3rd level or higher, the damage increases by 1d6 for each slot level above 2nd.",
            "_source": "Xanathar's Guide to Everything",
        },
        "cloying darkness": {
            "name": "Cloying Darkness", "level": 1, "school": "Necromancy",
            "casting_time": "1 action", "range": "30 feet",
            "components": "V, S",
            "duration": "1 round", "concentration": False,
            "description": "You reach out with a hand of decaying shadows. Make a ranged spell attack. If it hits, the target takes 2d8 necrotic damage and must make a Constitution saving throw. If it fails, its visual organs are enveloped in shadow until the start of your next turn, causing it to treat all lighting as if it's one step lower in intensity (it treats bright light as dim, dim light as darkness, and darkness as magical darkness). At Higher Levels. When you cast this spell using a spell slot of 2nd level or higher, the damage increases by 1d8 for each slot level above 1st.",
            "_source": "Kobold Press OGL",
        },
        "summon draconic spirit": {
            "name": "Summon Draconic Spirit", "level": 5, "school": "Conjuration",
            "casting_time": "1 action", "range": "60 feet",
            "components": "V, S, M (an object with an image of a dragon worth at least 500 gp)",
            "duration": "Concentration, up to 1 hour", "concentration": True,
            "description": "You call forth a draconic spirit. It manifests in an unoccupied space that you can see within range. This corporeal form uses the Draconic Spirit stat block. When you cast this spell, choose a family of dragon: chromatic, gem, or metallic. The creature resembles a dragon of the chosen family, which determines certain traits in its stat block. The creature disappears when it drops to 0 hit points or when the spell ends. The creature is an ally to you and your companions. In combat, the creature shares your initiative count, but it takes its turn immediately after yours. It obeys your verbal commands (no action required by you). If you don't issue any, it takes the Dodge action and uses its move to avoid danger. At Higher Levels. When you cast this spell using a spell slot of 6th level or higher, use the higher level wherever the spell's level appears in the stat block.",
            "_source": "Fizban's Treasury of Dragons",
        },
    }
    for name, data in _SUPPLEMENTARY_SPELLS.items():
        existing = _SPELL_CACHE.get(name, {})
        existing_desc = existing.get("desc", existing.get("description", ""))
        if isinstance(existing_desc, list):
            existing_desc = " ".join(existing_desc)
        # Overwrite if missing, or if existing description is a stub (< 50 chars)
        if name not in _SPELL_CACHE or len(existing_desc.strip()) < 50:
            _SPELL_CACHE[name] = data
    return _SPELL_CACHE


# ═══════════════════════════════════════════════════════════════
#  EQUIPMENT DESCRIPTION CACHE — from main.py ITEM_INDEX
# ═══════════════════════════════════════════════════════════════
_ITEM_INDEX = None


def _get_item_description(item_name):
    """Look up an item's PHB description from the app's ITEM_INDEX."""
    global _ITEM_INDEX
    if _ITEM_INDEX is None:
        try:
            sys.path.insert(0, "/home/james/dnd-character-manager")
            from core.data import ITEM_INDEX
            from core.features import _resolve_item_key
            _ITEM_INDEX = ITEM_INDEX
            _GET_ITEM_RESOLVER = _resolve_item_key
        except Exception:
            _ITEM_INDEX = {}
            _GET_ITEM_RESOLVER = lambda n: None
    else:
        try:
            sys.path.insert(0, "/home/james/dnd-character-manager")
            from core.features import _resolve_item_key
        except Exception:
            _GET_ITEM_RESOLVER = lambda n: None
    # Use the same fuzzy resolution as the rest of the app
    entry = _GET_ITEM_RESOLVER(item_name)
    if entry:
        return entry.get("description", "") or entry.get("type", "") or ""
    return ""
