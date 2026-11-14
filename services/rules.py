"""D&D 5e arithmetic that more than one part of the app needs.

These lived as hand-rolled expressions in routes, services, templates and the PDF
generator — 36 copies of `(score - 10) // 2` and a proficiency table re-exported
from three places. One definition each, imported from here.
"""
from __future__ import annotations

# PHB p.15: proficiency bonus by character level (1-20).
PROFICIENCY_BONUS = {
    1: 2, 2: 2, 3: 2, 4: 2,
    5: 3, 6: 3, 7: 3, 8: 3,
    9: 4, 10: 4, 11: 4, 12: 4,
    13: 5, 14: 5, 15: 5, 16: 5,
    17: 6, 18: 6, 19: 6, 20: 6,
}


def ability_mod(score) -> int:
    """Ability modifier for a score (PHB p.7): floor((score - 10) / 2).

    Floor, not truncate: a score of 1 gives -5, and ``int(-0.5)`` is 0, which is
    why this uses ``//`` instead of ``/``. None/unparseable scores read as 10 —
    the sheet renders NULL columns and would otherwise raise mid-template.
    """
    try:
        value = int(score)
    except (TypeError, ValueError):
        return 0
    return (value - 10) // 2


def proficiency_bonus(level) -> int:
    """Proficiency bonus for a character level, clamped to the 1-20 table.

    Out-of-table levels are clamped rather than extrapolated: a level 30 NPC is
    still +6, and a level 0 typo is +2, which is what every caller assumed when
    it wrote ``PROFICIENCY_BONUS.get(level, 2)``.
    """
    try:
        lvl = int(level)
    except (TypeError, ValueError):
        return PROFICIENCY_BONUS[1]
    lvl = max(1, min(20, lvl))
    return PROFICIENCY_BONUS[lvl]
