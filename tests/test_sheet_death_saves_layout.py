"""Death saves live inside the Hit Points card and only show at 0 HP.

The section used to be a standalone vital-block further down the sidebar, and it
was always rendered — so a character at full HP saw a tracker for a state they
were not in. These guards pin both halves: the DOM placement and the client-side
visibility rule that follows HP changes.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHEET = (REPO / "templates" / "sheet.html").read_text()
SHEET_JS = (REPO / "static" / "sheet.js").read_text()
STATUS_JS = (REPO / "static" / "sheet" / "status.js").read_text()


def test_death_saves_block_is_nested_in_hp_card():
    start = SHEET.index("top-hp card")
    block = SHEET.index('id="death-saves-block"')
    vitals = SHEET.index("top-vitals", start)
    assert start < block < vitals, "death-saves-block must sit inside the HP card"


def test_death_saves_block_hidden_above_zero_hp():
    for line in SHEET.splitlines():
        if 'id="death-saves-block"' in line:
            assert '{% if (character.hp_current|int) > 0 %} style="display:none"{% endif %}' in line
            return
    raise AssertionError("death-saves-block missing")


def test_no_standalone_death_saves_section_left():
    # The old copy followed the Hit Dice section as its own vital-block; a second
    # #ds-body would make the JS visibility toggle target the wrong element.
    assert SHEET.count('id="ds-body"') == 1
    assert SHEET.count('id="death-saves-block"') == 1


def test_hp_handlers_sync_death_saves_visibility():
    assert "function syncDeathSaves(" in SHEET_JS
    assert re.search(r"syncDeathSaves\(hp\)", SHEET_JS)
    # spend/max hit die, short rest and long rest all change HP in status.js
    assert STATUS_JS.count("syncDeathSaves(") >= 4
