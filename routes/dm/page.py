"""The DM Tools page itself and the campaign page it links to."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
import json
import core.data as _core_data  # MANUALS_BASE is read live: tests repoint it
from pathlib import Path
from core.config import STATIC
from core.security import require_user
from core.http import _render, static_asset_version
from core.db import get_db
from routes.characters import _load_monster_cache
from services.text import alpha_key
from services.reference_assets import ensure_source_slug_map_asset
from core.data import (
    _get_named_item_types, _get_source_slug_map, _load_manual_json, _scan_manual_pdfs,
    MANUAL_TRAPS, SRD_SPELLS, ITEM_INDEX, SRD_MAGIC_ITEMS,
)
from summon_templates import SUMMON_TEMPLATES
from services.schema import HAS_PORTRAIT_SQL, hydrate_json_columns
from services.sources import clean_source_display
from ._shared import NPC_LIST_COLS, _manual_npc_hp

router = APIRouter()


def _json_for_script(value) -> str:
    """json.dumps for embedding inside a <script> block.

    `{{ characters_json|safe }}` with a plain json.dumps let a character named
    `</script><img src=x onerror=...>` close the block and run: JSON's own escaping does not
    touch '<'. Escaping the angle brackets to \\u003c keeps the value valid JSON *and* inert in
    HTML, which is why the template can still use |safe.
    """
    return (json.dumps(value)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))

def _campaign_characters_live(db, entries):
    """Live character rows for a campaign roster, including a portrait flag.

    The stored `dm_campaigns.characters` blob is a snapshot (id/name/class/level/
    race/status) — no HP, no portrait flag — so campaign cards and the campaign
    detail page need a join. `has_portrait` is a boolean, never the blob: the
    portrait itself is fetched from /api/character/{id}/portrait-image.
    """
    out = []
    for entry in entries or []:
        cid = entry.get("id") if isinstance(entry, dict) else entry
        if not cid:
            continue
        row = db.execute(f"""
            SELECT id, name, race, subrace, class_name, subclass, level,
                   hp_current, hp_max, ac,
                   {HAS_PORTRAIT_SQL}
            FROM characters WHERE id=?
        """, (cid,)).fetchone()
        if row:
            d = dict(row)
            d["status"] = entry.get("status", "active") if isinstance(entry, dict) else "active"
            out.append(d)
        elif isinstance(entry, dict) and entry.get("id"):
            # Roster entry with no live row (delete strips rosters, so this means
            # a stale id). Keep it visible but fill every key the template reads:
            # a missing 'level' makes the card's `sum(attribute='level')` raise
            # UndefinedError and 500s the whole DM Tools page.
            d = dict(entry)
            d.setdefault("name", f"Character #{cid}")
            d.setdefault("level", 1)
            d.setdefault("race", "")
            d.setdefault("class_name", "")
            d.setdefault("subclass", None)
            d.setdefault("subrace", None)
            d["hp_current"] = None
            d["hp_max"] = None
            d["ac"] = None
            d["has_portrait"] = False
            out.append(d)
    return out

@router.get("/dm-tools", response_class=HTMLResponse)
def dm_tools(request: Request):
    """DM Tools main page — encounter builder, NPC manager, monster lookup."""
    user = require_user(request)
    db = get_db()

    # Load monsters from SRD cache
    all_monsters = _load_monster_cache()
    monsters_by_env = {}
    for m in all_monsters:
        m_type = m.get("type", "other")
        monsters_by_env.setdefault(m_type, []).append(m)

    # Load DM's NPCs (DB)
    npcs = [dict(r) for r in db.execute(
        f"SELECT {NPC_LIST_COLS} FROM dm_npcs WHERE user_id = ? ORDER BY is_enemy DESC, name",
        (user["id"],)
    ).fetchall()]
    for _n in npcs:
        # Boolean per row; the blob was never selected, so there is nothing to strip.
        _n["has_portrait"] = bool(_n.get("has_portrait"))
    # Enemies stay grouped first (the badges say so), but each group reads in
    # alphabetical order — services.text.alpha_key, because SQLite's ORDER BY name is
    # BINARY: lowercase names filed after every capitalised one, and "Éowyn" after "Zed".
    npcs.sort(key=lambda n: (0 if n.get("is_enemy") else 1, alpha_key(n.get("name"))))

    # Merge manual NPCs from extracted data
    manual_npcs = _load_manual_json("npcs.json")
    for i, mn in enumerate(manual_npcs):
        # Belt and braces: the sentinel is a database row (NPC id -1, created by the pack import and the
        # DM bootstrap so encounter rows always have a non-null NPC to point at), and the database query
        # above filters it. The current npcs.json carries no such entry, but the merge is where a second
        # copy would appear, and a creature-shaped placeholder in a search list is worth being sure
        # about. scripts/generate_portraits.py skips it by name for the same reason.
        if str(mn.get("name") or "").strip() == "__sentinel__":
            continue
        hp = _manual_npc_hp(mn)
        # Kept for the narrative flag below: the raw corpus value, empty when the corpus has none.
        hp_raw = mn.get("hp_current") or mn.get("hit_points", "")
        # Detect narrative NPCs (no combat stat block)
        scores = mn.get("ability_scores", {})
        has_stats = scores and any(v != 0 and v is not None for v in scores.values())
        is_narrative = not has_stats and str(hp_raw).lower() in ("unknown", "?", "0", "")
        npcs.append({
            "id": -(i + 1),  # negative ID = manual NPC
            "user_id": user["id"],
            "name": mn.get("name", "Unknown"),
            "race": mn.get("race", "Unknown"),
            "class_name": mn.get("class_name", ""),
            "subclass": mn.get("subclass", ""),
            "level": mn.get("level", 1),
            "hp_current": hp,
            "hp_max": hp,
            "ac": mn.get("ac", mn.get("armor_class", 10)) if has_stats else 10,
            "is_enemy": 0,
            "is_party_npc": 0,
            "role": mn.get("role", "NPC"),
            "alignment": mn.get("alignment", ""),
            "speed": mn.get("speed", "30 ft."),
            "skills": json.dumps(mn.get("skills", [])),
            "features": json.dumps(mn.get("features", [])),
            "inventory": json.dumps(mn.get("equipment", [])),
            "notes": mn.get("description", ""),
            "source": clean_source_display(mn.get("source"), "Manual"),
            "xp_reward": mn.get("xp_reward", 0),
            "strength": scores.get("strength", 0) if has_stats else 0,
            "dexterity": scores.get("dexterity", 0) if has_stats else 0,
            "constitution": scores.get("constitution", 0) if has_stats else 0,
            "intelligence": scores.get("intelligence", 0) if has_stats else 0,
            "wisdom": scores.get("wisdom", 0) if has_stats else 0,
            "charisma": scores.get("charisma", 0) if has_stats else 0,
            "proficiency_bonus": 2, "hit_dice": "1d8", "hit_dice_used": 0,
            "temp_hp": 0, "portrait_url": "", "faction": "",
            "_manual": True,
            "_narrative": is_narrative,
            "has_portrait": False,
        })

    # Load encounters (own + shared)
    encounters = [dict(r) for r in db.execute("""
        SELECT e.*, u.email as owner_email, 1 as is_owner
        FROM dm_encounters e JOIN users u ON u.id = e.user_id
        WHERE e.user_id = ? ORDER BY e.created_at DESC
    """, (user["id"],)).fetchall()]
    shared_encs = [dict(r) for r in db.execute("""
        SELECT e.*, u.email as owner_email, 0 as is_owner
        FROM dm_encounters e JOIN users u ON u.id = e.user_id
        WHERE e.shared = 1 AND e.user_id != ? ORDER BY e.created_at DESC
    """, (user["id"],)).fetchall()]
    encounters = encounters + shared_encs

    # Load campaigns
    campaigns = [dict(r) for r in db.execute(
        "SELECT * FROM dm_campaigns WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()]

    # Parse JSON fields
    for npc in npcs:
        hydrate_json_columns(npc)
    for c in campaigns:
        for f in ("quests", "locations", "characters"):
            try: c[f] = json.loads(c[f])
            except (json.JSONDecodeError, TypeError): c[f] = []
        # Roster snapshots carry no HP and no portrait flag — the card badges
        # need live values (name / hp / level) and a portrait boolean.
        c["characters"] = _campaign_characters_live(db, c["characters"])

    # Monster types for filtering
    monster_types = sorted(monsters_by_env.keys())
    # Challenge rating options
    cr_ranges = [(0, 0.25), (0.5, 2), (3, 5), (6, 10), (11, 16), (17, 30)]

    db.close()
    # Merge custom traps with manual traps (alpha_key, not .lower(): see services.text —
    # the traps panel is browsed the same way the monsters tab is).
    reference_traps = sorted(list(MANUAL_TRAPS), key=lambda t: alpha_key(t.get("name")))
    all_traps = list(reference_traps)
    try:
        db2 = get_db()
        custom_traps = [dict(r) for r in db2.execute(
            "SELECT * FROM dm_custom_traps WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        ).fetchall()]
        db2.close()
        for ct in custom_traps:
            all_traps.append({
                "name": ct["name"],
                "type": ct["type"],
                "danger": ct["danger"],
                "trigger": ct["trigger"] or "",
                "detection": {
                    "dc": ct["detection_dc"] or 10,
                    "skill": ct["detection_skill"] or "Perception",
                    "detail": ct["detection_detail"] or ""
                },
                "disarm": {
                    "dc": ct["disarm_dc"],
                    "method": ct["disarm_method"] or "",
                    "detail": ct["disarm_detail"] or ""
                },
                "effect": ct["effect"] or "",
                "save_dc": ct["save_dc"],
                "save_ability": ct["save_ability"] or "",
                "damage": ct["damage"] or "",
                "damage_type": ct["damage_type"] or "",
                "area": ct["area"] or "",
                "description": ct["description"] or "",
                "source": "Custom",
                "_custom_id": ct["id"],
                "_custom": True
            })
    except Exception:
        pass

    # Load characters for Quick Add in combat
    db3 = get_db()
    characters = [dict(r) for r in db3.execute(f"""
        SELECT id, name, race, subrace, class_name, level, subclass,
               hp_max, hp_current, ac, strength, dexterity, constitution,
               intelligence, wisdom, charisma, speed, proficiency_bonus,
               {HAS_PORTRAIT_SQL}
        FROM characters WHERE user_id = ? ORDER BY name
    """, (user["id"],)).fetchall()]
    db3.close()

    # Scan DnD-Manuals for the 📚 Manuals tab — grouped by folder.
    # _scan_manual_pdfs() dedupes: the tree reaches one book through the
    # top-level symlink, the DnD-Manuals symlink and a nested Manuals/ copy.
    dn_dir = _core_data.MANUALS_BASE
    pdfs = [str(p) for p in _scan_manual_pdfs()]
    slug_map = _get_source_slug_map()
    # Build reverse slug→path mapping
    slug_to_filename = {}
    for slug, info in slug_map.items():
        fn = Path(info.get("path", "")).name.lower()
        if fn:
            slug_to_filename[fn] = slug
    # Group PDFs by folder
    groups = {}  # folder_name_or_Root → [{name, slug_or_path, filepath, is_pdf}]
    for pdf_path in pdfs:
        p = Path(pdf_path)
        rel = p.relative_to(dn_dir)
        parts = rel.parts
        group = parts[0] if len(parts) > 1 else "Core Manuals"
        # If the first component is the DnD-Manuals symlink, use the next
        # component as the real group (e.g. "CritRoll", "Campaigns")
        if group == "DnD-Manuals" and len(parts) > 2:
            group = parts[1]
        elif group == "DnD-Manuals":
            group = "Core Manuals"
        # Rename groups for consistency with how users refer to them
        group_rename = {
            "5e TLOTR Setting": "Adventures in Middle Earth",
            "CritRoll": "Critical Roll",
        }
        group = group_rename.get(group, group)
        # Per-file group override (PDFs that belong in a different section)
        file_group_override = {
        }
        if p.name in file_group_override:
            group = file_group_override[p.name]
        fname_lower = p.name.lower()
        # Check if this file has a slug
        slug = slug_to_filename.get(fname_lower)
        entry = {
            "name": p.stem,
            "filename": p.name,
            "slug": slug or "",
            "path": str(rel),
            "size": p.stat().st_size if p.exists() else 0,
        }
        groups.setdefault(group, []).append(entry)
    manuals_groups = [{"group": g, "manuals": groups[g]} for g in sorted(groups.keys(), key=lambda x: (0 if x == "Core Manuals" else 1, x))]

    return _render("dm_tools.html", request=request,
                   monsters=all_monsters, monster_types=monster_types,
                   dm_library_version=ensure_dm_library_asset(
                       all_monsters,
                       # The NPC panel is browsed like the monsters tab, so the reference
                       # rows read alphabetically too. Sort a COPY: a manual NPC's id is its
                       # index into the raw npcs.json (id -1 → row 0), resolved by
                       # /api/dm/npc/{id} and friends, so the source list's order is fixed.
                       sorted((n for n in npcs if n.get("_manual")),
                              key=lambda n: alpha_key(n.get("name"))),
                       reference_traps,
                       # Same reason as monsters/NPCs: the panel is browsed, and the
                       # manual/AI-added spells used to arrive as an unsorted tail.
                       sorted(SRD_SPELLS, key=lambda s: alpha_key(s.get("name"))),
                       # 18 KB + 38 KB of global constant data the page used to inline.
                       named_item_types=_get_named_item_types(),
                       summon_templates=SUMMON_TEMPLATES,
                       # ~87 manual rows: a global tree, so it rides in the asset too.
                       manual_groups=manuals_groups),
                   dm_tools_js_version=static_asset_version("dm_tools.js"),
                   source_slug_map_version=ensure_source_slug_map_asset(_get_source_slug_map()),
                   cr_ranges=cr_ranges,
                   npcs=[n for n in npcs if not n.get("_manual")],
                   encounters=encounters, campaigns=campaigns,
                   # Only the DM's OWN traps are server-rendered now; the reference
                   # library rides in dm-library.js and is drawn by renderTrapCards().
                   traps=[t for t in all_traps if t.get("_custom")],
                   trap_total=len(all_traps),
                   # The DM's own character roster is user data, so it stays inline here —
                   # only global reference tables may move into a cached asset.
                   characters_json=_json_for_script(characters),
                   manuals_groups=manuals_groups,
                   # The spell library rides in dm-library.js too (renderSpellCards());
                   # only the count is server-rendered, so spell_total carries it.
                   spells=[], spell_total=len(SRD_SPELLS),
                   spell_schools=sorted({s.get("school", {}).get("name", "").title() for s in SRD_SPELLS if s.get("school")} - {""}),
                   spell_classes=sorted({c.get("name", "").title() for s in SRD_SPELLS for c in (s.get("classes") or []) if isinstance(c, dict) and c.get("name")}),
                   item_picker_types=sorted({i.get("type", "") for i in SRD_MAGIC_ITEMS} | {i.get("type", "") for i in ITEM_INDEX.values()} - {""}),
                   item_picker_rarities=sorted({i.get("rarity", "").title() for i in ITEM_INDEX.values() if i.get("rarity")} - {""}))

# ── Monster cards as a cacheable asset ───────────────────────────────────────
_DM_LIBRARY_VERSION: str | None = None

def _monster_card_payload(m: dict) -> dict:
    """Just the fields the Monsters-tab card shows (the modal refetches detail)."""
    ac = m.get("armor_class") or []
    ac_val = None
    if ac:
        first = ac[0]
        ac_val = first.get("value") if isinstance(first, dict) else first
    return {
        "i": m.get("index", ""),
        "n": m.get("name", "") or "",
        "t": (m.get("type", "") or "").lower(),
        "s": m.get("size", "") or "",
        "a": m.get("alignment", "") or "",
        "cr": m.get("challenge_rating", 0),
        "ac": ac_val if ac_val is not None else "?",
        "hp": m.get("hit_points", 0),
        "xp": m.get("xp", 0),
        "src": clean_source_display(m.get("source")),
    }

def ensure_dm_library_asset(monsters, manual_npcs, traps=(), spells=(),
                           named_item_types=None, summon_templates=None,
                           manual_groups=()) -> str:
    """Write static/dm-library.js — reference data the DM tools panels render.

    The panels used to server-render every row: ~1,900 monster cards, ~400
    manual NPC rows (one of which carries a 433 KB description) and the spell
    and trap libraries — megabytes of HTML on every load. Only *global*
    reference data belongs here; anything user-owned (DB NPCs, custom traps,
    encounters, campaigns) stays in the HTML so it can never leak between DMs.
    """
    global _DM_LIBRARY_VERSION
    if _DM_LIBRARY_VERSION is not None:
        return _DM_LIBRARY_VERSION
    try:
        monsters = [_monster_card_payload(m) for m in monsters]
        npcs = [_manual_npc_payload(n) for n in manual_npcs]
        traps = [_trap_card_payload(t) for t in traps]
        spells = [_spell_card_payload(s) for s in spells]
        manuals = _manual_group_payload(manual_groups)
        # A real JSON object (not a JS literal) so the payload is parseable and
        # diffable; the aliases keep dm_tools.js readable.
        payload = json.dumps({"monsters": monsters, "manualNpcs": npcs, "traps": traps,
                              "spells": spells, "manuals": manuals},
                             separators=(",", ":"), default=str)
        # NAMED_ITEM_TYPES (18 KB) and SUMMON_TEMPLATES (38 KB) were inline <script>
        # data on the page: re-sent on every load, uncacheable. /dm-tools is their only
        # consumer, so they ride here.
        consts = (
            "window.NAMED_ITEM_TYPES = "
            + json.dumps(named_item_types or {}, separators=(",", ":"), default=str, sort_keys=True)
            + ";\nwindow.SUMMON_TEMPLATES = "
            + json.dumps(summon_templates or [], separators=(",", ":"), default=str)
            + ";\n")
        body = (
            "/* Generated by routes.dm.ensure_dm_library_asset(); rendered by "
            "/static/dm_tools.js. Global reference data only. */\n"
            "window.DM_LIBRARY = " + payload + ";\n"
            "window.DM_MONSTERS = window.DM_LIBRARY.monsters;\n"
            "window.DM_MANUAL_NPCS = window.DM_LIBRARY.manualNpcs;\n"
            "window.DM_TRAPS = window.DM_LIBRARY.traps;\n"
            "window.DM_SPELLS = window.DM_LIBRARY.spells;\n"
            "window.DM_MANUALS = window.DM_LIBRARY.manuals;\n" + consts)
        target = STATIC / "dm-library.js"
        if not target.exists() or target.read_text() != body:
            target.write_text(body)
        _DM_LIBRARY_VERSION = static_asset_version("dm-library.js")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[dm-library] generation failed: {exc}")
        _DM_LIBRARY_VERSION = "0"
    return _DM_LIBRARY_VERSION

def _manual_npc_payload(n: dict) -> dict:
    """Row fields for a manual (read-only, extracted) NPC — see the template."""
    return {
        "id": n.get("id"),
        "n": n.get("name", "Unknown"),
        "r": n.get("race", ""),
        "c": n.get("class_name", ""),
        "sc": n.get("subclass", ""),
        "lvl": n.get("level", 1),
        "hp": n.get("hp_current", 0),
        "hpmax": n.get("hp_max", 0),
        "ac": n.get("ac", 10),
        "role": n.get("role", ""),
        "al": n.get("alignment", ""),
        "src": clean_source_display(n.get("source")),
        "notes": n.get("notes", "") or "",
        "narr": 1 if n.get("_narrative") else 0,
    }

def _trap_card_payload(t: dict) -> dict:
    """Row fields for a reference trap card — the template's markup, flattened.

    Keys are short because this rides in a cached asset every /dm-tools load
    parses; renderTrapCards() rebuilds the same markup the template emitted.
    """
    det = t.get("detection") or {}
    dis = t.get("disarm") or {}
    return {
        "n": t.get("name", "Unknown"),
        "t": t.get("type", ""),
        "d": t.get("danger", ""),
        "src": clean_source_display(t.get("source")),
        "sdc": t.get("save_dc") or "",
        "sab": t.get("save_ability") or "",
        "dmg": t.get("damage") or "",
        "dt": t.get("damage_type") or "",
        "area": t.get("area") or "",
        "trig": t.get("trigger") or "",
        "detdc": det.get("dc", ""),
        "detsk": det.get("skill", ""),
        "detd": det.get("detail", ""),
        "disdc": dis.get("dc") or "",
        "dism": dis.get("method") or "",
        "disd": dis.get("detail") or "",
        "eff": t.get("effect") or "",
    }

def _manual_group_payload(groups) -> list:
    """Manual library tree for the DM tools panel: [{'g': group, 'items': [row…]}].

    Global (the server's manual directory), ~87 rows whose long names and repeated inline
    styles cost 70 KB of HTML on every load.
    """
    out = []
    for group in groups or []:
        rows = []
        for m in group.get("manuals") or []:
            row = {"n": m.get("name", "")}
            if m.get("slug"):
                row["s"] = m["slug"]
            elif m.get("path"):
                row["p"] = m["path"]
            if (m.get("size") or 0) > 0:
                row["mb"] = round(m["size"] / 1048576, 1)
            rows.append(row)
        out.append({"g": group.get("group", ""), "items": rows})
    return out

def _spell_card_payload(s: dict) -> dict:
    """Row fields for a reference spell card (see the template's markup, now in JS).

    Short keys: ~700 rows ride in a cached asset that every /dm-tools load parses.
    """
    school = s.get("school")
    if isinstance(school, dict):
        school_name = school.get("name") or ""
    else:
        school_name = str(school or "")
    classes = []
    for c in s.get("classes") or []:
        if isinstance(c, dict):
            classes.append(c.get("name") or "")
        elif c:
            classes.append(str(c))
    return {
        "i": s.get("index", ""),
        "n": s.get("name", "Unknown"),
        "lv": s.get("level", 0),
        # data-school is matched against the filter's lowercased option value
        "sc": school_name.lower(),
        "scd": school_name if isinstance(school, dict) else school_name.title(),
        "src": clean_source_display(s.get("source")),
        "rit": 1 if s.get("ritual") else 0,
        "con": 1 if s.get("concentration") else 0,
        "ct": s.get("casting_time") or "",
        "rng": s.get("range") or "",
        "dur": s.get("duration") or "",
        "cls": ",".join(c for c in classes if c).lower(),
    }

@router.get("/campaign/{camp_id}", response_class=HTMLResponse)
def campaign_detail(camp_id: int, request: Request):
    """Server-rendered campaign detail page."""
    user = require_user(request)
    db = get_db()

    camp = db.execute(
        "SELECT * FROM dm_campaigns WHERE id=? AND user_id=?",
        (camp_id, user["id"])
    ).fetchone()
    if not camp:
        db.close()
        raise HTTPException(status_code=404, detail="Campaign not found")
    camp = dict(camp)

    for f in ("quests", "locations", "characters", "npcs"):
        try: camp[f] = json.loads(camp.get(f) or "[]")
        except (json.JSONDecodeError, TypeError): camp[f] = []

    enriched = []
    for entry in camp.get("characters", []):
        cid = entry.get("id") if isinstance(entry, dict) else entry
        row = db.execute(f"""
            SELECT id, name, race, subrace, class_name, subclass, level,
                   hp_current, hp_max, temp_hp, ac, strength, dexterity, constitution,
                   intelligence, wisdom, charisma, proficiency_bonus, speed,
                   {HAS_PORTRAIT_SQL}
            FROM characters WHERE id=?
        """, (cid,)).fetchone()
        if row:
            enriched.append(dict(row))
    chars = enriched

    npcs = []
    for entry in camp.get("npcs", []):
        nid = entry.get("id") if isinstance(entry, dict) else entry
        # Explicit columns + a portrait flag: SELECT * would drag the base64
        # portrait blob into memory for every NPC on the page.
        row = db.execute(f"""
            SELECT id, name, race, class_name, subclass, level, hp_current, hp_max,
                   ac, is_enemy, role, alignment, faction, xp_reward, notes, source,
                   {HAS_PORTRAIT_SQL}
            FROM dm_npcs WHERE id=?
        """, (nid,)).fetchone()
        if row:
            npcs.append(dict(row))

    all_chars = [dict(r) for r in db.execute(
        "SELECT id, name, race, class_name, level FROM characters WHERE user_id = ? ORDER BY name",
        (user["id"],)
    ).fetchall()]
    linked_char_ids = {c.get("id") for c in chars}

    live_size = len(chars) or camp.get("party_size", 4)
    live_level = (
        round(sum(c.get("level", 1) for c in chars) / len(chars))
        if chars else camp.get("party_level", 1)
    )

    db.close()
    return _render("campaign_detail.html", request=request,
                   camp=camp, chars=chars, npcs=npcs,
                   quests=camp.get("quests", []),
                   locations=camp.get("locations", []),
                   all_chars=all_chars, linked_char_ids=linked_char_ids,
                   live_size=live_size, live_level=live_level)
