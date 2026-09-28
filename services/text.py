"""Text helpers shared by routes, services and scripts.

Kept dependency-free (stdlib only) on purpose: `import main` builds the entity index
and regenerates the DM library asset at import time, so anything both a route and a
script needs has to live somewhere lighter than `main` / `routes/*`.
"""

import re
import unicodedata

_WS = re.compile(r"\s+")


def alpha_key(name) -> str:
    """Sort key for a browsable list of names — what a reader means by alphabetical.

    `.lower()` is codepoint order, and that is what the Monsters tab was sorted with:
    every accented name filed AFTER all the unaccented ones, so "Dáin Ironfoot" landed
    past "Dancing Flame" and the list read as unsorted at a glance. Fold the accents,
    casefold, and read a comma as a space so an inverted personal name files beside its
    base form ("Zombie, Lord" with the other zombies rather than after every
    "Zombie m…"). A nameless row sorts last instead of collapsing onto an empty key.
    """
    s = str(name or "").strip()
    if not s:
        return "\uffff"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return _WS.sub(" ", s.casefold().replace(",", " ")).strip()
