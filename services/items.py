"""Item helpers — item-index lookup, description/type generation, curse split.

Extracted from main.py (2026-08-12). Pure functions over item dicts plus
two lazy main imports (ITEM_INDEX, _item_page_map) at call time — mirrors
pdf_generator's pattern and avoids import-time circulars (main ↔ services).
"""

from __future__ import annotations

import re


def _resolve_item_key(item_name: str):
    """Look up an item in ITEM_INDEX, handling SRD reference format.
    'Flavor Name (SRD: Reference)' → extracts Reference for the lookup."""
    from main import ITEM_INDEX
    if not item_name:
        return None
    key = item_name.strip().lower()
    item = ITEM_INDEX.get(key)
    if item:
        return item
    # Try resolving SRD reference
    srd_match = re.search(r'\(srd:\s*(.+?)\)', key)
    if srd_match:
        srd_ref = srd_match.group(1).strip().lower()
        # Normalize: strip special chars for fuzzy matching
        srd_norm = re.sub(r'[—–()\[\]{}]', ' ', srd_ref)
        srd_norm = re.sub(r'\s+', ' ', srd_norm).strip()
        # Try exact match
        item = ITEM_INDEX.get(srd_ref)
        if not item:
            item = ITEM_INDEX.get(srd_norm)
        if not item:
            # Try partial match on normalized ref — prefer best match
            best = None
            best_score = 999
            for k, v in ITEM_INDEX.items():
                k_norm = re.sub(r'[—–()\[\]{}]', ' ', k)
                k_norm = re.sub(r'\s+', ' ', k_norm).strip()
                if srd_norm in k_norm or k_norm in srd_norm:
                    # Score: prefer shorter distance between normalized lengths
                    score = abs(len(k_norm) - len(srd_norm))
                    if score < best_score:
                        best_score = score
                        best = v
            if best:
                return best
        if item:
            return item
    # Try partial match on original key
    for k, v in ITEM_INDEX.items():
        if key in k:
            return v
    # ── Quantity stripping: "Light Crossbow + 20 Bolt" → "Light Crossbow" ──
    qty_stripped = re.sub(r'\s*\+\s*\d+.*', '', key).strip()
    if qty_stripped and qty_stripped != key:
        item = ITEM_INDEX.get(qty_stripped)
        if not item:
            for k, v in ITEM_INDEX.items():
                if qty_stripped in k:
                    item = v
                    break
        if item:
            return item
    # ── Prefix stripping: "Vial of X", "Bag of X" → "X" ──
    for prefix in ("vial of ", "bag of ", "flask of ", "pouch of "):
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            item = ITEM_INDEX.get(suffix)
            if not item:
                for k, v in ITEM_INDEX.items():
                    if suffix in k:
                        item = v
                        break
            if item:
                return item
    # ── Em-dash splitting: "Spell Scroll — Cantrip" → try base + variant ──
    for sep in (" — ", " – ", " – ", " -- "):
        if sep in key:
            parts = key.split(sep, 1)
            base = parts[0].strip()
            variant = parts[1].strip() if len(parts) > 1 else ""
            # Try "base (variant)" key
            paren_key = f"{base} ({variant})"
            item = ITEM_INDEX.get(paren_key)
            if item:
                return item
            # Try just the base name
            item = ITEM_INDEX.get(base)
            if item:
                return item
            # Try variant in parentheses for any key starting with base
            for k, v in ITEM_INDEX.items():
                if k.startswith(base) and variant in k:
                    return v
            # Try partial match for base
            for k, v in ITEM_INDEX.items():
                if base in k:
                    return v
            break
    return None


def _split_curse_text(desc: str) -> tuple[str, str]:
    """Split item description into safe text and hidden curse subsection."""
    if not desc or "curse" not in desc.lower():
        return desc, ""
    m = re.search(
        r"(?ms)(?:^|\n\n|\n|(?<=\.)\s)"
        r"(?:(?:[A-Z][a-z]+(?:'s)?\s+)?Curse\.)",
        desc
    )
    if not m:
        idx = desc.lower().find("curse.")
        if idx < 0:
            return desc, ""
        curse_start = idx
    else:
        curse_start = m.start()
    safe = desc[:curse_start].strip()
    curse = desc[curse_start:].strip()
    return safe, curse


