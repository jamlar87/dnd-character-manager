"""Internal entity search — the nav search bar's "Internal data" section.

Indexes everything the app already knows: SRD core data (``main.ITEM_INDEX``,
``SRD_SPELLS``, ``SRD_FEATURES``, ``data.RACES``, ``data.FEATS``,
``RACIAL_TRAIT_DESCS``, ``FEATURE_DESCRIPTIONS``) plus every ingested manual JSON
file in ``data/manual_data/`` (monsters, npcs, traps, magic_items, equipment,
spells, races, feats, backgrounds, subclasses).

Why this exists: ``routes.characters.helpers._search_json_data`` only greps 8 of
those files and returns manual-shaped text snippets (``page: 0``), so internal
hits rendered as dead rows with no type and no link. This module returns typed,
linkable rows instead: ``{kind, name, subtitle, snippet, source, slug, page, score}``.

Import rule (same as ``services/items.py``): ``main`` is imported lazily INSIDE
``_build_index()`` — module-scope ``from main import ...`` here would re-enter a
partially initialised ``main`` when main imports this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KIND_META: dict[str, dict] = {
    "item": {"icon": "📦", "label": "Item"},
    "creature": {"icon": "🐉", "label": "Creature"},
    "npc": {"icon": "👤", "label": "NPC"},
    "feat": {"icon": "⭐", "label": "Feat"},
    "race": {"icon": "🧬", "label": "Race"},
    "trait": {"icon": "🧬", "label": "Trait"},
    "spell": {"icon": "✨", "label": "Spell"},
    "background": {"icon": "🎭", "label": "Background"},
    "subclass": {"icon": "🧩", "label": "Subclass"},
    "trap": {"icon": "⚠️", "label": "Trap"},
    "feature": {"icon": "🔧", "label": "Feature"},
}

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_MANUAL_DIR = _DATA_DIR / "manual_data"

# Tie-break order for rows with an EQUAL score. Names collide across groups
# ("Dwarf" is both a playable race and a bestiary stat block), and the result
# order must not depend on index insertion order. Character-building reference
# entities come first — a player searching "dwarf" wants the race; the stat
# block is still listed, in the Creature group.
KIND_PRIORITY = {
    "race": 0, "trait": 1, "spell": 2, "feat": 3, "item": 4,
    "background": 5, "subclass": 6, "feature": 7,
    "creature": 8, "npc": 9, "trap": 10,
}

_index: list[dict] | None = None
_seen: set[tuple] = set()


# ── helpers ────────────────────────────────────────────────────────────────

def _read_json(name: str):
    path = _MANUAL_DIR / name
    if not path.exists():
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _flatten(value) -> str:
    """Flatten any desc/action/feature structure to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    return str(value)


def _entity_text(*parts) -> str:
    """Matching haystack: flattened, curly-quote normalised, whitespace-collapsed, LOWERED.

    The lowercase matters — a mixed-case name like 'Longsword' would otherwise
    never match a lowercased query word.
    """
    text = " ".join(_flatten(p) for p in parts if p)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def _slug_page(source: str) -> tuple[str, int | None]:
    """'(Lost Mine of Phandelver, p.52)' / 'PHB 2014 p.150' -> (slug, page)."""
    if not source:
        return "", None
    from routes.characters.helpers import slug_for_source  # call-time import

    page = None
    m = re.search(r"p\.?\s*(\d+)", source)
    if m:
        page = int(m.group(1))
    return slug_for_source(source) or "", page


def _add(rows: list[dict], kind: str, name: str, *, subtitle: str = "",
         snippet: str = "", source: str = "", text: str = "", raw=None,
         dedupe_key: tuple | None = None) -> None:
    name = (name or "").strip()
    if not name:
        return
    key = dedupe_key if dedupe_key is not None else (kind, name.lower())
    if key in _seen:
        return
    _seen.add(key)
    slug, page = _slug_page(source)
    rows.append({
        "kind": kind,
        "name": name,
        "subtitle": subtitle.strip(),
        "snippet": snippet.strip()[:300],
        "source": source or "",
        "slug": slug,
        "page": page,
        "text": _entity_text(name, subtitle, snippet, text),
        "name_lower": name.lower(),
        "raw": raw,
    })


# ── index ──────────────────────────────────────────────────────────────────

