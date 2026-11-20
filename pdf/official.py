"""Fill the official WotC fillable character sheet (AcroForm fields)."""

import os

from services.rules import ability_mod

from .layout import _draw_appendix_page

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "..", "..", "media", "james", "SlowDisk1tb", "home-move", "DnD-Manuals", "5E_CharacterSheet_Fillable.pdf")


def _resolve_template():
    """Find the official fillable PDF template."""
    candidates = [
        _TEMPLATE_PATH,
        "/media/james/SlowDisk1tb/home-move/DnD-Manuals/5E_CharacterSheet_Fillable.pdf",
        os.path.join(_REPO_ROOT, "5E_CharacterSheet_Fillable.pdf"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Official WotC fillable sheet not found. "
        "Download from https://media.wizards.com/2016/dnd/downloads/5E_CharacterSheet_Fillable.pdf"
    )


# ── Save/skill check-box name mapping (matched by page y-position) ──
_SAVE_CHECKBOXES = {
    "Strength": "Check Box 11",
    "Dexterity": "Check Box 18",
    "Constitution": "Check Box 19",
    "Intelligence": "Check Box 20",
    "Wisdom": "Check Box 21",
    "Charisma": "Check Box 22",
}
# Also by abbreviation
_SAVE_ABBR_CHECKBOXES = {"STR": "Check Box 11", "DEX": "Check Box 18", "CON": "Check Box 19",
                         "INT": "Check Box 20", "WIS": "Check Box 21", "CHA": "Check Box 22"}

_SKILL_CHECKBOXES = {
    "Acrobatics": "Check Box 23",
    "Animal Handling": "Check Box 24",
    "Arcana": "Check Box 25",
    "Athletics": "Check Box 26",
    "Deception": "Check Box 27",
    "History": "Check Box 28",
    "Insight": "Check Box 29",
    "Intimidation": "Check Box 30",
    "Investigation": "Check Box 31",
    "Medicine": "Check Box 32",
    "Nature": "Check Box 33",
    "Perception": "Check Box 34",
    "Performance": "Check Box 35",
    "Persuasion": "Check Box 36",
    "Religion": "Check Box 37",
    "Sleight of Hand": "Check Box 38",
    "Stealth": "Check Box 39",
    "Survival": "Check Box 40",
}

# Spell fields on page 3: named Spells 10XXX where XXX encodes row.
# We fill spells level-by-level; the first few are enough for most characters.
# If more space is needed, add lower-priority cells.
_SPELL_FIELDS_BY_LEVEL = {
    0: ["Spells 1014", "Spells 1015", "Spells 1016", "Spells 1017", "Spells 1018", "Spells 1019", "Spells 1020", "Spells 1021"],
    1: ["Spells 1022", "Spells 1023", "Spells 1024", "Spells 1025", "Spells 1026", "Spells 1027", "Spells 1028", "Spells 1029"],
    2: ["Spells 1030", "Spells 1031", "Spells 1032", "Spells 1033", "Spells 1034", "Spells 1035", "Spells 1036", "Spells 1037"],
    3: ["Spells 1038", "Spells 1039", "Spells 1040", "Spells 1041", "Spells 1042", "Spells 1043", "Spells 1044", "Spells 1045"],
    4: ["Spells 1046", "Spells 1047", "Spells 1048", "Spells 1049", "Spells 1050", "Spells 1051", "Spells 1052", "Spells 1053"],
    5: ["Spells 1054", "Spells 1055", "Spells 1056", "Spells 1057", "Spells 1058", "Spells 1059", "Spells 1060", "Spells 1061"],
    6: ["Spells 1062", "Spells 1063", "Spells 1064", "Spells 1065", "Spells 1066", "Spells 1067", "Spells 1068", "Spells 1069"],
    7: ["Spells 1070", "Spells 1071", "Spells 1072", "Spells 1073", "Spells 1074", "Spells 1075", "Spells 1076", "Spells 1077"],
    8: ["Spells 1078", "Spells 1079", "Spells 1080", "Spells 1081", "Spells 1082", "Spells 1083", "Spells 1084", "Spells 1085"],
    9: ["Spells 1086", "Spells 1087", "Spells 1088", "Spells 1089", "Spells 1090", "Spells 1091", "Spells 1092", "Spells 1093"],
}

# Slot fields: SlotsTotal 19-27 = levels 1-9, SlotsRemaining 19-27 = levels 1-9
_SLOT_TOTAL_FIELDS = {i: f"SlotsTotal {18+i}" for i in range(1, 10)}


def _skill_field_name(name):
    """Map D&D skill name to the PDF field name."""
    lookup = {
        "Animal Handling": "Animal",
        "Sleight of Hand": "SleightofHand",
    }
    return lookup.get(name, name)


def _mod_str(val):
    """Format an ability modifier as signed string."""
    if val >= 0:
        return f"+{val}"
    return str(val)

def fill_official_sheet(char_data, output_path=None):
    """Fill the official WotC fillable character sheet PDF with character data.

    Args:
        char_data: dict from build_char_data()
        output_path: optional path to save PDF. If None, returns bytes.

    Returns:
        PDF bytes if output_path is None, else None.
    """
    import tempfile

    from pypdf import PdfReader, PdfWriter

    template_path = _resolve_template()
    reader = PdfReader(template_path)
    writer = PdfWriter(clone_from=template_path)

    # ── BUILD FIELD-NAME MAPPING (normalize trailing spaces) ────────
    # The template has some field names with trailing spaces (e.g. "Race ", "Perception ")
    actual_field_names = {}
    for n in reader.get_fields() or {}:
        actual_field_names[n.strip()] = n
    _resolve_fn = lambda name: actual_field_names.get(name, name)

    d = char_data

    # ── PAGE 1 FIELDS ────────────────────────────────────────────

    fields = {}

    # Identity
    fields["CharacterName"] = d.get("name", "")
    classes = d.get("class_levels", {}) or {}
    if isinstance(classes, str):
        import json
        classes = json.loads(classes) if classes else {}
    class_str = d.get("class_name", "")
    lvl = d.get("level", 1)
    if classes and isinstance(classes, dict):
        parts = [f"{cls} {l}" for cls, l in sorted(classes.items()) if l > 0]
        if parts:
            fields["ClassLevel"] = " / ".join(parts)
        else:
            fields["ClassLevel"] = f"{class_str} {lvl}" if class_str else f"Level {lvl}"
    else:
        fields["ClassLevel"] = f"{class_str} {lvl}" if class_str else f"Level {lvl}"
    race = d.get("race", "")
    subrace = d.get("subrace", "")
    fields["Race"] = f"{subrace} {race}" if subrace else race
    fields["Background"] = d.get("background", "")
    fields["Alignment"] = d.get("alignment", "")[:15]
    fields["PlayerName"] = d.get("player_name", d.get("user_id", ""))
    fields["XP"] = str(d.get("xp", d.get("experience", "")))

    # Ability scores
    ab_map = {"STR": "strength", "DEX": "dexterity", "CON": "constitution",
              "INT": "intelligence", "WIS": "wisdom", "CHA": "charisma"}
    for abbr, attr in ab_map.items():
        score = d.get(attr, 10)
        mod = ability_mod(score)
        fields[abbr] = str(score)
        # Template uses CHamod (lowercase 'a') not CHAmod
        mod_field = "CHamod" if abbr == "CHA" else f"{abbr}mod"
        fields[mod_field] = _mod_str(mod)

    # Saves
    save_profs = set()
    raw = d.get("save_proficiencies", []) or []
    for s in raw:
        if isinstance(s, dict):
            save_profs.add(s.get("name", "").lower())
        elif isinstance(s, str):
            save_profs.add(s.lower())

    pb = d.get("proficiency_bonus", 2)
    # Build save-to-ability mapping: save name -> abbreviation
    _SAVE_TO_ABBR = {"strength": "STR", "dexterity": "DEX", "constitution": "CON",
                     "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA"}
    for abbr, attr in ab_map.items():
        base_mod = ability_mod(d.get(attr, 10))
        save_name = attr
        is_prof = save_name in save_profs or abbr.lower() in save_profs
        save_mod = base_mod + pb if is_prof else base_mod
        fields[f"ST {save_name.capitalize()}"] = _mod_str(save_mod)
        if is_prof:
            cb = _SAVE_ABBR_CHECKBOXES.get(abbr) or _SAVE_CHECKBOXES.get(save_name.capitalize())
            if cb:
                fields[cb] = "/Yes"

    # Skills
    skills_data = d.get("skills", {}) or {}
    if isinstance(skills_data, str):
        import json
        skills_data = json.loads(skills_data) if skills_data else {}
    skill_profs = {}
    if isinstance(skills_data, dict):
        skill_profs = {k.lower(): v for k, v in skills_data.items()}
    elif isinstance(skills_data, list):
        for s in skills_data:
            if isinstance(s, str):
                skill_profs[s.lower()] = 2  # proficient
            elif isinstance(s, dict):
                skill_profs[s.get("name", "").lower()] = s.get("proficiency", 2)

    skill_ability_map = {
        "Acrobatics": "DEX", "Animal Handling": "WIS", "Arcana": "INT",
        "Athletics": "STR", "Deception": "CHA", "History": "INT",
        "Insight": "WIS", "Intimidation": "CHA", "Investigation": "INT",
        "Medicine": "WIS", "Nature": "INT", "Perception": "WIS",
        "Performance": "CHA", "Persuasion": "CHA", "Religion": "INT",
        "Sleight of Hand": "DEX", "Stealth": "DEX", "Survival": "WIS",
    }

    for skill_name, abbr in skill_ability_map.items():
        attr = ab_map[abbr]  # "DEX" → "dexterity"
        ab_score = d.get(attr, 10)
        ab_mod = ability_mod(ab_score)
        prof_val = skill_profs.get(skill_name.lower(), 0)
        if isinstance(prof_val, str):
            prof_val = 2 if prof_val.lower() in ("proficient", "expertise") else 0
        total_mod = ab_mod
        if prof_val == 2:
            total_mod += pb
        elif prof_val > 2:  # expertise
            total_mod += pb * 2
        field_name = _skill_field_name(skill_name)
        fields[field_name] = _mod_str(total_mod)
        if prof_val >= 2 and skill_name in _SKILL_CHECKBOXES:
            fields[_SKILL_CHECKBOXES[skill_name]] = "/Yes"

    # Combat stats
    fields["AC"] = str(d.get("ac", 10))
    fields["Initiative"] = _mod_str(d.get("initiative", ability_mod(d.get("dexterity", 10))))
    fields["Speed"] = str(d.get("speed", 30))
    fields["HPMax"] = str(d.get("hp_max", d.get("hp", 1)))
    fields["HPCurrent"] = str(d.get("hp_current", d.get("hp", 1)))
    fields["HPTemp"] = str(d.get("hp_temp", ""))
    fields["HD"] = str(d.get("hit_dice_type", "1d8"))
    hd_total = d.get("hit_dice_total", d.get("level", 1))
    fields["HDTotal"] = str(hd_total)
    fields["ProfBonus"] = _mod_str(pb)
    fields["Passive"] = str(d.get("passive_perception", 10))
    fields["Inspiration"] = str(d.get("inspiration", ""))

    # Weapons (up to 3 slots)
    attacks = d.get("attacks_data", []) or []
    if isinstance(attacks, str):
        import json
        attacks = json.loads(attacks) if attacks else []
    weapon_slots = ["Wpn Name", "Wpn Name 2", "Wpn Name 3"]
    atk_slots = ["Wpn1 AtkBonus", "Wpn2 AtkBonus", "Wpn3 AtkBonus"]
    dmg_slots = ["Wpn1 Damage", "Wpn2 Damage", "Wpn3 Damage"]
    for i in range(3):
        if i < len(attacks):
            atk = attacks[i]
            fields[weapon_slots[i]] = atk.get("name", "")
            atk_bonus = atk.get("attack_bonus") if atk.get("attack_bonus") is not None else atk.get("bonus", 0)
            fields[atk_slots[i]] = _mod_str(atk_bonus)
            fields[dmg_slots[i]] = atk.get("damage", "")
    # Overflow attacks into AttacksSpellcasting text area
    extra_attacks = []
    for i, atk in enumerate(attacks):
        if i < 3:
            # Already handled above
            continue
        atk_bonus = atk.get("attack_bonus") if atk.get("attack_bonus") is not None else atk.get("bonus", 0)
        extra_attacks.append(f"{atk.get('name', '?')} +{atk_bonus} {atk.get('damage', '')}")
    attack_text = "\n".join(extra_attacks) if extra_attacks else ""

    # Spellcasting info (always shown on AttacksSpellcasting area)
    sa = d.get("spell_ability", "")
    spell_save = d.get("spell_save_dc", 0)
    spell_atk = d.get("spell_attack_bonus", 0)
    if sa:
        spell_line = f"DC {spell_save} ATK +{spell_atk} ({sa})"
        if attack_text:
            attack_text = spell_line + "\n" + attack_text
        else:
            attack_text = spell_line
    fields["AttacksSpellcasting"] = attack_text

    # Money
    fields["CP"] = str(d.get("cp", 0))
    fields["SP"] = str(d.get("sp", 0))
    fields["EP"] = str(d.get("ep", 0))
    fields["GP"] = str(d.get("gp", 0))
    fields["PP"] = str(d.get("pp", 0))

    # ── PAGE 1 NARRATIVE BOXES ─────────────────────────────────────
    fields["PersonalityTraits"] = str(d.get("personality_traits", ""))
    fields["Ideals"] = str(d.get("ideals", ""))
    fields["Bonds"] = str(d.get("bonds", ""))
    fields["Flaws"] = str(d.get("flaws", ""))

    # Proficiencies & Languages
    prof_lines = []
    for key, label in [("armor_proficiencies", "Armor"), ("weapon_proficiencies", "Weapons"),
                        ("tool_proficiencies", "Tools"), ("languages", "Languages")]:
        vals = d.get(key, []) or []
        if isinstance(vals, str):
            import json
            vals = json.loads(vals) if vals else []
        if vals:
            prof_lines.append(f"{label}: {', '.join(v for v in vals if isinstance(v, str))}")
    fields["ProficienciesLang"] = "\n".join(prof_lines)

    # Equipment (condensed)
    inv = d.get("inventory", []) or []
    if isinstance(inv, str):
        import json
        inv = json.loads(inv) if inv else []
    equip_lines = [f"{i.get('name', '?')} x{i.get('quantity', 1)}" for i in inv[:30] if isinstance(i, dict)]
    equip_str = ", ".join(equip_lines)
    if d.get("has_equipment_appendix"):
        if equip_str:
            equip_str += "\n... See Appendix"
    fields["Equipment"] = equip_str

    # Features & Traits (condensed)
    has_feat_appendix = bool(d.get("full_feature_text", "").strip())
    # Use condensed_features (names only) with appendix ref — user wanted
    # short descriptions OR "See Appendix". With the ref, names + ref is enough.
    feat_text = d.get("condensed_features", "") or ""
    if has_feat_appendix and feat_text:
        feat_text = str(feat_text)
        if feat_text:
            feat_text += "\n... See Appendix"
    fields["Features and Traits"] = str(feat_text)

    # ── PAGE 2 FIELDS ──────────────────────────────────────────────
    fields["CharacterName 2"] = d.get("name", "")
    fields["Age"] = str(d.get("age", ""))
    fields["Height"] = str(d.get("height", ""))
    fields["Weight"] = str(d.get("weight", ""))
    fields["Eyes"] = str(d.get("eyes", ""))
    fields["Skin"] = str(d.get("skin", ""))
    fields["Hair"] = str(d.get("hair", ""))
    fields["FactionName"] = str(d.get("faction", ""))
    fields["Allies"] = str(d.get("allies", ""))

    # Backstory
    backstory = str(d.get("backstory", "") or "")
    pd = d.get("personality_data", {}) or {}
    if isinstance(pd, str):
        import json
        pd = json.loads(pd) if pd else {}
    backstory_extras = pd.get("backstory", "")
    if backstory_extras and backstory_extras not in backstory:
        if backstory:
            backstory += "\n\n" + backstory_extras
        else:
            backstory = backstory_extras
    fields["Backstory"] = backstory

    # Feats & Traits page 2
    feat_long = str(d.get("full_feature_text", "") or "")
    if feat_long.strip():
        if feat_long:
            feat_long += "\n... See Appendix"
    fields["Feat+Traits"] = feat_long

    # Treasure
    treasure = str(d.get("treasure", "") or "")
    fields["Treasure"] = treasure

    # ── PAGE 3: SPELL SHEET ────────────────────────────────────────
    spells = d.get("spells", []) or []
    if isinstance(spells, str):
        import json
        spells = json.loads(spells) if spells else []

    if spells:
        # Fill spellcasting info on page 3
        fields["SpellcastingAbility 2"] = d.get("spell_ability", "")
        fields["SpellSaveDC  2"] = str(d.get("spell_save_dc", 0))
        fields["SpellAtkBonus 2"] = _mod_str(d.get("spell_attack_bonus", 0))
        fields["Spellcasting Class 2"] = d.get("class_name", "")

        # Group spells by level
        by_level = {}
        for spell in spells:
            if isinstance(spell, (list, tuple)):
                name, level = spell[0], spell[1]
            elif isinstance(spell, dict):
                name = spell.get("name", "")
                level = spell.get("level", 0)
            else:
                continue
            by_level.setdefault(int(level), []).append(name)

        # Fill slot totals and used
        slot_data = d.get("spell_slot_data", {}) or {}
        if isinstance(slot_data, str):
            import json
            slot_data = json.loads(slot_data) if slot_data else {}
        slots_by_level = slot_data.get("by_level", {}) if isinstance(slot_data, dict) else {}
        slots_used = d.get("spell_slots_used", {}) or {}
        if isinstance(slots_used, str):
            import json
            slots_used = json.loads(slots_used) if slots_used else {}

        for lvl in range(1, 10):
            total = slots_by_level.get(str(lvl), 0) or 0
            fields[_SLOT_TOTAL_FIELDS[lvl]] = str(total)

        # Fill spell names into fields
        for lvl in range(0, 10):
            names = by_level.get(lvl, [])
            slot_fields = _SPELL_FIELDS_BY_LEVEL.get(lvl, [])
            for i, sname in enumerate(names):
                if i < len(slot_fields):
                    fields[slot_fields[i]] = sname

        # Tag the spellcasting class field with appendix note
        has_spell_appendix = bool(d.get("spell_appendix", "").strip())
        if has_spell_appendix:
            sc_val = fields.get("Spellcasting Class 2", "")
            if sc_val:
                fields["Spellcasting Class 2"] = sc_val + " (See Appendix)"

    # ── APPLY FIELDS TO ALL PAGES ──────────────────────────────────
    # Map stripped names to actual field names (handle trailing spaces)
    field_dict = {_resolve_fn(k): v for k, v in fields.items() if v}

    # Calculate font size for each text field so text fits its bounding box
    from pypdf.generic import NameObject, TextStringObject
    for page in writer.pages:
        for ann_ref in page.get("/Annots") or []:
            try:
                obj = ann_ref.get_object()
                ft = obj.get("/FT")
                if not (ft and str(ft) == "/Tx"):
                    continue
                t = str(obj.get("/T", "")).strip()
                val = fields.get(t)
                if not val:
                    continue
                rect = obj.get("/Rect")
                if not rect:
                    continue
                bw = float(rect[2]) - float(rect[0])
                bh = float(rect[3]) - float(rect[1])
                if bw < 1 or bh < 1:
                    continue
                # Try font sizes from 9 down to 4, pick the largest that fits all text
                text_len = len(str(val))
                chosen_size = 4
                for fs in range(9, 3, -1):
                    cpl = bw / (fs * 0.55)
                    if cpl < 1: continue
                    lines_needed = max(1, text_len / cpl)
                    h_needed = lines_needed * (fs * 1.2)
                    if h_needed <= bh:
                        chosen_size = fs
                        break
                new_da = f"/Helv {chosen_size} Tf 0 g"
                obj[NameObject("/DA")] = TextStringObject(new_da)
            except Exception:
                pass

    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            field_dict,
            auto_regenerate=True,
        )

    # Flatten: remove AcroForm so fields aren't editable
    metadata = reader.metadata
    if metadata:
        writer.add_metadata(metadata)

    # Write output
    use_temp = output_path is None
    path = output_path or tempfile.mktemp(suffix=".pdf")

    with open(path, "wb") as f:
        writer.write(f)

    # ── APPENDIX PAGES (full-feature / spell / equipment / allies overflow) ──
    # Generate appendix content with ReportLab, then merge
    appendices_needed = (
        d.get("full_feature_text", "").strip()
        or d.get("spell_appendix", "").strip()
        or d.get("equipment_appendix", "").strip()
        or d.get("summons_appendix", "").strip()
        or d.get("allies_appendix", "").strip()
    )
    if appendices_needed:
        from reportlab.lib.pagesizes import letter as rl_letter
        from reportlab.pdfgen import canvas as rl_canvas
        appendices_path = tempfile.mktemp(suffix="_appendices.pdf")
        ac = rl_canvas.Canvas(appendices_path, pagesize=rl_letter)
        ac.setTitle(f"D&D Character Sheet Appendices — {d.get('name', 'Character')}")
        ac.setAuthor("Character Manager")

        if d.get("spell_appendix", "").strip():
            _draw_appendix_page(ac, d, "SPELL APPENDIX", "spell_appendix")
            ac.showPage()
        if d.get("equipment_appendix", "").strip():
            _draw_appendix_page(ac, d, "EQUIPMENT APPENDIX", "equipment_appendix")
            ac.showPage()
        if d.get("full_feature_text", "").strip():
            _draw_appendix_page(ac, d, "CLASS FEATURE APPENDIX", "full_feature_text",
                                subtitle=f"{d.get('class_name','')} {d.get('level','')} | {d.get('race','')} | {d.get('background','')}")
        if d.get("has_summons_appendix") and d.get("summons_appendix", "").strip():
            ac.showPage()
            _draw_appendix_page(ac, d, "SUMMONS APPENDIX", "summons_appendix",
                                subtitle=f"{d.get('class_name','')} {d.get('level','')} | {d.get('race','')} | {d.get('background','')}")
        if d.get("allies_appendix", "").strip():
            ac.showPage()
            _draw_appendix_page(ac, d, "ALLIES & ORGANIZATIONS", "allies_appendix")
        ac.save()

        if os.path.getsize(appendices_path) > 1000:  # non-empty
            from pypdf import PdfWriter as MergeWriter
            merger = MergeWriter()
            merger.append(path)
            merger.append(appendices_path)
            with open(path, "wb") as f:
                merger.write(f)
            merger.close()

        try:
            os.unlink(appendices_path)
        except OSError:
            pass

    if use_temp:
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data
