"""AI trap builder."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.security import require_user
from routes.characters import _extract_json

from ._shared import call_ai

router = APIRouter()


@router.post("/api/dm/ai/build-trap", response_class=JSONResponse)
async def dm_ai_build_trap(request: Request):
    """AI-generated custom trap following DMG/UA trap creation guidelines."""
    require_user(request)
    data = await request.json()
    danger = data.get("danger", "dangerous")
    trap_type = data.get("type", "mechanical")
    theme = data.get("theme", "")
    location = data.get("location", "")
    party_level = data.get("party_level", "")

    # DMG presets for the AI to reference
    danger_guidelines = {
        "setback": "DC 10-11, attack +3-5, 1d10 damage, minor inconvenience — a trip wire that tangles, a minor poison that sickens for an hour",
        "dangerous": "DC 12-15, attack +6-8, 2d10 damage, likely to injure — collapsing floor, scything blade, glyph of warding",
        "deadly": "DC 16-20, attack +9-12, 4d10+ damage, could kill — rolling boulder trap, sphere of annihilation, prismatic wall"
    }
    guidelines = danger_guidelines.get(danger, danger_guidelines["dangerous"])
    level_note = f"\nParty level: ~{party_level}" if party_level else ""

    prompt = f"""Design a creative D&D 5e trap following these guidelines:

Type: {trap_type}
Danger: {danger} ({guidelines}){level_note}
Theme/location: {theme or 'any'}{' — ' + location if location else ''}

Generate a FRESH, UNIQUE trap — do NOT use common names like "Whispering Blade". Think of something original.

Return a single JSON object with these keys:
{{
  "name": "Short evocative trap name (4-6 words) — be creative and original",
  "trigger": "What activates it (1 sentence)",
  "detection_dc": <int, appropriate for {danger}>,
  "detection_skill": "Perception or Investigation",
  "detection_detail": "What observant characters notice (1 sentence)",
  "disarm_dc": <int, same as detection_dc or slightly higher>,
  "disarm_method": "How to disable (e.g. 'Dexterity (thieves\\' tools)', 'Arcana', 'Strength')",
  "disarm_detail": "What happens when disarmed successfully (1 sentence)",
  "effect": "Full mechanical description with save DC, damage, and consequences (2-3 sentences)",
  "save_dc": <int, appropriate for {danger}>,
  "save_ability": "Dexterity, Constitution, Wisdom, etc.",
  "damage": "e.g. '2d10' or '4d10+5'",
  "damage_type": "piercing, fire, poison, etc.",
  "area": "e.g. '10 ft. square' or '30 ft. line'",
  "description": "Flavor text and DM tips (1-2 sentences)"
}}

Use the DMG guidelines for DCs and damage. Make the trap feel unique and cinematic.
Keep within the danger level bounds — don't overpower a setback trap or underpower a deadly one."""

    text = await call_ai(prompt=prompt, label="trap-gen")
    ai = _extract_json(text) if text else None

    if not ai:
        # Fallback: generate a basic trap from the danger presets
        presets = {
            "setback": {"name": f"{theme or 'Simple'} {trap_type.title()} Trap", "save_dc": 10, "damage": "1d10",
                        "trigger": "Pressure plate or trip wire", "effect": "A minor hazard is triggered."},
            "dangerous": {"name": f"{theme or 'Hidden'} {trap_type.title()} Trap", "save_dc": 14, "damage": "2d10",
                          "trigger": "A concealed mechanism activates", "effect": "A dangerous hazard strikes the party."},
            "deadly": {"name": f"{theme or 'Devastating'} {trap_type.title()} Trap", "save_dc": 18, "damage": "4d10",
                       "trigger": "An intricate trigger mechanism fires", "effect": "A lethal hazard threatens to destroy everything in range."},
        }
        preset = presets.get(danger, presets["dangerous"])
        ai = preset

    return JSONResponse({
        "name": ai.get("name", f"{danger.title()} {trap_type.title()} Trap"),
        "trigger": ai.get("trigger", ""),
        "detection_dc": ai.get("detection_dc", 10),
        "detection_skill": ai.get("detection_skill", "Perception"),
        "detection_detail": ai.get("detection_detail", ""),
        "disarm_dc": ai.get("disarm_dc", ai.get("detection_dc", 10)),
        "disarm_method": ai.get("disarm_method", "Dexterity (thieves' tools)"),
        "disarm_detail": ai.get("disarm_detail", ""),
        "effect": ai.get("effect", ""),
        "save_dc": ai.get("save_dc", 15),
        "save_ability": ai.get("save_ability", "Dexterity"),
        "damage": ai.get("damage", ""),
        "damage_type": ai.get("damage_type", ""),
        "area": ai.get("area", ""),
        "description": ai.get("description", ""),
        "danger": danger,
        "type": trap_type,
    })