def _build_index() -> list[dict]:
    import main as app
    import data as app_data

    rows: list[dict] = []

    # ── Items: SRD+manual item index first (it owns page numbers) ──
    for key, item in (getattr(app, "ITEM_INDEX", {}) or {}).items():
        if not isinstance(item, dict):
            continue
        rarity = (item.get("rarity") or "").strip()
        subtitle = item.get("type") or "Item"
        if rarity and rarity.lower() != "unknown":
            subtitle = f"{subtitle} · {rarity}"
        _add(rows, "item", item.get("name") or key, subtitle=subtitle,
             snippet=item.get("description", ""), source=item.get("source", ""),
             text=item.get("cost", ""), raw={"row": item, "origin": "index"})

    for item in _read_json("magic_items.json"):
        if not isinstance(item, dict):
            continue
        subtitle = f"Magic Item · {item.get('type') or 'Wondrous'}"
        if item.get("requires_attunement"):
            subtitle += " · attunement"
        _add(rows, "item", item.get("name", ""), subtitle=subtitle,
             snippet=item.get("description", ""), source=item.get("source", ""),
             text=item.get("rarity", ""), raw={"row": item, "origin": "magic_item"})

    for item in _read_json("equipment.json"):
        if not isinstance(item, dict):
            continue
        sub = item.get("subtype") or item.get("type") or "Equipment"
        _add(rows, "item", item.get("name", ""), subtitle=sub,
             snippet=item.get("description", ""), source=item.get("source", ""),
             text=f"{item.get('damage', '')} {item.get('cost', '')}",
             raw={"row": item, "origin": "equipment"})

    # ── Creatures ──
    # Source of truth is the DM bestiary's own list (SRD + every ingested manual,
    # ~1943 rows) — monsters.json alone lacks the SRD blocks (Goblin, Troll...).
    try:
        from routes.characters.helpers import _load_monster_cache
        monsters = _load_monster_cache()
    except Exception:
        monsters = _read_json("monsters.json")
    for mon in monsters:
        if not isinstance(mon, dict):
            continue
        cr = mon.get("challenge_rating")
        size = mon.get("size") or ""
        mtype = mon.get("type") or ""
        subtitle = " · ".join(x for x in (f"CR {cr}" if cr not in (None, "") else "",
                                          f"{size} {mtype}".strip()) if x)
        _add(rows, "creature", mon.get("name", ""), subtitle=subtitle,
             snippet=(mon.get("description", "")
                      or _flatten(mon.get("special_abilities"))[:200]
                      or _flatten(mon.get("actions"))[:200]),
             source=mon.get("source", ""),
             text=" ".join([_flatten(mon.get("special_abilities")),
                            _flatten(mon.get("features")),
                            _flatten(mon.get("actions")),
                            _flatten(mon.get("traits")),
                            str(mon.get("alignment", "")),
                            str(mon.get("languages", "")),
                            _flatten(mon.get("senses")),
                            _flatten(mon.get("damage_immunities")),
                            _flatten(mon.get("damage_resistances"))]),
             raw={"row": mon, "origin": "monster"})

    # ── NPCs ──
    for npc in _read_json("npcs.json"):
        if not isinstance(npc, dict):
            continue
        bits = [npc.get("race"), npc.get("class_name") or npc.get("role")]
        level = npc.get("level") or 0
        if level:
            bits.append(f"level {level}")
        _add(rows, "npc", npc.get("name", ""),
             subtitle=" · ".join(str(b) for b in bits if b),
             snippet=npc.get("description", ""), source=npc.get("source", ""),
             text=f"{_flatten(npc.get('features'))} {_flatten(npc.get('actions'))}",
             raw={"row": npc, "origin": "npc"})

    # ── Traps ──
    for trap in _read_json("traps.json"):
        if not isinstance(trap, dict):
            continue
        bits = [trap.get("type"), trap.get("danger")]
        if trap.get("save_dc"):
            bits.append(f"DC {trap['save_dc']} {trap.get('save_ability', '')}".strip())
        _add(rows, "trap", trap.get("name", ""),
             subtitle=" · ".join(str(b) for b in bits if b),
             snippet=trap.get("effect") or trap.get("description") or trap.get("trigger", ""),
             source=trap.get("source", ""),
             text=" ".join([str(trap.get("trigger", "")), str(trap.get("detection", "")),
                            str(trap.get("damage", "")), str(trap.get("damage_type", "")),
                            str(trap.get("area", ""))]),
             raw={"row": trap, "origin": "trap"})

    # ── Feats: manual JSON then data.py (covers non-SRD like Gunner) ──
    for feat in _read_json("feats.json"):
        if not isinstance(feat, dict):
            continue
        _add(rows, "feat", feat.get("name", ""),
             subtitle=feat.get("prerequisite") or "Feat",
             snippet=feat.get("description", ""), source=feat.get("source", ""),
             text=feat.get("benefits", ""), raw={"row": feat, "origin": "feat_json"})

    for key, feat in (getattr(app_data, "FEATS", {}) or {}).items():
        if not isinstance(feat, dict):
            continue
        _add(rows, "feat", feat.get("name") or key.title(), subtitle="Feat",
             snippet=feat.get("desc", "") or feat.get("description", ""),
             source=feat.get("source", ""), raw={"row": feat, "origin": "data_feats"})

    # ── Races + their traits ──
    for race in _read_json("races.json"):
        if not isinstance(race, dict):
            continue
        rname = race.get("name", "")
        _add(rows, "race", rname,
             subtitle=" · ".join(x for x in (race.get("size"), 
                                             f"{race.get('speed')} ft." if race.get("speed") else "") if x),
             snippet=race.get("description", ""), source=race.get("source", ""),
             text=_flatten(race.get("traits")), raw={"row": race, "origin": "race_json"})
        for trait in (race.get("traits") or []):
            if not isinstance(trait, dict):
                continue
            _add(rows, "trait", trait.get("name", ""), subtitle=f"Trait · {rname}",
                 snippet=trait.get("description", ""), source=race.get("source", ""),
                 dedupe_key=("trait", (trait.get("name") or "").lower(), rname.lower()),
                 raw={"row": trait, "origin": "race_trait", "race": rname,
                      "race_source": race.get("source", "")})

    trait_descs = getattr(app, "RACIAL_TRAIT_DESCS", {}) or {}
    for rname, race in (getattr(app, "RACES", {}) or {}).items():
        if not isinstance(race, dict):
            continue
        _add(rows, "race", rname, subtitle="Race",
             snippet=race.get("desc", ""), source=race.get("source", ""),
             text=_flatten(race.get("asi")), raw={"row": race, "origin": "data_races"})
        for trait in (race.get("traits") or []):
            tname = trait if isinstance(trait, str) else (trait or {}).get("name", "")
            if not tname:
                continue
            _add(rows, "trait", tname, subtitle=f"Trait · {rname}",
                 snippet=trait_descs.get(tname, ""), source=race.get("source", ""),
                 dedupe_key=("trait", tname.lower(), rname.lower()),
                 raw={"row": {"name": tname, "description": trait_descs.get(tname, "")},
                      "origin": "data_race_trait", "race": rname,
                      "race_source": race.get("source", "")})

    # ── Spells ──
    for spell in _read_json("spells.json"):
        if not isinstance(spell, dict):
            continue
        _add(rows, "spell", spell.get("name", ""),
             subtitle=_spell_subtitle(spell.get("level"), None, spell.get("classes")),
             snippet=spell.get("description", ""), source=spell.get("source", ""),
             text=f"{spell.get('casting_time', '')} {spell.get('higher_levels', '')}",
             raw={"row": spell, "origin": "spell_json"})

    for spell in (getattr(app, "SRD_SPELLS", []) or []):
        if not isinstance(spell, dict):
            continue
        school = (spell.get("school") or {})
        school = school.get("name") if isinstance(school, dict) else school
        classes = [c.get("name") for c in (spell.get("classes") or []) if isinstance(c, dict)]
        _add(rows, "spell", spell.get("name", ""),
             subtitle=_spell_subtitle(spell.get("level"), school, classes),
             snippet=_flatten(spell.get("desc")), source=spell.get("source", ""),
             text=f"{spell.get('casting_time', '')} {_flatten(spell.get('higher_level'))}",
             raw={"row": spell, "origin": "srd_spell"})

    # ── Backgrounds ──
    for bg in _read_json("backgrounds.json"):
        if not isinstance(bg, dict):
            continue
        _add(rows, "background", bg.get("name", ""), subtitle="Background",
             snippet=bg.get("description", ""), source=bg.get("source", ""),
             text=_flatten(bg.get("skill_proficiencies")), raw={"row": bg, "origin": "bg_json"})

    # ── Subclasses ──
    for sc in _read_json("subclasses.json"):
        if not isinstance(sc, dict):
            continue
        _add(rows, "subclass", sc.get("name", ""),
             subtitle=f"{sc.get('class', '')} subclass".strip(),
             snippet=sc.get("description", ""), source=sc.get("source", ""),
             text=_flatten(sc.get("features")), raw={"row": sc, "origin": "subclass_json"})

    # ── Class features: SRD feature list + the big description map ──
    for feat in (getattr(app, "SRD_FEATURES", []) or []):
        if not isinstance(feat, dict):
            continue
        cls = feat.get("class") or {}
        cls_name = cls.get("name") if isinstance(cls, dict) else cls
        level = feat.get("level")
        _add(rows, "feature", feat.get("name", ""),
             subtitle=" · ".join(x for x in (cls_name, f"level {level}" if level else "") if x),
             snippet=_flatten(feat.get("desc")), source=feat.get("source", ""),
             raw={"row": feat, "origin": "srd_feature"})

    for fname, desc in (getattr(app_data, "FEATURE_DESCRIPTIONS", {}) or {}).items():
        _add(rows, "feature", fname.title() if fname.islower() else fname,
             subtitle="Class feature", snippet=desc if isinstance(desc, str) else _flatten(desc),
             raw={"row": {"name": fname, "description": desc}, "origin": "feature_desc"})

    return rows


