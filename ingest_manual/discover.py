from __future__ import annotations
from pathlib import Path
import re
from .core import MANUALS_DIR


# ═══════════════════════════════════════════════════════════════════════════════
# PDF Discovery
# ═══════════════════════════════════════════════════════════════════════════════

def discover_manuals() -> list[dict]:
    """Walk the manuals directory and return unique PDFs with metadata."""
    if not MANUALS_DIR.exists():
        print(f"ERROR: Manuals dir not found: {MANUALS_DIR}")
        return []

    # Non-content patterns to skip
    # Patterns that apply to the PDF's own filename only (not parent directory)
    _skip_filename_only = [
        r'(?i)screen\b',           # GM/loremaster screens
        r'(?i)character.?sheet',   # blank or pre-filled character sheets
        r'(?i)jpg.?map.?pack',     # JPG-based map bundles
    ]
    # Patterns that apply to filename OR parent directory
    _skip_filename_or_parent = [
        r'(?i)\bmap[s]?\b',      # map packs, battle maps, region maps
        r'(?i)endpaper',           # decorative endpapers
        r'(?i)\bcover\b',         # standalone cover art
    ]

    # Pass 1: collapse duplicates by title, keeping the SHALLOWEST path.
    # Every PDF is reachable twice — a bare symlink in manuals/ plus the
    # DnD-Manuals/ directory it points at. pdf_map consumers resolve `path`
    # against MANUALS_DIR, so a nested entry ("DnD-Manuals/x.pdf") doubles the
    # directory and silently defeats cache re-extraction.
    best: dict[str, Path] = {}
    for f in sorted(MANUALS_DIR.rglob("*.pdf", recurse_symlinks=True)):
        title = f.stem.replace("_", " ").replace("-", " ").replace("  ", " ").strip()
        parent = str(f.parent.name)
        # Skip non-content PDFs (maps, endpapers, character sheets, covers, screens)
        if any(re.search(pat, title) for pat in _skip_filename_only):
            continue
        if any(re.search(pat, title) or re.search(pat, parent) for pat in _skip_filename_or_parent):
            continue
        key = title.lower()
        cur = best.get(key)
        if cur is None or len(f.relative_to(MANUALS_DIR).parts) < len(cur.relative_to(MANUALS_DIR).parts):
            best[key] = f

    # Pass 2: slugs + metadata (stable order by title)
    seen_slugs: dict[str, int] = {}
    manuals = []
    for _key, f in sorted(best.items()):
        title = f.stem.replace("_", " ").replace("-", " ").replace("  ", " ").strip()

        # Derive short slug/label, guarantee uniqueness
        slug = _derive_slug(title)
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0

        manuals.append({
            "title": title,
            "filename": f.name,
            "path": str(f.relative_to(MANUALS_DIR)),
            "abs_path": str(f),
            "slug": slug,
            "size_kb": f.stat().st_size // 1024,
            "mtime": f.stat().st_mtime,
        })

    return manuals


def _derive_slug(title: str) -> str:
    """Derive a short label/slug from a manual title."""
    title_lower = title.lower()
    for kw, slug in [
        ("player's handbook", "PHB"),
        ("dungeon master's guide", "DMG"),
        ("monster manual", "MM"),
        ("xanathar's guide", "XGE"),
        ("volo's guide", "VGM"),
        ("mordenkainen's tome", "MTF"),
        ("sword coast", "SCAG"),
        ("elemental evil", "EEPC"),
        ("guildmasters' guide", "GGR"),
        ("wayfinders guide", "WGE"),
        ("tortle package", "TTP"),
        ("lost mine", "LMoP"),
        ("tomb of annihilation", "ToA"),
        ("dragon heist", "WDH"),
        ("hoard of the dragon queen", "HotDQ"),
        ("rise of tiamat", "RoT"),
        ("wild sheep", "WSC"),
        ("ancestral weapon", "AW"),
        # ── Kobold Press ──
        ("creature codex", "CC"),
        ("tome of beasts", "ToB"),
        ("book of ebon tides", "EBT"),
        ("courts of the shadow fey", "CSF"),
        ("deep magic", "DPM"),
        ("tales from the shadows", "TFS"),
        ("tales of the margreve", "TOM"),
        ("margreve players guide", "MPG"),
        ("wrath of the river king", "WRK"),
        ("expanding the ranger", "ETR"),
        ("shadows of the dusk queen", "SDQ"),
        ("marauders of the margreve", "MOM"),
        ("encounters in avernus", "EIA"),
        ("saltmarsh encounters", "SME"),
        ("ratatosk", "RAT"),
        ("warlock lair", "WLL"),
        ("warlock lairs", "WLL"),
        ("warlock bestiary", "WLB"),
        # ── TLOTR / Adventures in Middle-earth ──
        ("adventuresinmiddle earthloremastersguide", "LMG"),
        ("adventuresinmiddle earthplayersguide", "AIPG"),
        ("adventures in middle-earth", "AIME"),
        ("adventuresinmiddle", "AIME"),
        ("bree land", "BLRG"),
        ("erebor adventures", "EREA"),
        ("ereboradventures", "EREA"),
        ("eriador adventures", "ERIA"),
        ("eriadoradventures", "ERIA"),
        ("lonely mountain", "LMRG"),
        ("lonelymountain", "LMRG"),
        ("loremaster", "LMG"),
        ("mirkwood campaign", "MWC"),
        ("mirkwoodcampaign", "MWC"),
        ("player's guide", "AIPG"),
        ("playersguide", "AIPG"),
        ("rhovanion", "RRG"),
        ("rivendell", "RVR"),
        ("the road goes ever on", "RGEO"),
        ("theroadgoeseveron", "RGEO"),
        ("wilderland adventures", "WLA"),
        ("wilderlandadventures", "WLA"),
        ("eaves of mirkwood", "EOM"),
        ("eavesofmirkwood", "EOM"),
        # ── DM's Guild / AL modules ──
        ("defiance in phlan", "DDP"),
        ("secrets of sokol keep", "SSK"),
        ("shadows over the moonsea", "SOM"),
        ("dues for the dead", "DFD"),
        # ── Critical Role ──
        ("call of the netherdeep", "CotN"),
        ("wildemount", "EGW"),
        ("taldorei", "TCSR"),
        # ── Hit Point Press ──
        ("floral dragons", "FGFD"),
        ("field guide", "FGFD"),
    ]:
        if kw in title_lower:
            return slug
    # Fallback: camel-case abbreviation from first letters, up to 6 chars
    return "".join(w[0] for w in title.split() if w[0].isalpha())[:6].upper()