def _build_item_description(item: dict) -> str:
    """Generate a PHB 2014-accurate description for any equipment item.
    Uses SRD desc if available, otherwise derives from metadata."""
    cat = item.get("equipment_category", {}).get("name", "")
    desc_list = item.get("desc", [])
    if desc_list:
        return " ".join(desc_list)

    # ── Weapons ──
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Simple")
        wrange = item.get("weapon_range", "Melee")
        dmg = item.get("damage", {})
        dmg_dice = dmg.get("damage_dice", "")
        dmg_type = (dmg.get("damage_type", {}) or {}).get("name", "")
        props = [p.get("name", "") for p in item.get("properties", [])]

        # Build range string
        range_part = wrange
        normal = item.get("range", {}).get("normal")
        if normal:
            long_range = item.get("range", {}).get("long", "")
            range_part = f"Range {normal}/{long_range} ft"

        # Build properties string
        prop_strs = []
        for p in props:
            if p == "Versatile":
                vdmg = item.get("two_handed_damage", {}).get("damage_dice", "")
                prop_strs.append(f"versatile ({vdmg})" if vdmg else "versatile")
            elif p == "Thrown":
                thr = item.get("throw_range", {})
                tn = thr.get("normal", "")
                tl = thr.get("long", "")
                prop_strs.append(f"thrown (range {tn}/{tl})" if tn else "thrown")
            elif p == "Ammunition":
                prop_strs.append("ammunition")
            elif p == "Finesse":
                prop_strs.append("finesse")
            elif p == "Heavy":
                prop_strs.append("heavy")
            elif p == "Light":
                prop_strs.append("light")
            elif p == "Loading":
                prop_strs.append("loading")
            elif p == "Reach":
                prop_strs.append("reach")
            elif p == "Two-Handed":
                prop_strs.append("two-handed")
            elif p == "Special":
                prop_strs.append("special")
            elif p == "Monk":
                prop_strs.append("monk")
            else:
                prop_strs.append(p.lower())

        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        parts = [f"{wcat} {wrange.lower()} weapon"]
        if dmg_dice:
            parts.append(f"{dmg_dice} {dmg_type.lower()}" if dmg_type else dmg_dice)
        if prop_strs:
            parts.append(". ".join(prop_strs))
        parts.append(f"{weight} lb. {cost_str}.")
        return " ".join(f"{p}{'.' if i==0 and not p.endswith('.') else ''}" if i > 0 and not p.startswith('.') else p for i, p in enumerate(parts)).replace("..", ".").replace(" .", ".")

    # ── Armor ──
    if cat == "Armor":
        ac = item.get("armor_class", {}).get("base", "?")
        armor_cat = item.get("armor_category", "Armor")
        ac_parts = [str(ac)]
        if item.get("armor_class", {}).get("dex_bonus"):
            dex = item["armor_class"]["dex_bonus"]
            ac_parts.append(f"+ Dex modifier (max {item['armor_class'].get('max_bonus', dex)})")
        ac_str = " + ".join(ac_parts)
        notes = []
        if item.get("str_minimum", 0) > 0:
            notes.append(f"Requires STR {item['str_minimum']}")
        if item.get("stealth_disadvantage"):
            notes.append("disadvantage on Stealth")
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        desc = f"{armor_cat} armor. AC {ac_str}. "
        if notes:
            desc += ", ".join(notes) + ". "
        desc += f"{weight} lb. {cost_str}."
        return desc

    # ── Tools ──
    if cat == "Tools":
        tname = item.get("name", "")
        tool_descs = {
            "Alchemist's supplies": "Used for alchemical crafting. Proficiency lets you add your bonus to checks made to identify potions, poisons, and alchemical substances. 8 lb. 50 gp.",
            "Brewer's supplies": "Used for brewing beer and ale. Proficiency lets you add your bonus to checks made to identify or craft beverages. 9 lb. 20 gp.",
            "Calligrapher's supplies": "Used for calligraphy, illuminating manuscripts, and detecting forgeries. Proficiency adds your bonus to checks related to writing. 5 lb. 10 gp.",
            "Carpenter's tools": "Used for woodworking and construction. Proficiency lets you add your bonus to checks to build, repair, or identify wooden structures. 6 lb. 8 gp.",
            "Cartographer's tools": "Used for mapping and charting. Proficiency lets you add your bonus to checks to navigate, create maps, or identify landmarks. 6 lb. 15 gp.",
            "Cobbler's tools": "Used for shoemaking and leatherwork for footwear. Proficiency adds your bonus to checks to repair or identify footwear. 5 lb. 5 gp.",
            "Cook's utensils": "Used for preparing meals. Proficiency lets you add your bonus to checks to cook, identify ingredients, or detect spoiled food. 8 lb. 1 gp.",
            "Glassblower's tools": "Used for shaping molten glass. Proficiency adds your bonus to checks to create, identify, or repair glass objects. 5 lb. 30 gp.",
            "Jeweler's tools": "Used for crafting jewelry and identifying gems. Proficiency lets you add your bonus to checks to appraise jewelry or identify gemstones. 2 lb. 25 gp.",
            "Leatherworker's tools": "Used for working with leather and hides. Proficiency adds your bonus to checks to create, repair, or identify leather goods. 5 lb. 5 gp.",
            "Mason's tools": "Used for stonework and construction. Proficiency lets you add your bonus to checks to build, demolish, or identify stone structures. 8 lb. 10 gp.",
            "Painter's supplies": "Used for painting and creating artwork. Proficiency adds your bonus to checks to create, identify, or authenticate paintings. 5 lb. 10 gp.",
            "Potter's tools": "Used for shaping clay and ceramics. Proficiency adds your bonus to checks to create, identify, or repair ceramic objects. 3 lb. 10 gp.",
            "Smith's tools": "Used for metalworking, forging, and repairing metal objects. Proficiency lets you add your bonus to checks to craft, identify, or repair metal items. 8 lb. 20 gp.",
            "Tinker's tools": "Used for tinkering with small mechanical devices. Proficiency lets you add your bonus to checks to repair or create small mechanical objects. 10 lb. 50 gp.",
            "Weaver's tools": "Used for working with cloth, thread, and textiles. Proficiency adds your bonus to checks to create, identify, or repair fabric items. 5 lb. 1 gp.",
            "Woodcarver's tools": "Used for carving wood into small objects. Proficiency adds your bonus to checks to create, identify, or repair wooden crafts. 5 lb. 1 gp.",
            "Navigator's tools": "Used for navigation at sea. Proficiency lets you add your bonus to checks to determine location, avoid getting lost, and chart courses. 2 lb. 25 gp.",
            "Thieves' tools": "Used for picking locks and disarming traps. Proficiency lets you add your bonus to ability checks made to disarm traps or pick locks. 1 lb. 25 gp.",
            "Disguise kit": "Used for creating disguises and costumes. Proficiency lets you add your bonus to checks made to create a visual disguise. 3 lb. 25 gp.",
            "Forgery kit": "Used for forging documents. Proficiency lets you add your bonus to checks made to create a physical counterfeit of a document. 5 lb. 15 gp.",
            "Herbalism kit": "Used for creating herbal remedies, antitoxins, and potions of healing. Proficiency lets you add your bonus to checks made to identify or apply herbs. 3 lb. 5 gp.",
            "Poisoner's kit": "Used for creating and applying poisons. Proficiency lets you add your bonus to checks made to create, identify, or handle poisons. 2 lb. 50 gp.",
            "Dice set": "A set of dice for games of chance. Proficiency lets you add your bonus to checks made to play that game. 0 lb. 1 sp.",
            "Dragonchess set": "A complex strategy board game popular among nobles. Proficiency lets you add your bonus to checks made to play Dragonchess. 0.5 lb. 1 gp.",
            "Playing card set": "A deck of cards for gambling and games. Proficiency lets you add your bonus to checks made to play card games. 0 lb. 5 sp.",
            "Three-Dragon Ante set": "A betting card game themed around dragons. Proficiency lets you add your bonus to checks to play Three-Dragon Ante. 0 lb. 1 gp.",
            "Bagpipes": "A musical wind instrument using enclosed reeds. Proficiency lets you add your bonus to Performance checks with this instrument. 6 lb. 30 gp.",
            "Drum": "A percussion instrument. Proficiency lets you add bonus to Performance checks. 3 lb. 6 gp.",
            "Dulcimer": "A stringed instrument played with hammers. Proficiency adds bonus to Performance checks. 10 lb. 25 gp.",
            "Flute": "A woodwind instrument. Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Lute": "A stringed instrument similar to a guitar. Proficiency adds bonus to Performance checks. 2 lb. 35 gp.",
            "Lyre": "A small harp-like stringed instrument. Proficiency adds bonus to Performance checks. 2 lb. 30 gp.",
            "Horn": "A brass wind instrument. Proficiency adds bonus to Performance checks. 2 lb. 3 gp.",
            "Pan flute": "A set of graduated pipes. Proficiency adds bonus to Performance checks. 2 lb. 12 gp.",
            "Shawm": "A woodwind instrument (double reed, precursor to oboe). Proficiency adds bonus to Performance checks. 1 lb. 2 gp.",
            "Viol": "A bowed string instrument. Proficiency adds bonus to Performance checks. 1 lb. 30 gp.",
        }
        if tname in tool_descs:
            return tool_descs[tname]
        cost = item.get("cost", {})
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
        weight = item.get("weight", "?")
        return f"A set of tools for {tname.lower()}. {weight} lb. {cost_str}."

    # ── Adventuring Gear ──
    gear_descriptions = {
        "Abacus": "A portable calculating device — a wooden frame with beads on rods. Used for arithmetic and accounting. 2 gp. 2 lb.",
        "Acid (vial)": "As an action, splash the contents of this vial onto a creature within 5 feet, or throw the vial up to 20 feet, shattering it on impact. On a hit, the target takes 2d6 acid damage. 25 gp. 1 lb.",
        "Alchemist's fire (flask)": "A sticky, adhesive fluid that ignites when exposed to air. As an action, throw this flask up to 20 feet, shattering on impact. Target takes 1d4 fire damage at the start of each of its turns until it uses an action to extinguish the flames. 50 gp. 1 lb.",
        "Antitoxin (vial)": "Drink this vial to gain advantage on saving throws against poison for 1 hour. 50 gp.",
        "Arcane focus — Crystal": "An arcane focus is a special item designed to channel the power of arcane spells. A sorcerer, warlock, or wizard can use such an item as a spellcasting focus. 10 gp. 1 lb.",
        "Arcane focus — Orb": "An arcane focus designed to channel the power of arcane spells. 20 gp. 3 lb.",
        "Arcane focus — Rod": "An arcane focus designed to channel arcane spells. 10 gp. 2 lb.",
        "Arcane focus — Staff": "An arcane focus designed to channel arcane spells (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Arcane focus — Wand": "An arcane focus designed to channel arcane spells. 10 gp. 1 lb.",
        "Arrows (20)": "Ammunition for shortbows and longbows. 1 gp. 1 lb. per 20.",
        "Blowgun needles (50)": "Ammunition for blowguns. 1 gp. 1 lb. per 50.",
        "Crossbow bolts (20)": "Ammunition for crossbows. 1 gp. 1.5 lb. per 20.",
        "Sling bullets (20)": "Ammunition for slings. 4 cp. 1.5 lb. per 20.",
        "Backpack": "A sturdy leather backpack that can hold up to 30 pounds of gear. 2 gp. 5 lb.",
        "Ball bearings (bag of 1,000)": "As an action, spill these tiny metal balls to cover a 10-foot square area. Any creature moving across the area must succeed on a DC 10 Dexterity saving throw or fall prone. 1 gp. 2 lb.",
        "Barrel": "A wooden barrel that can hold 40 gallons of liquid or 4 cubic feet of solid goods. 2 gp. 70 lb.",
        "Basket": "A woven container for carrying goods. 4 sp. 2 lb.",
        "Bedroll": "A cloth bedroll and blanket for sleeping. 1 gp. 7 lb.",
        "Bell": "A small hand bell that rings clearly. 1 gp.",
        "Blanket": "A warm woolen blanket. 5 sp. 3 lb.",
        "Block and tackle": "A set of pulleys with a cable threaded through them. Lets you hoist up to four times the normal weight you could lift. 1 gp. 5 lb.",
        "Book": "A leather-bound tome containing lore, records, or stories — typically worth 25 gp depending on content. 25 gp. 5 lb.",
        "Bottle, glass": "A glass bottle that holds 1½ pints of liquid. 2 gp. 2 lb.",
        "Bucket": "A wooden or metal bucket holding 3 gallons. 5 cp. 2 lb.",
        "Caltrops (bag of 20)": "As an action, spread a bag of caltrops to cover a 5-foot square. A creature entering the area must succeed on a DC 15 Dexterity saving throw or stop moving and take 1 piercing damage. 1 gp. 2 lb.",
        "Candle": "Provides dim light in a 5-foot radius for 1 hour. 1 cp.",
        "Case, crossbow bolt": "A wooden case that holds up to 20 crossbow bolts. 1 gp. 1 lb.",
        "Case, map or scroll": "A cylindrical leather case that protects maps, scrolls, or documents from water and wear. 1 gp. 1 lb.",
        "Chain (10 feet)": "A 10-foot iron chain. Has 10 hit points and can be burst with a DC 20 Strength check. 5 gp. 10 lb.",
        "Chalk (1 piece)": "A piece of white chalk for marking surfaces. 1 cp.",
        "Chest": "A wooden chest that holds 12 cubic feet or 300 pounds of gear. Has a lock (DC 15 to pick). 5 gp. 25 lb.",
        "Climber's kit": "Includes special pitons, boot tips, gloves, and a harness. Gives you a climbing speed equal to your walking speed for 10 minutes, once per short rest. While using it, you can't fall more than 25 feet. 25 gp. 12 lb.",
        "Clothes, common": "Simple, durable work clothes — tunic, trousers, boots. 5 sp. 3 lb.",
        "Clothes, costume": "An outfit for a specific role or costume. 5 gp. 4 lb.",
        "Clothes, fine": "High-quality fabric and tailoring suitable for nobility. 15 gp. 6 lb.",
        "Clothes, traveler's": "Sturdy clothes designed for long journeys, with reinforced stitching and multiple pockets. 2 gp. 4 lb.",
        "Component pouch": "A small watertight leather belt pouch with compartments to hold all the material components and other special items you need to cast your spells (except for those with a specific cost). 25 gp. 2 lb.",
        "Crowbar": "Using a crowbar grants advantage on Strength checks where leverage can be applied. 2 gp. 5 lb.",
        "Druidic focus — Sprig of mistletoe": "A druidic focus used to channel nature magic. Druids can use it as a spellcasting focus. 1 gp.",
        "Druidic focus — Totem": "A druidic focus incorporating feathers, fur, bones, and teeth from sacred animals. 1 gp.",
        "Druidic focus — Wooden staff": "A druidic focus (also counts as a quarterstaff). 5 gp. 4 lb.",
        "Druidic focus — Yew wand": "A druidic focus carved from yew wood. 10 gp. 1 lb.",
        "Fishing tackle": "Includes a wooden rod, silken line, corkwood bobbers, steel hooks, lead sinkers, velvet lures, and narrow netting. 1 gp. 4 lb.",
        "Flask or tankard": "A metal container that holds 1 pint of liquid. 2 cp. 1 lb.",
        "Grappling hook": "A metal hook attached to a rope. Throw with a DC 10 Strength (Athletics) check to secure it. 2 gp. 4 lb.",
        "Hammer": "A one-handed metal hammer for driving pitons and nails. 1 gp. 3 lb.",
        "Hammer, sledge": "A two-handed heavy hammer for demolition. 2 gp. 10 lb.",
        "Healer's kit": "A leather pouch containing bandages, salves, and splints. Has 10 uses. As an action, expend one use to stabilize a dying creature without needing a Wisdom (Medicine) check. 5 gp. 3 lb.",
        "Holy symbol — Amulet": "A holy symbol representing a deity or pantheon. A cleric or paladin can use it as a spellcasting focus. 5 gp. 1 lb.",
        "Holy symbol — Emblem": "A holy symbol on a shield or tabard. 5 gp.",
        "Holy symbol — Reliquary": "A holy symbol containing a sacred relic. 5 gp. 2 lb.",
        "Holy water (flask)": "As an action, splash holy water onto a creature within 5 feet, or throw the flask up to 20 feet. On a hit against a fiend or undead, the target takes 2d6 radiant damage. A cleric or paladin can create holy water with a 1-hour ritual using 25 gp of powdered silver. 25 gp. 1 lb.",
        "Hourglass": "A sand-filled glass timer for measuring time in 1-hour increments. 25 gp. 1 lb.",
        "Hunting trap": "A saw-toothed steel trap. As an action, set it. A creature stepping on it must succeed on a DC 13 Dexterity saving throw or take 1d4 piercing damage and stop moving. The creature can be freed with a DC 13 Strength check. 5 gp. 25 lb.",
        "Ink (1 ounce bottle)": "Black ink for writing. 10 gp.",
        "Ink pen": "A writing implement, typically a quill. 2 cp.",
        "Jug or pitcher": "A ceramic container holding 1 gallon of liquid. 2 cp. 4 lb.",
        "Ladder (10-foot)": "A 10-foot wooden ladder. 1 sp. 25 lb.",
        "Lamp": "A hooded lantern casting bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. 5 sp. 2 lb.",
        "Lantern, bullseye": "Casts bright light in a 60-foot cone and dim light for an additional 60 feet for 6 hours on a flask of oil. 10 gp. 2 lb.",
        "Lantern, hooded": "Casts bright light in a 30-foot radius and dim light for an additional 30 feet for 6 hours on a flask of oil. Lowering the hood reduces light to dim in a 5-foot radius. 5 gp. 2 lb.",
        "Lock": "A simple lock with a key. DC 15 Dexterity check with thieves' tools to pick. 10 gp. 1 lb.",
        "Magnifying glass": "A lens for inspecting small details. Grants advantage on ability checks to appraise or inspect small or detailed items. Also useful for starting fires in sunlight. 100 gp.",
        "Manacles": "Metal restraints that bind a Small or Medium creature. Escaping requires a DC 20 Dexterity check; breaking them a DC 20 Strength check. 2 gp. 6 lb.",
        "Mess kit": "A tin box containing a cup, simple cutlery, and a plate. 2 sp. 1 lb.",
        "Mirror, steel": "A polished steel mirror for signaling or grooming. 5 gp. 0.5 lb.",
        "Oil (flask)": "A flask of lantern oil. As an action, splash oil on a creature or pour on the ground (covers a 5-foot square). If lit, burns for 2 rounds dealing 5 fire damage per round. 1 sp. 1 lb.",
        "Paper (one sheet)": "A single sheet of parchment or vellum for writing. 2 sp.",
        "Parchment (one sheet)": "A single sheet of animal-skin parchment. 1 sp.",
        "Perfume (vial)": "A vial of fragrant perfume. 5 gp.",
        "Pick, miner's": "A mining pick for breaking stone. 2 gp. 10 lb.",
        "Piton": "A metal spike driven into rock for climbing. Holds up to 500 lb. 5 cp. 0.25 lb.",
        "Poison, basic (vial)": "Apply to a weapon or up to three pieces of ammunition. The poison retains potency for 1 minute. A creature hit must succeed on a DC 10 Constitution saving throw or take 1d4 poison damage. 100 gp.",
        "Pole (10-foot)": "A 10-foot wooden pole. Useful for prodding suspicious objects from a safe distance. 5 cp. 7 lb.",
        "Pot, iron": "An iron cooking pot holding 1 gallon. 2 gp. 10 lb.",
        "Potion of healing": "Drink this potion as an action to regain 2d4+2 hit points. 50 gp. 0.5 lb.",
        "Pouch": "A small leather or cloth pouch that holds 6 pounds or ⅕ cubic foot of gear. 5 sp. 1 lb.",
        "Quiver": "A leather quiver holding up to 20 arrows or bolts. 1 gp. 1 lb.",
        "Ram, portable": "A portable battering ram. Gives you a +4 bonus on Strength checks to break open doors, and advantage if a second character helps. 4 gp. 35 lb.",
        "Rations (1 day)": "Dried and preserved food suitable for travel — jerky, dried fruit, hardtack, and nuts. 5 sp. 2 lb.",
        "Robes": "Floor-length cloth robes worn by clergy, scholars, and wizards. 1 gp. 4 lb.",
        "Rope, hempen (50 feet)": "50 feet of hempen rope. Has 2 hit points and can be burst with a DC 17 Strength check. 1 gp. 10 lb.",
        "Rope, silk (50 feet)": "50 feet of silk rope. Has 2 hit points and can be burst with a DC 17 Strength check. Lighter and stronger than hempen. 10 gp. 5 lb.",
        "Sack": "A cloth sack holding 30 pounds or 1 cubic foot of gear. 1 cp. 0.5 lb.",
        "Scale, merchant's": "A balance scale with weights for measuring goods. 5 gp. 3 lb.",
        "Sealing wax": "A stick of wax for sealing letters and documents. 5 sp.",
        "Shovel": "A digging tool. 2 gp. 5 lb.",
        "Signal whistle": "A shrill whistle audible up to 600 feet away. 5 cp.",
        "Signet ring": "A ring bearing an engraved family or guild seal for stamping wax. 5 gp.",
        "Soap": "A bar of soap for washing. 2 cp.",
        "Spellbook": "A leather-bound tome essential for wizards to record spells. Contains 100 blank pages. 50 gp. 3 lb.",
        "Spikes, iron (10)": "A set of 10 iron spikes used to wedge doors shut or anchor ropes for climbing. 1 gp. 5 lb.",
        "Spyglass": "A telescope that magnifies distant objects to twice their apparent size. 1,000 gp. 1 lb.",
        "Tent, two-person": "A simple canvas tent for two people. 2 gp. 20 lb.",
        "Tinderbox": "A small box containing flint, fire steel, and tinder. Using it to start a fire requires an action. 5 sp. 1 lb.",
        "Torch": "A torch burns for 1 hour, providing bright light in a 20-foot radius and dim light for an additional 20 feet. Make a melee attack with a lit torch to deal 1 fire damage. 1 cp. 1 lb.",
        "Vial": "A small glass vial holding up to 4 ounces of liquid. 1 gp.",
        "Waterskin": "A leather waterskin holding 4 pints of liquid. 2 sp. 5 lb. (full).",
        "Whetstone": "A stone for sharpening blades. 1 cp. 1 lb.",
    }

    name = item.get("name", "")
    if name in gear_descriptions:
        return gear_descriptions[name]

    # Fallback: use gear category + cost/weight
    subcat = (item.get("gear_category") or {}).get("name", "")
    cost = item.get("cost", {})
    if isinstance(cost, dict):
        cost_str = f"{cost.get('quantity','?')} {cost.get('unit','gp')}"
    else:
        cost_str = str(cost) if cost else "? gp"
    weight = item.get("weight", "?")
    if subcat:
        return f"{subcat}. {weight} lb. {cost_str}."
    return f"{weight} lb. {cost_str}."