def _spell_subtitle(level, school, classes) -> str:
    lvl = level if isinstance(level, int) else None
    if lvl == 0:
        head = "Cantrip"
    elif lvl:
        head = f"{lvl}{'st' if lvl == 1 else 'nd' if lvl == 2 else 'rd' if lvl == 3 else 'th'}-level"
    else:
        head = "Spell"
    bits = [head, school or ""]
    names = [c for c in (classes or []) if c]
    if names:
        bits.append(", ".join(str(n) for n in names[:3]))
    return " · ".join(b for b in bits if b)


def _get_index() -> list[dict]:
    global _index
    if _index is None:
        _index = _build_index()
    return _index


def reset_index() -> None:
    """Drop the cached index (tests / after manual ingestion)."""
    global _index
    _index = None
    _seen.clear()


def index_stats() -> dict:
    rows = _get_index()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"total": len(rows), "counts": counts}


# ── search ─────────────────────────────────────────────────────────────────

def _score(row: dict, query: str, words: list[str]) -> float:
    name = row["name_lower"]
    text = row["text"]
    score = 0.0
    if name == query:
        score += 10.0
    elif name.startswith(query):
        score += 5.0
    elif query in name:
        score += 3.0
    if query and query in text:
        score += 5.0
    for w in words:
        if w in name:
            score += 3.0
        elif w in text:
            score += 1.0
    return score


