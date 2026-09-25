"""A page repair must not offer a class spell list as the place a spell is described.

The PHB prints "SORCERER SPELLS" and friends before the descriptions chapter, so a spell's name
looks like a heading on the list page (p.64) long before its entry (p.235). The repair tool
proposed exactly that for Dominate Person — a page that names the spell and explains nothing. A
flagged record is better than a confidently wrong page.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import repair_page_citations as rpc  # noqa: E402


LIST_PAGE = """SORCERER SPELLS
Cantrips
Dancing Lights
Friends
3rd Level
Dominate Person
Fireball
4th Level
Polymorph
"""

ENTRY_PAGE = """DOMINATE PERSON
5th-level enchantment
Casting Time: 1 action
Range: 60 feet
Duration: Concentration, up to 1 minute
You attempt to beguile a humanoid that you can see within range. It must
succeed on a Wisdom saving throw or be charmed by you for the duration.
"""


def test_class_list_page_is_not_offered_as_a_spells_page():
    pages = {64: LIST_PAGE, 235: ENTRY_PAGE}
    assert rpc.looks_like_class_list(LIST_PAGE) is True
    assert rpc.looks_like_class_list(ENTRY_PAGE) is False
    assert rpc.heading_page(pages, "Dominate Person") == 235
    assert rpc.find_in_book(pages, "Dominate Person") == 235


def test_a_description_still_wins_when_no_list_is_present():
    pages = {235: ENTRY_PAGE}
    assert rpc.find_in_book(pages, "Dominate Person") == 235


# Only the first page of a class list carries a header, so the header guard misses the rest.
HEADERLESS_LIST = """Cantrips
Blade Ward
Chill Touch
Friends
True Strike
1st Level
Burning Hands
Charm Person
Magic Missile
"""


def test_a_headerless_list_page_is_rejected_by_what_follows_the_name():
    lines = HEADERLESS_LIST.split("\n")
    idx = lines.index("Blade Ward")
    assert rpc.looks_like_list_context(lines, idx) is True
    assert rpc.looks_like_class_list(HEADERLESS_LIST) is False  # no header to catch it
    pages = {105: HEADERLESS_LIST, 235: ENTRY_PAGE}
    assert rpc.heading_page(pages, "Blade Ward") is None


def test_a_longer_word_containing_the_name_is_not_a_heading():
    """The cron applies this repair on a timer, so a substring match corrupts data unattended.

    "BLESSING" (warlock class features, p.110) contained the spell "Bless" and was proposed over
    the real entry on p.223.
    """
    pages = {110: "BLESSING\nStarting at 1st level, when you reduce a hostile creature\nto 0 hit points, you gain temporary hit points equal to your Charisma modifier.\n"}
    assert rpc.heading_page(pages, "Bless") is None
    assert rpc.find_in_book(pages, "Bless") is None


def test_a_real_entry_is_not_mistaken_for_a_list():
    lines = ENTRY_PAGE.split("\n")
    assert rpc.looks_like_list_context(lines, 0) is False