def _build_item_type(item: dict) -> str:
    """Get a display type string for an item."""
    cat = item.get("equipment_category", {}).get("name", "")
    if cat == "Weapon":
        wcat = item.get("weapon_category", "Weapon")
        wrange = item.get("weapon_range", "Melee")
        return f"{wcat} {wrange} Weapon"
    if cat == "Armor":
        return item.get("armor_category", cat)
    subcat = (item.get("gear_category") or {}).get("name", "")
    if subcat:
        return subcat
    return cat or "Equipment"


def _resolve_source(item_key: str, fallback: str) -> str:
    """Return source string with page number from map, or fallback."""
    from main import _item_page_map
    mapped = _item_page_map.get(item_key.lower())
    if mapped:
        # Don't replace if fallback already has better info (e.g., specific adventure page)
        if "p." in fallback.lower():
            return fallback
        return mapped
    return fallback


def _extract_srd_dice(item: dict) -> str:
    """Extract damage dice from an SRD item dict."""
    damage = item.get("damage")
    if damage and isinstance(damage, dict):
        dd = damage.get("damage_dice", "")
        if dd:
            return dd
    return ""


def _item_rarity_for_level(level: int) -> list[str]:
    if level <= 4:   return []           # No magic items
    if level <= 10:  return ["uncommon"]
    if level <= 16:  return ["rare", "uncommon"]
    return ["very rare", "rare", "uncommon"]


__all__ = [
    "_resolve_item_key", "_split_curse_text", "_build_item_description",
    "_build_item_type", "_resolve_source", "_extract_srd_dice",
    "_item_rarity_for_level",
]