def search_entities(query: str, *, limit: int = 40, per_kind: int = 3,
                    sources: set[str] | None = None) -> list[dict]:
    """Ranked internal-entity hits for `query`.

    `sources` is a set of book slugs (empty/None = every book). Entities that
    carry a source must match the filter; entities without a source are dropped
    while a filter is active (same rule as helpers._search_data_py_feats).
    """
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []
    words = [w for w in query.split() if w]
    if not words:
        return []

    hits: list[tuple[float, dict]] = []
    for row in _get_index():
        if sources:
            if not row["slug"] or row["slug"] not in sources:
                continue
        text = row["text"]
        if not all(w in text for w in words):
            continue
        hits.append((_score(row, query, words), row))

    hits.sort(key=lambda pair: (-pair[0], KIND_PRIORITY.get(pair[1]["kind"], 99)))

    out: list[dict] = []
    used: dict[str, int] = {}
    for score, row in hits:
        kind = row["kind"]
        if used.get(kind, 0) >= per_kind:
            continue
        used[kind] = used.get(kind, 0) + 1
        out.append({
            "kind": kind,
            "name": row["name"],
            "subtitle": row["subtitle"],
            "snippet": row["snippet"],
            "source": row["source"],
            "slug": row["slug"],
            "page": row["page"],
            "score": round(score, 2),
        })
        if len(out) >= limit:
            break
    return out


# ── detail (for the result modal) ───────────────────────────────────────────

def _stat(label: str, value) -> dict | None:
    if value in (None, "", 0, [], {}):
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    return {"label": label, "value": str(value)}


