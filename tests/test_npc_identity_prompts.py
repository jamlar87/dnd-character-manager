"""An NPC portrait must match the NPC the record describes.

Regression: two real portraits came back wrong, for two unrelated reasons.

  "Khelkur the Gull" is recorded as a DWARF, and was drawn as a white eagle in armour. Repeating the
  same prompt produced a dwarf, so the prompt was not deterministically wrong — it merely permitted
  the bird reading via the nickname, and sometimes took it. The race was in the subtitle and was
  never used as the subject.

  "Insight Acuere" is a tiefling whose own description says "She has resistance to fire damage", and
  was drawn male. There is no gender field anywhere in the 799 NPC records, so nothing said otherwise,
  and the description sentence sat past the 15-word snippet cap, so even the prose was cut off.

The rules below are deliberately conservative. A prompt that says nothing leaves the model to choose
something plausible; a prompt that says something WRONG fights the model. So absent evidence means
absent detail, and 293 of 799 records intentionally carry no gender.
"""
from services import ref_portraits, portraits


# --- gender -------------------------------------------------------------------------------------

def test_pronoun_yields_gender():
    assert ref_portraits.gender_from_text(
        "The tiefling professor uses the scholarly mastermind stat block. She has resistance to "
        "fire damage and darkvision.") == "female"
    assert ref_portraits.gender_from_text("He draws his blade and charges.") == "male"


def test_no_pronoun_yields_nothing():
    """Khelkur's description says 'who's', which is not evidence of anything."""
    assert ref_portraits.gender_from_text(
        "A neutral evil, dwarf occult silvertongue who's one of the masters of the Consortium.") == ""
    assert ref_portraits.gender_from_text("") == ""


def test_conflicting_pronouns_yield_nothing():
    """Both sets present means the prose is about more than one person — do not guess."""
    assert ref_portraits.gender_from_text("She struck him down.") == ""


def test_pronoun_inside_a_word_is_not_a_pronoun():
    assert ref_portraits.gender_from_text("The Shepherd tended sheep.") == ""


def test_gender_is_read_before_the_word_cap():
    """The whole reason Insight Acuere was male: her 'She' is beyond the 15-word snippet cap."""
    snippet = ("The tiefling professor uses the scholarly mastermind stat block (see appendix A), "
               "with these changes: She has resistance to fire damage.")
    assert len(snippet.split()) > 15, "precondition: the pronoun is past the cap"
    prompt = ref_portraits.prompt_for("npc", "Insight Acuere", "Tiefling · Ally", snippet)
    assert "female" in prompt


# --- race ---------------------------------------------------------------------------------------

def test_race_is_read_from_the_subtitle():
    assert ref_portraits.race_from_subtitle("Dwarf · Occult Silvertongue") == "Dwarf"
    assert ref_portraits.race_from_subtitle("Tiefling · Ally") == "Tiefling"


def test_filing_labels_are_not_races():
    """A wrong race actively fights the description, so junk must not reach the prompt."""
    for junk in ("any race", "varies", "Chapter 4 | The Jewel of Hope",
                 "(Call of the Netherdeep)", "", "   "):
        assert ref_portraits.race_from_subtitle(junk) == "", junk


# --- the nickname that became an animal ---------------------------------------------------------

def test_animal_nickname_is_dropped():
    assert ref_portraits.name_for_portrait("Khelkur the Gull") == "Khelkur"


def test_non_animal_nicknames_are_kept():
    """'the Trapper' and 'the Crown' describe a person and must not be silently deleted."""
    for name in ("Old Salt the Trapper", "Lady Vex the Crown", "Thorin Oakenshield"):
        assert ref_portraits.name_for_portrait(name) == name, name


# --- end to end ---------------------------------------------------------------------------------

def test_dwarf_named_gull_is_prompted_as_a_dwarf():
    prompt = ref_portraits.prompt_for(
        "npc", "Khelkur the Gull", "Dwarf · Occult Silvertongue",
        "A neutral evil, dwarf occult silvertongue who's one of the masters of the Consortium.")
    assert "dwarf humanoid" in prompt, prompt
    assert "the Gull" not in prompt, prompt
    assert "Khelkur" in prompt


def test_species_leads_and_humanoid_is_stated():
    """Order is the fix: early tokens carry the most weight in these checkpoints."""
    prompt = portraits.npc_prompt("Nobody Special", race="Halfling")
    assert "halfling humanoid" in prompt
    assert prompt.index("halfling") < prompt.index("Nobody Special")


def test_npc_prompt_still_accepts_the_old_signature():
    """role= callers (the DM NPC path) must keep working."""
    prompt = portraits.npc_prompt("Ayo Jabe", notes="", race="Human", role="ally")
    assert "human humanoid" in prompt
    assert "ally" in prompt
