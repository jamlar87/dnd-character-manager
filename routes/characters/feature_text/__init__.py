"""Feature description text for the character sheet.

Moved out of routes/characters/all.py. Subclass text is split into parts
at entry boundaries; the merged dict keeps the original insertion order.
"""

from .channel_divinity import CHANNEL_DIVINITY_DESCRIPTIONS
from .subclass_part1 import SUBCLASS_PART1
from .subclass_part2 import SUBCLASS_PART2
from .subclass_part3 import SUBCLASS_PART3
from .subclass_part4 import SUBCLASS_PART4
from .subclass_part5 import SUBCLASS_PART5
from .subclass_part6 import SUBCLASS_PART6
from .subclass_part7 import SUBCLASS_PART7

SUBCLASS_FEATURE_DESCRIPTIONS: dict[str, str] = {
    **SUBCLASS_PART1, **SUBCLASS_PART2, **SUBCLASS_PART3, **SUBCLASS_PART4, **SUBCLASS_PART5, **SUBCLASS_PART6, **SUBCLASS_PART7,
}
