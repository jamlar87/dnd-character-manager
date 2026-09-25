"""Every prompt must be fantasy-coded and ask for a blank background.

The rule exists because it was broken visibly: a dwarf barbarian's portrait came back as a figure in
a motorcycle racing suit standing in a desert. The cause was in this very code — the character prompts
ended "Rich colors, detailed background bokeh", which REQUESTS a decorated backdrop and gives the
model somewhere to put a scene. The reference prompts already said "plain parchment background", so
only the character path was wrong; these tests check every path so a future edit cannot fix one and
miss another.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services import portraits, ref_portraits  # noqa: E402

FANTASY = "fantasy"
BLANK = "blank"


def _all_prompts() -> dict[str, str]:
    """One prompt from each builder, so a fix to one path can't pass while another stays wrong."""
    return {
        "npc": portraits.npc_prompt("Quash Dentdruggle", notes="a tinkerer"),
        "character_curated": portraits.portrait_prompt("Dwarf", "Barbarian"),
        "character_fallback": portraits.portrait_prompt("Aarakocra", "Commoner"),
        "ref_creature_plain": ref_portraits.prompt_for("creature", "Owlbear", "CR 3"),
        "ref_creature_construct": ref_portraits.prompt_for("creature", "Clockwork Dragon", "Huge construct"),
        "ref_item_construct": ref_portraits.prompt_for("item", "Carriage", "Mounts and Vehicles"),
        "ref_item_plain": ref_portraits.prompt_for("item", "Rope", "Adventuring Gear"),
    }


def test_every_prompt_is_fantasy_coded():
    for label, p in _all_prompts().items():
        assert FANTASY in p.lower(), f"{label} is not fantasy-coded: {p[:160]}"


def test_every_prompt_asks_for_a_blank_background():
    for label, p in _all_prompts().items():
        assert BLANK in p.lower(), f"{label} does not ask for a blank background: {p[:160]}"


def test_no_prompt_still_requests_a_decorated_backdrop():
    """The regression itself: these phrases ask for scenery, which the model then invents."""
    for label, p in _all_prompts().items():
        low = p.lower()
        for banned in ("bokeh", "detailed background", "parchment background"):
            assert banned not in low, f"{label} still requests a backdrop via {banned!r}"


CURATED = (("Dwarf", "Barbarian"), ("Dwarf", "Cleric"), ("Dwarf", "Fighter"),
           ("Elf", "Wizard"), ("Elf", "Ranger"), ("Elf", "Rogue"), ("Human", "Paladin"),
           ("Human", "Fighter"), ("Half-Orc", "Barbarian"), ("Tiefling", "Warlock"),
           ("Dragonborn", "Paladin"), ("Dragonborn", "Sorcerer"))


def test_no_curated_prompt_names_a_setting_anywhere():
    """The general guard.

    Each of the 11 curated prompts originally ended by naming a place — "Snow-capped peaks and storm
    clouds behind", "Stormlit cathedral behind", "Shadowy ruins at midnight". Those are landscape
    instructions, and they contradict "no scenery"; the model picks whichever it prefers. Listing the
    dead phrases catches today's text, but this checks the RESULT for every entry, so a future edit
    that adds a setting fails here rather than in the art.
    """
    scenery = ("behind", "backdrop", "interior", "peaks", "ruins", "cathedral", "temple",
               "fortress", "bokeh", "sky")
    # Two words were removed from this list after it failed on CORRECT prompts, and both are worth
    # remembering: "glow" matched "candlelight glow", and "forest" matched "leather armor in forest
    # greens" — a garment colour. Light and colour words describe the subject, which the house style
    # wants; only place-words belong here. A false positive here would push someone to weaken a
    # good prompt, which is worse than the scenery it was guarding against.
    for race, cls in CURATED:
        p = portraits.portrait_prompt(race, cls).lower()
        for word in scenery:
            assert word not in p, f"{race}/{cls} still names scenery via {word!r}"


def test_removals_do_not_leave_broken_punctuation():
    """"centred, , painterly" reads as damage to a diffusion model and wastes prompt tokens."""
    for label, p in _all_prompts().items():
        for bad in (", ,", " ,", ",,", " .", "..", "( ,", "on a,", " a,", " ,", ". ,"):
            assert bad not in p, f"{label} has broken punctuation {bad!r}"


def test_the_dead_scenery_list_is_actually_exercised():
    """A removal list nobody hits is a removal list that silently stopped working."""
    text = "Snow-capped peaks and storm clouds behind. Stormlit cathedral behind."
    out = portraits.house_style(text)
    assert "peaks" not in out.lower() and "cathedral" not in out.lower()


def test_contradictory_background_instructions_are_not_both_present():
    """Appending "blank background" after "detailed background bokeh" gives two opposite orders."""
    p = portraits.house_style("A wizard. Rich colors, detailed background bokeh.")
    assert "bokeh" not in p.lower()
    assert "blank" in p.lower()


def test_the_blank_background_clause_names_the_things_that_go_wrong():
    # Short on purpose: this is appended to every prompt and SDXL truncates at 77 tokens. "no scenery"
    # and "no people" are the two that matter most — the first is a blank background, the second is how
    # a construct creature came back as a human figure. See test_prompt_token_budget.py.
    for banned in ("no scenery", "no people", "no text"):
        assert banned in portraits.BLANK_BG.lower()


def test_the_negative_prompt_excludes_photos_and_modern_scenes():
    """The desert racing suit was a photo; the negative prompt is what applies that globally."""
    low = portraits.COMFY_NEGATIVE.lower()
    for banned in ("photo", "photorealistic", "modern clothing", "racing suit", "scenery", "horizon"):
        assert banned in low, f"negative prompt should exclude {banned!r}"


def test_house_style_is_idempotent_enough():
    """Applying twice must not stack two contradictory background orders."""
    once = portraits.house_style("A dwarf barbarian.")
    twice = portraits.house_style(once)
    assert twice.count(portraits.BLANK_BG) == 1 or "blank" in twice.lower()
    assert "bokeh" not in twice.lower()
