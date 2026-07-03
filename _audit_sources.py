"""Audit all source formats in manual data files."""
import json, re, os
from collections import Counter

DATA_DIR = "data/manual_data"
FILES = [
    "magic_items.json", "npcs.json", "equipment.json", "spells.json",
    "feats.json", "backgrounds.json", "races.json", "subclasses.json",
    "monsters.json",
]

# Known clean format pattern: (Manual, pg#) where pg# is like "p.23", "p23", "23"
CLEAN_PATTERN = re.compile(r'^\([A-Za-z0-9 /&:_-]+,\s*p\.?\s*\d+\)$')

def extract_sources(entry):
    """Yield all source strings found in an entry."""
    # Direct source fields
    for key in ('source', '_source', '_source_manual', 'source_manual', 'manual_source'):
        v = entry.get(key) or (entry.get('metadata') or {}).get(key)
        if v:
            yield ('field', key, str(v))
            break
    else:
        # Check description/text for (Source, pg#) patterns
        for field in ('description', 'text', 'desc'):
            txt = entry.get(field, '')
            if txt:
                for m in re.finditer(r'\(([^)]+)\)', txt):
                    s = m.group(1).strip()
                    if re.search(r'(Manual|pg|p\.|Adventure|Campaign|SCAG|XGE|TCE|VGM|MTF|GGR|DMG|PHB|EEPC|LMoP|ToA|HotDQ|RoT|WDH|CoS|OotA|SKT|TTP|GoS|MOT|IDRotF)', s, re.I):
                        yield ('desc_field', field, s)

for fn in FILES:
    path = os.path.join(DATA_DIR, fn)
    data = json.load(open(path))
    items = list(data.values()) if isinstance(data, dict) else data
    
    all_sources = Counter()
    clean_count = 0
    messy_count = 0
    messy_samples = []
    
    for item in items:
        has_source = False
        for kind, key, val in extract_sources(item):
            all_sources[f"{kind}:{key}={val[:150]}"] += 1
            has_source = True
            if CLEAN_PATTERN.match(val):
                clean_count += 1
            else:
                messy_count += 1
                if len(messy_samples) < 20:
                    messy_samples.append(f"  [{kind}:{key}] {val[:150]}")
    
    print(f"\n=== {fn} ({len(items)} entries) ===")
    print(f"  Clean format:  {clean_count}")
    print(f"  Messy format:  {messy_count}")
    
    if messy_samples:
        print(f"  Messy samples:")
        for s in messy_samples:
            print(s)
    
    print(f"  Source value distribution:")
    for src, cnt in all_sources.most_common(30):
        print(f"    [{cnt:>3}] {src[:150]}")
