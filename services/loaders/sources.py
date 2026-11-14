"""Normalisation and enrichment rules for ingested manual records.

Source strings, item rarity, spell shape, recharge wording and description
quality — pure string/dict work: callers pass a record in, these return the
value to store. Nothing here touches disk or a registry.

(Not to be confused with services/sources.py, which is display-side
source hygiene for records that never got a real book.)
"""

from __future__ import annotations

import re

__all__ = [
    "_DND_KEYWORDS", "_count_keywords", "_should_replace_description",
    "_subclass_name_key", "_existing_subclass_name", "_normalize_manual_source",
    "_normalize_recharge", "_normalize_item_rarity", "_normalize_manual_spell",
]

_DND_KEYWORDS = [
    "saving throw", "attack roll", "bonus action", "ability check",
    "damage", "resistance", "immunity", "vulnerability", "concentration",
    "spell slot", "proficiency", "advantage", "disadvantage",
    "hit points", "armor class", "initiative", "reaction",
    "ritual", "cantrip", "melee", "ranged",
]


def _count_keywords(text: str) -> int:
    """Count D&D mechanical keyword occurrences in text."""
    t = text.lower()
    return sum(t.count(kw) for kw in _DND_KEYWORDS)


def _should_replace_description(existing: str, new: str) -> bool:
    """Return True if the new description is substantively better than existing.
    Requires: new is >50% longer AND has more mechanical keywords."""
    if not new or not new.strip():
        return False
    if not existing or not existing.strip():
        # Existing is empty — new wins regardless
        return True

    len_ratio = len(new) / max(len(existing), 1)
    new_kw = _count_keywords(new)
    existing_kw = _count_keywords(existing)

    # New must be >50% longer AND have more (or equal) keywords
    if len_ratio > 1.5 and new_kw >= existing_kw:
        return True
    # Or new is dramatically longer (>3x)
    if len_ratio > 3.0:
        return True
    # Or existing has zero keywords and new has some
    if existing_kw == 0 and new_kw > 0:
        return True

    return False

# Articles and punctuation are dropped so the two spellings of one subclass
# collide: "Circle of Stars" / "Circle of the Stars", "Genie" / "The Genie".
_SUBCLASS_ARTICLE_RE = re.compile(r"\b(?:the|an?)\b")
_SUBCLASS_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def _subclass_name_key(name: str) -> str:
    """Comparison shape for a subclass name, ignoring articles and punctuation."""
    return _SUBCLASS_PUNCT_RE.sub("", _SUBCLASS_ARTICLE_RE.sub(" ", (name or "").lower()))


def _existing_subclass_name(existing: list[str], name: str) -> str | None:
    """Return the already-listed subclass this name is a second spelling of.

    The merge below only skips a LITERAL repeat (`sc_name not in subs`), so a
    re-extraction that titled the same subclass differently added a twin: the
    wizard listed "Circle of Stars" AND "Circle of the Stars" (identical content),
    and the Warlock patron appeared as both "Genie" and "The Genie" — where the
    listed "Genie" had no features at all, because the extracted features were
    hung off the other spelling. Matching on the article-stripped name lets the
    entry fold into the name already in use instead of duplicating it.
    """
    key = _subclass_name_key(name)
    if not key:
        return None
    for candidate in existing:
        if candidate != name and _subclass_name_key(candidate) == key:
            return candidate
    return None

def _normalize_manual_source(source: str, source_manual: str, meta: dict) -> str:
    """Rebuild source string with proper book prefix when it's missing or malformed.

    Handles bare page references like 'Page 9', 'p.141', 'page 20-21' by
    reconstructing them as '{display_name} p.{page}' using _source_manual metadata.
    """
    from core.data import _get_source_slug_map
    source = (source or "").strip()
    if not source_manual:
        return source
    is_bare_page = bool(re.match(r'^(page|p\.?|pg)\b', source, re.IGNORECASE))
    if source and "Unknown" not in source and not is_bare_page:
        return source  # Already has a valid format
    slug = source_manual
    page_match = re.search(r'(\d+)', source) if source else None
    page_str = f" p.{page_match.group(1)}" if page_match else ""
    book_info = (meta.get("pdf_map", {}) if isinstance(meta, dict) else {}).get(slug, {})
    if book_info:
        title = book_info.get("title", slug)
        title = re.sub(r"^D&D 5E\s*[-–—]\s*", "", title)
        # Prefer curated display name from slug map over raw PDF title
        slug_map = _get_source_slug_map()
        slug_info = slug_map.get(slug, {})
        display = slug_info.get("display", "")
        title = display if display else title
        return f"{title}{page_str}"
    return f"{slug}{page_str}"

