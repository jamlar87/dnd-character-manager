#!/usr/bin/env python3
"""Build a class/subclass feature → source mapping.

Output: data/page_maps/feature_page_map.json
        {"frenzy": "PHB 2014 p.49", "ooze mind": "(Kobold Quarterly 20)", ...}
        Keys are lowercase (the convention `_item_page_map` already uses).

The entity index lists ~2,270 class/subclass features from
`data.FEATURE_DESCRIPTIONS`; unlike races, spells, traits and items, features had
no page map, so every one of those rows rendered with no source badge even though
the provenance was documented. Two evidence chains, nothing guessed:

1. routes/characters/all.py's registries label their sections with a book and page
   ("# ── Barbarian: Path of the Berserker (PHB p.49) ──", "# Paladin oaths —
   PHB p.86-88"). Attribution is only taken when the LIVE text for that key is
   still the registry's text: the merge into FEATURE_DESCRIPTIONS is
   fill-if-missing, so a key can legitimately carry another source's description
   (Pitfall 83/86) and then the section's page says nothing about the text shown.

2. A feature that exactly ONE subclass claims inherits that subclass record's own
   source string — the same value the subclass row already displays, e.g.
   "(Tasha's Cauldron of Everything, p.67)". A name two subclasses share is left
   out: one page cannot be right for both (Pitfall 57).

Anything still unresolved is left out rather than pointed at a plausible page.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path("/home/james/dnd-character-manager")
sys.path.insert(0, str(REPO))

OUTPUT_PATH = REPO / "data" / "page_maps" / "feature_page_map.json"
ALL_PY = REPO / "routes" / "characters" / "all.py"
SUBCLASSES = REPO / "data" / "manual_data" / "subclasses.json"

# A section label that names a book and a page, e.g.
#   "── Barbarian: Path of the Berserker (PHB p.49) ──"   -> ("PHB 2014", "49")
#   "# Cleric domains — PHB p.59-62"                      -> ("PHB 2014", "59")
PAGE_LABEL = re.compile(r"([A-Z][A-Za-z'’0-9]*(?:\s+[A-Z0-9][A-Za-z'’0-9]*){0,3})\s*p\.?\s*(\d+)")
# A bare section header with no page, e.g. "# ── Multiclass Support ──": the page
# from an earlier section must not leak into the keys that follow.
SECTION_HEADER = re.compile(r"^\s*#\s*[─—\-=]{2,}")
# A dict key inside a registry, e.g.  "frenzy":
KEY_LINE = re.compile(r'^\s{4,}"([^"]+)"\s*:')

# The app's own canonical short form (see helpers.slug_for_source: "PHB 2014 p.170" -> "PHB")
BOOK_ALIASES = {"PHB": "PHB 2014", "DMG": "DMG 2014", "MM": "Monster Manual"}


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def registry_sections():
    """(book, page, subclass) per key for each section of the two registries.

    A key with no section label of its own must not inherit the previous
    section's page — that is how "ooze mind" (Kobold Quarterly) would have been
    tagged PHB p.119. The section label names the subclass, so attribution is
    only kept when the key really is one of that subclass's features.
    """
    text = ALL_PY.read_text()
    section_pages = {}          # norm(key) -> (book, page, subclass or "")
    current = None
    for line in text.splitlines():
        if not line.lstrip().startswith("#"):
            m = KEY_LINE.match(line)
            if m and current:
                section_pages.setdefault(norm(m.group(1)), current)
            continue
        comment = line.lstrip().lstrip("#").strip()
        m = PAGE_LABEL.search(comment)
        if m:
            book = BOOK_ALIASES.get(m.group(1).strip(), m.group(1).strip())
            label = comment[: m.start()].strip(" —─-()[]")
            # "Barbarian: Path of the Berserker" -> "Path of the Berserker"
            subclass = label.split(":", 1)[1].strip() if ":" in label else ""
            current = (book, m.group(2), subclass)
        elif SECTION_HEADER.match(line):
            current = None      # a new section with no page: stop leaking the old one
    return section_pages


def subclass_sources():
    """norm(feature) -> owning subclasses, and subclass -> its source string."""
    import main  # noqa: PLC0415  (heavy import; scripts are one-shot)

    owners = defaultdict(set)
    for sub_name, levels in (getattr(main, "SUBCLASS_FEATURES", {}) or {}).items():
        if not isinstance(levels, dict):
            continue
        for _lvl, feats in levels.items():
            for f in (feats if isinstance(feats, list) else [feats]):
                n = f.get("name") if isinstance(f, dict) else f
                if n:
                    owners[norm(n)].add(sub_name)
    sources = {}
    if SUBCLASSES.exists():
        for rec in json.loads(SUBCLASSES.read_text()):
            if not isinstance(rec, dict):
                continue
            name = (rec.get("name") or "").strip()
            src = (rec.get("source") or "").strip()
            if name and src:
                sources[name] = src
            for f in (rec.get("features") or []):
                n = f.get("name") if isinstance(f, dict) else f
                if n:
                    owners[norm(n)].add(name)
    return owners, sources


def class_and_trait_pages():
    """feature -> source for class features and racial traits, from the maps the
    app already ships: SRD_FEATURES says which class owns a feature, and
    class_page_map / trait_page_map know those classes' and traits' pages."""
    import main  # noqa: PLC0415

    class_pages = {}
    cpm = REPO / "data" / "page_maps" / "class_page_map.json"
    if cpm.exists():
        for name, info in json.loads(cpm.read_text()).items():
            src = (info or {}).get("source_str") if isinstance(info, dict) else None
            if src:
                class_pages[norm(name)] = src

    feature_class = defaultdict(set)
    for feat in (getattr(main, "SRD_FEATURES", []) or []):
        if not isinstance(feat, dict):
            continue
        cls = feat.get("class") or {}
        cls_name = cls.get("name") if isinstance(cls, dict) else cls
        fname = str(feat.get("name", "")).strip()
        if cls_name and fname:
            # "Action Surge (1 use)" and "Action Surge" are the same feature
            feature_class[norm(fname.split(" (", 1)[0])].add(cls_name)

    trait_pages = {}
    tpm = REPO / "data" / "page_maps" / "trait_page_map.json"
    if tpm.exists():
        for name, src in json.loads(tpm.read_text()).items():
            if (src or "").strip():
                trait_pages[norm(name)] = src

    def lookup(key: str) -> str:
        k = norm(key)
        classes = feature_class.get(k) or set()
        if len(classes) == 1:
            src = class_pages.get(norm(next(iter(classes))))
            if src:
                return src
        return trait_pages.get(k, "")

    return lookup


def main_() -> int:
    import data as app_data            # noqa: PLC0415
    import routes.characters           # noqa: F401,PLC0415  (runs the merges)
    import routes.characters.all as allmod  # noqa: PLC0415
    from collections import Counter     # noqa: PLC0415
    live = app_data.FEATURE_DESCRIPTIONS
    sub_desc = {norm(k): v for k, v in (allmod.SUBCLASS_FEATURE_DESCRIPTIONS or {}).items()}
    cd_desc = {norm(k): v for k, v in (allmod.CHANNEL_DIVINITY_DESCRIPTIONS or {}).items()}

    sections = registry_sections()
    owners, sub_src = subclass_sources()
    fallback = class_and_trait_pages()
    # A subclass named in a registry section has a page; features inherit it the
    # same way trait_page_map gives a trait its parent race's page.
    subclass_page = {}
    for _k, (book, page, sub) in sections.items():
        if sub:
            subclass_page.setdefault(norm(sub), f"{book} p.{page}")

    def section_claim(k):
        """The registry section's (book, page) — only if the key belongs to it."""
        sec = sections.get(k)
        if not sec:
            return None
        book, page, section_subclass = sec
        if section_subclass:
            named = {norm(x) for x in owners.get(k, set())}
            if norm(section_subclass) not in named:
                return None      # the key is not that subclass's feature
        return f"{book} p.{page}"

    out, stats = {}, Counter()
    for key, text in live.items():
        k = norm(key)
        if not k or "::" in key:
            continue      # "Subclass::feature" is an internal lookup key, not an entity
        # ── chain 1: the registry's own section label, text must still match ──
        registry_text = sub_desc.get(k) or cd_desc.get(k)
        owner = "subclass" if sub_desc.get(k) is not None else (
            "channel divinity" if cd_desc.get(k) is not None else "")
        if registry_text is not None and text == registry_text:
            claim = section_claim(k)
            if claim:
                out[k] = claim
                stats[f"all.py {owner} registry section"] += 1
                continue
            stats["in a registry but no trustworthy section"] += 1
        # ── chain 2: exactly one subclass owns the name and has a source ──
        mine = owners.get(k) or set()
        if len(mine) == 1:
            only = next(iter(mine))
            src = subclass_page.get(norm(only)) or sub_src.get(only, "")
            if src:
                out[k] = src
                stats["sole owning subclass's source"] += 1
                continue
            stats["sole owner without a source"] += 1
        elif len(mine) > 1:
            stats["claimed by several subclasses"] += 1
        else:
            stats["no subclass claims it"] += 1
        # ── chain 3: class features (via SRD_FEATURES) and racial traits ──
        src = fallback(key)
        if src:
            out[k] = src
            stats["class/trait page map"] += 1
        else:
            stats["still unattributed"] += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(dict(sorted(out.items())), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO)}: {len(out)} of {len(live)} feature keys attributed")
    for k, v in stats.most_common():
        print(f"  {v:5}  {k}")
    with_page = sum(1 for v in out.values() if re.search(r"p\.?\s*\d+", v))
    print(f"  {with_page:5}  carry an explicit page (linkable), {len(out) - with_page} book-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
