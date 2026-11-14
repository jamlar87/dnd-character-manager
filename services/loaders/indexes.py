"""Manual magic items -> SRD_MAGIC_ITEMS and the ITEM_INDEX lookup table.

ITEM_INDEX is the key-normalised view the item endpoints read, so an item that
is registered here is immediately resolvable by name, rarity and source.
"""

from __future__ import annotations

from services.items import _resolve_source
from services.loaders.files import _load_manual_json
from services.loaders.sources import _normalize_item_rarity, _normalize_manual_source

__all__ = ["merge_manual_items"]


def merge_manual_items(meta: dict) -> None:
    """Append magic_items.json to the item registry and its index."""
    from core.data import ITEM_INDEX, SRD_MAGIC_ITEMS

    # ── Magic Items ── append to SRD_MAGIC_ITEMS (ITEM_INDEX auto-picks them up)
    manual_items = _load_manual_json("magic_items.json")
    existing_item_names = {i.get("name", "").lower() for i in SRD_MAGIC_ITEMS}
    for item in manual_items:
        if item.get("name", "").lower() not in existing_item_names:
            # Enrich source from _source_manual + pdf_map
            source = (item.get("source") or "").strip()
            if item.get("_source_manual"):
                source = _normalize_manual_source(source, item["_source_manual"], meta)
            # Normalize rarity (handle non-standard strings from ingested data)
            norm_rarity = _normalize_item_rarity(item.get("rarity") or "unknown")

            # Heuristic: classify remaining "unknown" items by type
            if norm_rarity == "unknown":
                raw_type_lower = (item.get("type") or "").lower()
                name_lower = (item.get("name") or "").lower()
                # Consumables → common
                if any(w in raw_type_lower for w in ("potion", "scroll", "drug", "oil", "elixir", "dust", "powder")):
                    norm_rarity = "common"
                elif any(w in name_lower for w in ("potion", "scroll", "oil of", "dust of")):
                    norm_rarity = "common"
                # Wondrous items → uncommon (default for misc magic)
                elif "wondrous" in raw_type_lower or "artefact" in raw_type_lower:
                    norm_rarity = "uncommon"
                # Weapons/armor → uncommon
                elif any(w in raw_type_lower for w in ("weapon", "sword", "axe", "bow", "spear", "dagger", "mace", "hammer", "staff")):
                    norm_rarity = "uncommon"
                elif any(w in raw_type_lower for w in ("armor", "armour", "shield", "helm", "plate", "mail", "chain", "leather")):
                    norm_rarity = "uncommon"
                # Rings/rods/wands → uncommon
                elif any(w in raw_type_lower for w in ("ring", "rod", "wand")):
                    norm_rarity = "uncommon"
                else:
                    norm_rarity = "uncommon"  # Safe default for unknown items

            # Normalize category (extract base from compound types)
            import re as _re2
            raw_type = (item.get("type") or "Wondrous item").strip()
            type_lower = raw_type.lower()
            base_m = _re2.match(r'([\w][\w\s]*?)(?:\s*[\(,]|$)', type_lower)
            base_cat = base_m.group(1).strip() if base_m else type_lower
            CAT_NORM = {
                "armour": "armor", "wondrous artefact": "wondrous item",
                "ammunition": "weapon", "arrow": "weapon", "great spear": "weapon",
                "great bow": "weapon", "great shield": "armor", "great axe": "weapon",
                "short sword": "weapon", "long sword": "weapon", "spear": "weapon",
                "axe": "weapon", "helm": "wondrous item", "mirror": "wondrous item",
                "ring-mail": "armor", "coat of mail": "armor", "scale hauberk": "armor",
                "shield": "armor", "drug": "potion", "primal boon": "wondrous item",
                "enchanted quality": "wondrous item", "close combat weapon": "weapon",
                "armor (shield)": "armor", "artifact": "wondrous item",
                "armor (light": "armor", "armor (medium": "armor", "armor (heavy": "armor",
            }
            norm_cat = CAT_NORM.get(base_cat, base_cat)

            # Map to SRD format
            mapped = {
                "name": item.get("name", ""),
                "desc": [item.get("description", "")],
                "rarity": {"name": norm_rarity},
                "equipment_category": {"name": norm_cat},
                "source": source,
            }
            SRD_MAGIC_ITEMS.append(mapped)
            existing_item_names.add(item["name"].lower())
    if manual_items:
        print(f"  + Magic Items: {len(manual_items)}")
        # Rebuild ITEM_INDEX entries for manual magic items
        for item in manual_items:
            name = item.get("name", "")
            if not name:
                continue
            key = name.lower()
            if key not in ITEM_INDEX:
                source = (item.get("source") or "").strip()
                if item.get("_source_manual"):
                    source = _normalize_manual_source(source, item["_source_manual"], meta)
                rarity = _normalize_item_rarity(item.get("rarity") or "varies")
                entry_dict = {
                    "name": name,
                    "type": "Magic Item",
                    "description": item.get("description", ""),
                    "cost": "—",
                    "weight": None,
                    "rarity": rarity,
                    "source": _resolve_source(key, source or ""),
                }
                if item.get("dice"):
                    entry_dict["dice"] = item["dice"]
                ITEM_INDEX[key] = entry_dict