def _normalize_recharge(recharge: str) -> str:
    """Normalize racial trait recharge strings to canonical forms.
    Canonical values: 'short', 'long', 'combat', 'special', 'dawn'.
    """
    r = recharge.lower().strip()
    if "short" in r and "long" in r:
        return "short"
    if "short" in r:
        return "short"
    if "long" in r:
        return "long"
    if "combat" in r:
        return "combat"
    if "special" in r:
        return "special"
    if "dawn" in r:
        return "dawn"
    return r

def _normalize_item_rarity(raw_rarity: str) -> str:
    """Normalize ingested magic-item rarity strings to standard 5e values.

    Maps exotic labels (fabled/unique), scaling descriptors (+1/+2/+3), and
    mundane-flavored text onto the canonical set: common, uncommon, rare,
    very rare, legendary, artifact, varies, unknown.
    """
    rarity_lower = (raw_rarity or "unknown").strip().lower()
    RARITY_NORM = {
        "fabled": "legendary", "unique": "artifact",
        "none (non-magical)": "common", "faint conjuration": "common",
        "faint abjuration": "common", "moderate": "uncommon",
        "minor magical property": "common",
    }
    if rarity_lower in RARITY_NORM:
        return RARITY_NORM[rarity_lower]
    if "varies" in rarity_lower or "see" in rarity_lower:
        return "varies"
    if "fabled" in rarity_lower:
        return "legendary"
    if rarity_lower.startswith("uncommon") and ("+" in rarity_lower or "," in rarity_lower):
        return "varies"  # +1/+2/+3 scaling items like "uncommon (+1), rare (+2), or very rare (+3)"
    if rarity_lower.startswith(("rare", "very rare", "legendary", "artifact", "common", "uncommon")) and ("+" in rarity_lower or "," in rarity_lower):
        return "varies"
    std_rarities = {"common", "uncommon", "rare", "very rare", "legendary", "artifact", "unknown", "varies"}
    return rarity_lower if rarity_lower in std_rarities else "unknown"

def _normalize_manual_spell(spell: dict) -> dict:
    """Shape a manual spell entry to match SRD format in the merged registry.

    Manual spells.json entries store description under ``description`` (string),
    higher-level text under ``higher_levels`` (string), components as a
    comma-joined string, and have no ``index``. SRD entries use ``desc`` /
    ``higher_level`` lists, a components list + ``material``, and a slug index.
    The DM Tools spells tab depends on these fields, so normalize before merge.
    """
    # index slug — SRD convention: lowercase, apostrophes dropped, spaces → '-'
    if not spell.get("index"):
        name = spell.get("name", "")
        idx = name.replace("\u2019", "").replace("\u2018", "").replace("'", "")
        idx = idx.lower().replace(" ", "-").replace("/", "-")
        spell["index"] = idx

    # description → desc (list of paragraphs, SRD style)
    if "desc" not in spell and spell.get("description"):
        desc = spell.get("description")
        if isinstance(desc, str):
            spell["desc"] = [p for p in desc.split("\n\n") if p.strip()] or [desc]
        elif isinstance(desc, list):
            spell["desc"] = desc

    # higher_levels → higher_level (list, SRD style)
    if "higher_level" not in spell and spell.get("higher_levels"):
        hl = spell["higher_levels"]
        spell["higher_level"] = [hl] if isinstance(hl, str) else (hl or [])

    # components string "V, S, M (material)" → components list + material
    comps = spell.get("components")
    if isinstance(comps, str):
        m = re.search(r"\((.*)\)", comps)
        spell["material"] = m.group(1) if m else ""
        letters = [c.strip() for c in re.sub(r"\(.*\)", "", comps).split(",") if c.strip()]
        spell["components"] = letters or []

    return spell
