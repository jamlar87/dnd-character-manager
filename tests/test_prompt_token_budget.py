"""Every prompt must fit inside SDXL's 77-token window.

This is the guard for the worst bug in this file's history. The house style and the construct cues were
written at length, prompts grew to ~90-97 words (~121-130 CLIP tokens), and SDXL truncates at 77. So the
SUBJECT was cut off the end of the prompt while the style tail survived, and the model — given a style
instruction and no subject — invented one. A construct creature came back as a naked human figure; a
vessel came back as empty desert terrain. Nothing errored; the images looked plausible in isolation.

Neither the prompt tests nor the picture itself caught it, because a truncated prompt still produces a
confident, clean, well-composed image of the wrong thing.

Word counts rather than a tokenizer: the repo's venv has no CLIP tokenizer, and a test that silently
needs network access is worse than one that is slightly conservative. Measured against
openai/clip-vit-large-patch14, this corpus runs at 1.28-1.40 tokens per word, so a 55-word ceiling
holds under 77 tokens with margin. token_check.py in this directory measures the real thing — run it
after editing a prompt builder.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services import portraits, ref_portraits  # noqa: E402

#: 77 tokens at the MEASURED 1.53 tokens/word. Measured against openai/clip-vit-large-patch14: the
#: construct prompts above came out at 69, 69 and 73 tokens for 45, 50 and 48 words. A 55-word budget
#: would have permitted 84 tokens and passed while still truncating, which is the same class of mistake
#: as the bug itself — so this number comes from measurement, not from rounding a guess.
WORD_BUDGET = 48


def _positive_prompts() -> dict[str, str]:
    return {
        "npc": portraits.npc_prompt("Quash Dentdruggle", notes="a gnome tinkerer of no fixed abode"),
        "character_curated": portraits.portrait_prompt("Dwarf", "Barbarian"),
        "character_curated_2": portraits.portrait_prompt("Dragonborn", "Paladin"),
        "character_fallback": portraits.portrait_prompt("Aarakocra", "Commoner"),
        "ref_creature_plain": ref_portraits.prompt_for("creature", "Owlbear", "CR 3"),
        "ref_creature_construct": ref_portraits.prompt_for(
            "creature", "Clockwork Dragon", "CR 12.0 · Huge construct"),
        "ref_creature_golem": ref_portraits.prompt_for(
            "creature", "Alchemical Golem", "CR 8 · construct"),
        "ref_creature_vessel": ref_portraits.prompt_for(
            "creature", "Airship", "CR 0 · Gargantuan construct"),
        "ref_item_construct": ref_portraits.prompt_for("item", "Carriage", "Mounts and Vehicles"),
        "ref_item_plain": ref_portraits.prompt_for("item", "Rope", "Adventuring Gear"),
    }


def test_every_positive_prompt_fits_the_token_window():
    for label, p in _positive_prompts().items():
        words = len(p.split())
        assert words <= WORD_BUDGET, (
            f"{label} is {words} words (~{int(words * 1.4)} tokens), over the {WORD_BUDGET}-word "
            f"budget — SDXL truncates at 77 tokens and the SUBJECT is what gets cut: {p}")


def test_the_negative_prompt_fits_too():
    """The negative is encoded by the same 77-token window."""
    words = len(portraits.COMFY_NEGATIVE.split())
    assert words <= WORD_BUDGET, f"negative prompt is {words} words, over budget"


def test_the_house_style_clauses_are_terse():
    """These are appended to everything, so their length is multiplied across the whole library."""
    for name, clause in (("FANTASY_STYLE", portraits.FANTASY_STYLE), ("BLANK_BG", portraits.BLANK_BG)):
        assert len(clause.split()) <= 12, f"{name} has grown to {len(clause.split())} words"


def test_the_longest_construct_cue_leaves_room_for_the_subject():
    """A cue is the bulk of a reference prompt; if one grows, the subject falls off the end."""
    longest = max(desc for _, desc in ref_portraits.CONSTRUCT_CUES)
    assert len(longest.split()) <= 20, f"construct cue is {len(longest.split())} words"


def test_a_long_record_name_does_not_overflow_the_budget():
    """Real records carry long names and subtitles; the budget must hold for those, not just samples."""
    worst = ref_portraits.prompt_for(
        "creature",
        "Clockwork Oaken Bolter of the Nine Gilded Spires of Mechanus",
        "CR 18.0 · Huge construct with a very long subtitle appended by the ingest pipeline")
    assert len(worst.split()) <= WORD_BUDGET + 12, f"worst case is {len(worst.split())} words: {worst}"