def _detail_stats(kind: str, row: dict) -> list[dict]:
    raw = row["raw"] or {}
    data = raw.get("row") or {}
    origin = raw.get("origin", "")
    stats: list[dict | None] = []

    if kind == "item":
        stats = [_stat("Type", data.get("type")),
                 _stat("Rarity", data.get("rarity")),
                 _stat("Attunement", "Required" if data.get("requires_attunement") else None),
                 _stat("Cost", data.get("cost")),
                 _stat("Weight", f"{data['weight']} lb." if data.get("weight") else None),
                 _stat("Damage", data.get("damage"))]
    elif kind == "creature":
        ac = data.get("armor_class")
        if isinstance(ac, list):
            ac = ", ".join(str(a.get("value", a)) if isinstance(a, dict) else str(a) for a in ac)
        stats = [_stat("AC", ac), _stat("HP", data.get("hit_points")),
                 _stat("Speed", _flatten(data.get("speed")).replace("  ", " ")),
                 _stat("CR", data.get("challenge_rating")),
                 _stat("Size", data.get("size")), _stat("Type", data.get("type")),
                 _stat("Alignment", data.get("alignment"))]
    elif kind == "npc":
        stats = [_stat("Race", data.get("race")), _stat("Class", data.get("class_name")),
                 _stat("Level", data.get("level")), _stat("Role", data.get("role")),
                 _stat("AC", data.get("armor_class") or None), _stat("HP", data.get("hit_points"))]
    elif kind == "feat":
        stats = [_stat("Prerequisite", data.get("prerequisite")),
                 _stat("Benefits", data.get("benefits"))]
    elif kind == "race":
        stats = [_stat("Size", data.get("size")), _stat("Speed", data.get("speed")),
                 _stat("Darkvision", data.get("darkvision")),
                 _stat("ASI", _flatten(data.get("asi"))), _stat("Languages", _flatten(data.get("languages")))]
    elif kind == "trait":
        stats = [_stat("From", raw.get("race")), _stat("Uses", data.get("uses"))]
    elif kind == "spell":
        school = data.get("school")
        school = school.get("name") if isinstance(school, dict) else school
        classes = [c.get("name") for c in (data.get("classes") or []) if isinstance(c, dict)]
        stats = [_stat("Level", data.get("level")), _stat("School", school),
                 _stat("Casting Time", data.get("casting_time")), _stat("Range", data.get("range")),
                 _stat("Duration", data.get("duration")), _stat("Components", _flatten(data.get("components"))),
                 _stat("Concentration", "Yes" if data.get("concentration") else None),
                 _stat("Ritual", "Yes" if data.get("ritual") else None),
                 _stat("Classes", classes or data.get("classes"))]
    elif kind == "background":
        stats = [_stat("Skills", data.get("skill_proficiencies")),
                 _stat("Tools", data.get("tool_proficiencies")),
                 _stat("Languages", data.get("languages")),
                 _stat("Feature", (data.get("feature") or {}).get("name")
                       if isinstance(data.get("feature"), dict) else data.get("feature"))]
    elif kind == "subclass":
        stats = [_stat("Class", data.get("class"))]
    elif kind == "trap":
        stats = [_stat("Type", data.get("type")), _stat("Danger", data.get("danger")),
                 _stat("Save", f"DC {data['save_dc']} {data.get('save_ability', '')}".strip()
                       if data.get("save_dc") else None),
                 _stat("Damage", f"{data.get('damage', '')} {data.get('damage_type', '')}".strip()),
                 _stat("Area", data.get("area"))]
    elif kind == "feature":
        cls = data.get("class") or {}
        stats = [_stat("Class", cls.get("name") if isinstance(cls, dict) else cls),
                 _stat("Level", data.get("level"))]

    return [s for s in stats if s]


def _detail_body(kind: str, row: dict) -> str:
    raw = row["raw"] or {}
    data = raw.get("row") or {}
    origin = raw.get("origin", "")
    parts: list[str] = []

    for field in ("description", "desc", "effect", "trigger", "detection", "disarm"):
        val = data.get(field)
        text = _flatten(val)
        if text:
            parts.append(text)
            break

    if kind in ("creature", "npc"):
        for field in ("special_abilities", "features", "actions", "spellcasting"):
            text = _flatten(data.get(field))
            if text:
                parts.append(f"{field.replace('_', ' ').title()}:\n{text}")
    elif kind == "race":
        traits = data.get("traits") or []
        names = [t if isinstance(t, str) else (t or {}).get("name", "") for t in traits]
        names = [n for n in names if n]
        if names:
            parts.append("Traits: " + ", ".join(names))
    elif kind == "subclass":
        text = _flatten(data.get("features"))
        if text:
            parts.append(text)

    body = "\n\n".join(p for p in parts if p).strip()
    return body or row["subtitle"] or row["name"]


def entity_detail(kind: str, name: str) -> dict | None:
    """Structured detail payload for one entity, or None if unknown."""
    if kind not in KIND_META or not name:
        return None
    wanted = name.strip().lower()
    for row in _get_index():
        if row["kind"] != kind or row["name_lower"] != wanted:
            continue
        return {
            "kind": kind,
            "kind_label": KIND_META[kind]["label"],
            "icon": KIND_META[kind]["icon"],
            "name": row["name"],
            "subtitle": row["subtitle"],
            "source": row["source"],
            "slug": row["slug"],
            "page": row["page"],
            "stats": _detail_stats(kind, row),
            "body": _detail_body(kind, row),
        }
    return None
