"""Manual subclasses -> CLASSES[..]["subclasses"] and SUBCLASS_FEATURES.

Also fills feature descriptions/sources, folding a second spelling of an
existing subclass into the name already listed instead of adding a twin.
"""

from __future__ import annotations

from data import FEATURE_DESCRIPTIONS, FEATURE_SOURCES

from services.loaders.files import _load_manual_json
from services.loaders.sources import _existing_subclass_name, _should_replace_description

__all__ = ["merge_manual_subclasses"]


def merge_manual_subclasses(meta: dict) -> None:
    """Append subclasses.json to their classes."""
    from core.data import CLASSES, LIMITED_USE, SUBCLASS_FEATURES

    # ── Subclasses ── append to CLASSES[class_name]["subclasses"] + descriptions
    manual_subclasses = _load_manual_json("subclasses.json")
    for sc in manual_subclasses:
        sc_name = sc.get("name", "")
        parent_class = sc.get("class", "")
        if not sc_name or not parent_class:
            continue
        if parent_class not in CLASSES:
            print(f"  ⚠ Subclass '{sc_name}' references unknown class '{parent_class}' — skipping")
            continue
        subs = CLASSES[parent_class].setdefault("subclasses", [])
        descs = CLASSES[parent_class].setdefault("subclass_descs", {})
        srcs = CLASSES[parent_class].setdefault("_subclass_sources", {})
        # Fold a second spelling of a subclass already listed into that name, so
        # its features/description/source land on the entry the user can pick
        # instead of creating a twin (see _existing_subclass_name).
        _already_listed = _existing_subclass_name(subs, sc_name)
        if _already_listed:
            sc_name = _already_listed
        # Skip base progression entries (name == class) — they carry base features,
        # not a real subclass choice. Still extract features/descriptions below.
        _is_base_progression = (sc_name == parent_class)
        # Skip subclasses whose name is itself a known base class (e.g. "Wanderer"
        # extracted as a Ranger subclass, but Wanderer is an AiME standalone class).
        _is_known_class = (sc_name in CLASSES and sc_name != parent_class)
        if sc_name not in subs and not _is_base_progression and not _is_known_class:
            subs.append(sc_name)
        descs[sc_name] = sc.get("description", "")
        sc_source = sc.get("source", "")
        if sc_source:
            # Clean up bad sources: bare page numbers, Unknown markers
            ref_manual = sc.get("_source_manual", "")
            if ref_manual and meta.get("pdf_map", {}).get(ref_manual):
                book_title = meta["pdf_map"][ref_manual]["title"]
                for prefix in ("D&D 5E - ", "D&D 5E-", "DnD 5E - "):
                    if book_title.startswith(prefix):
                        book_title = book_title[len(prefix):]
                        break
                if sc_source.startswith("p.") or sc_source.startswith("p "):
                    sc_source = f"{book_title} {sc_source}"
                elif "p." in sc_source or "p " in sc_source:
                    pass  # already has a page reference, keep as-is (e.g., "PHB p.97")
                elif "Unknown" in sc_source or "Part 1" in sc_source or len(sc_source) < 12:
                    sc_source = book_title
            srcs[sc_name] = sc_source

        # Populate SUBCLASS_FEATURES from extracted feature data
        features = sc.get("features", [])
        if features:
            by_level: dict[int, list[str]] = {}
            for feat in features:
                lvl = feat.get("level", 0)
                fname = feat.get("name", "")
                fdesc = feat.get("description", "")
                if fname and lvl > 0:  # skip L0 "atonement" meta-features
                    by_level.setdefault(lvl, []).append(fname)
                    # Store description for lookup (replace hardcoded if ingested is better)
                    if fdesc:
                        key = fname.lower()
                        existing = FEATURE_DESCRIPTIONS.get(key, "")
                        if not existing or _should_replace_description(existing, fdesc):
                            if existing and _should_replace_description(existing, fdesc):
                                pass  # Ingested version is better, will overwrite
                            FEATURE_DESCRIPTIONS[key] = fdesc
                            # Record the provenance in the same guarded branch as
                            # the text, so it always describes what is displayed.
                            if sc_source:
                                FEATURE_SOURCES[key] = sc_source
                        # Also store subclass-specific key for disambiguation
                        sc_key = f"{sc_name}::{key}"
                        FEATURE_DESCRIPTIONS[sc_key] = fdesc
                    # Register limited-use subclass features
                    fuses = feat.get("uses")
                    frecharge = feat.get("recharge", "")
                    # Coerce uses to int if it's a string (LLM sometimes outputs "3")
                    try:
                        fuses = int(fuses) if fuses else 0
                    except (ValueError, TypeError):
                        fuses = 0
                    if fuses > 0 and frecharge:
                        key = fname.lower()
                        if key not in LIMITED_USE:
                            LIMITED_USE[key] = {"min": fuses, "max": fuses,
                                "recharge": "short" if "short" in frecharge.lower() else "long",
                                "class": parent_class, "per": "fixed"}
            if by_level and sc_name not in SUBCLASS_FEATURES:
                SUBCLASS_FEATURES[sc_name] = by_level
    # Merge base progression features (name == class) into all subclasses
    # so AiME classes like Slayer/Warden get their base features regardless of subclass.
    for parent_cls in list(SUBCLASS_FEATURES.keys()):
        if parent_cls in CLASSES:
            base_feats = SUBCLASS_FEATURES.get(parent_cls, {})
            if base_feats:
                for sc_name in CLASSES[parent_cls].get("subclasses", []):
                    if sc_name != parent_cls and sc_name in SUBCLASS_FEATURES:
                        for lvl, names in base_feats.items():
                            existing = SUBCLASS_FEATURES[sc_name].setdefault(lvl, [])
                            for n in names:
                                if n not in existing:
                                    existing.append(n)
    if manual_subclasses:
        print(f"  + Subclasses: {len(manual_subclasses)}")
