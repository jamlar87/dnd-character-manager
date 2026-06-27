#!/usr/bin/env python3
"""Test the improved _find_best_context function on specific monsters."""

import re, sys
sys.path.insert(0, '.')
from pathlib import Path

CACHE_DIR = Path('data/manual_cache')

def _find_best_context(text, monster_name, context_chars=3000):
    if not text:
        return None
    patterns = [re.escape(monster_name)]
    base = re.sub(r'\s*\(.*?\)\s*$', '', monster_name)
    if base != monster_name:
        patterns.append(re.escape(base))
    stat_indicators = ['Armor Class', 'Hit Points', 'STR', 'DEX', 'CON',
                       'INT', 'WIS', 'CHA', 'AC ', 'HP ', 'Speed ', 'Challenge']
    best_match = None
    best_score = -999
    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            start = max(0, match.start() - context_chars // 2)
            end = min(len(text), match.end() + context_chars // 2)
            pm = text.rfind('--- PAGE ', max(0, start - 200), start)
            if pm >= 0: start = pm
            pn = text.find('--- PAGE ', end, end + 200)
            if pn >= 0: end = pn
            context = text[start:end].strip()
            score = 0
            nearby = text[max(0, match.start()-500):min(len(text), match.end()+500)]
            for ind in stat_indicators:
                if ind in nearby: score += 2
            if len(nearby) < 300: score -= 5
            toc_dots = nearby.count('..') + nearby.count('.....')
            if toc_dots > 2: score -= 5
            before_name = context[:match.start() - start].strip()
            if len(before_name) > 100 and not any(ind in context for ind in stat_indicators):
                score += 3
            if score > best_score:
                best_score = score
                best_match = context
    return best_match, best_score

stat_indicators = ['Armor Class', 'Hit Points', 'STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA', 'AC ', 'HP ', 'Speed ', 'Challenge']

tests = [
    ('VGM', 'Gazer'),
    ('VGM', 'Bheur Hag'),
    ('CSF', 'Shadow Fey Cutthroat'),
    ('TFS', 'zombie mastiff'),
    ('WRKF', 'Mulchmouth, Bugbear Leader'),
    ('KW', 'Gelatinous Cube Familiar'),
    ('TMFRV', 'Dancing Stone Familiar'),
    ('WRKF', 'Sir Oberest the Green (Sidhe Knight)'),
    ('EBT', 'Shadow Fey Bandit'),
    ('CotN', 'Horizonback Tortoise'),
]

for slug, name in tests:
    text = (CACHE_DIR / f'{slug}.txt').read_text(errors='replace')
    ctx, score = _find_best_context(text, name)
    print(f'\n=== {slug}:{name} (score={score}) ===')
    if ctx:
        has_stats = any(ind in ctx[:800] for ind in ['Armor Class', 'STR', 'DEX', 'CON', 'HP '])
        print(f'  Length: {len(ctx)} chars, stat block nearby: {has_stats}')
        print(f'  ---')
        print(ctx[:500])
        print('  ...')
    else:
        print('  NOT FOUND in text')
