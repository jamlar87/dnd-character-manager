#!/usr/bin/env python3
"""Register limited-use traits that have no FEATURE_ACTION_TYPES entry.

Every fold can add traits, and the sweep logs `ACTION-TYPE GAP` when it does. Doing that by hand
for a five-hour sweep is a treadmill, so this closes it: for each unregistered key it finds the
feature's own description and reads the action type from the wording.

Cue precedence (deliberately conservative — the text must say it):

  1. "as a bonus action" / "use a bonus action" / "may use his|her|its bonus action" -> Bonus Action
  2. "as a reaction" / "use a reaction"                                        -> Reaction
  3. "as an action" / "action to"                                              -> Action
  4. nothing explicit                                                          -> Special

Special is the honest default: most unmatched traits are passives, spellcasting, or riders that
cost no action of their own, and a wrong badge is worse than a generic one. The tooltip carries the
feature's own words, so a human can see what it actually does.

Idempotent, and it writes into data.py under a clearly marked block so the provenance is obvious.
Prints nothing when there is nothing to do (cron-friendly).

Run: .venv/bin/python3 scripts/register_action_gaps.py [--dry-run]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

MERGED = HERE / "data" / "manual_data"
DATA_PY = HERE / "data.py"
ANCHOR = "\n}\n\n# ── Always-on combat rider callout cards"
MARK = "    # ── auto-registered by scripts/register_action_gaps.py ──────────────────"

BONUS = re.compile(r"(as|use|uses|using|spend|expend|may use (?:his|her|its|their))[^.]{0,30}bonus action", re.I)
REACTION = re.compile(r"(as|use|uses|using|spend|expend|may use (?:his|her|its|their))[^.]{0,30}reaction", re.I)
ACTION = re.compile(r"\bas an action\b|\baction to\b|(?:use|uses|using|spend|expend)[^.]{0,20}\ban action\b", re.I)


def clean_key(key: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", key).strip()


CAST_ACTION = re.compile(r"\b1?\s*(?:standard\s+)?action\b", re.I)
CAST_BONUS = re.compile(r"\bbonus action\b", re.I)
CAST_REACT = re.compile(r"\breaction\b", re.I)


def spell_times() -> dict[str, str]:
    """key -> a spell's casting_time, which is explicit evidence about its action cost."""
    out: dict[str, str] = {}
    path = MERGED / "spells.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return out
    for entry in (data if isinstance(data, list) else []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip().lower()
        cast = re.sub(r"\s+", " ", str(entry.get("casting_time") or "")).strip()
        if name and cast:
            out.setdefault(name, cast)
    return out


def descriptions() -> dict[str, str]:
    """key -> the feature's own description, from the merged data.

    Walks the JSON recursively: the merged files are not all list-shaped (races.json is keyed by
    race), and scanning only top-level lists silently ignored every trait in those files — which is
    why "Silent Steps" was reported as having no description while sitting in races.json.
    """
    found: dict[str, str] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            name = str(node.get("name") or "").strip()
            text = node.get("description")
            if name and isinstance(text, str) and text.strip():
                key = name.lower()
                clean_text = re.sub(r"\s+", " ", text).strip()
                if key not in found or len(clean_text) > len(found[key]):
                    found[key] = clean_text
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(MERGED.glob("*.json")):
        try:
            walk(json.loads(path.read_text()))
        except Exception:
            continue
    return found


SPELL_MENTION = re.compile(r"\bcast(?:ing)?\s+(?:the\s+)?([a-z][a-z' -]{2,40}?)\s+(?:spell\b|at will|once|as)", re.I)


def infer(text: str, cast: str = "", spells: dict[str, str] | None = None) -> str:
    """The action type, from the feature's own words.

    A spell's casting_time is explicit, so it outranks prose. The MPG spell "Mucus Spray" is
    "1 standard action", and that is how its subclass twin "Mucus Spray (Sp)" is typed too — the
    feature text never says. A trait that grants a spell inherits that spell's casting time as well:
    "Silent Steps" lets you cast *silence*, which is an action.

    In prose the **earliest cue wins**, because the activation sentence comes before any follow-up
    commands. "Animating Performance" opens "As an action, you can animate…" and mentions the bonus
    action used to command the animated item only afterwards; taking the bonus action there would
    label the whole feature wrong.
    """
    if not cast and spells:
        match = SPELL_MENTION.search(text)
        if match:
            cast = spells.get(match.group(1).strip().lower(), "")
    for pattern, kind in ((CAST_BONUS, "Bonus Action"), (CAST_REACT, "Reaction"), (CAST_ACTION, "Action")):
        if pattern.search(cast):
            return kind
    earliest: tuple[int, str] | None = None
    for pattern, kind in ((BONUS, "Bonus Action"), (REACTION, "Reaction"), (ACTION, "Action")):
        match = pattern.search(text)
        if match and (earliest is None or match.start() < earliest[0]):
            earliest = (match.start(), kind)
    return earliest[1] if earliest else "Special"


def main() -> int:
    dry = "--dry-run" in sys.argv

    from services.data_loader import load_manual_data
    load_manual_data()
    from data import LIMITED_USE, FEATURE_ACTION_TYPES

    missing = sorted(
        k for k in LIMITED_USE
        if k not in FEATURE_ACTION_TYPES and clean_key(k) not in FEATURE_ACTION_TYPES
    )
    if not missing:
        return 0

    desc = descriptions()
    casts = spell_times()
    lines: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for key in missing:
        bare = clean_key(key)
        # "Mucus Spray (Sp)" and "Mucus Spray" strip to one key: register it once, or data.py gets a
        # duplicate dict key (the second silently shadows the first).
        if bare in FEATURE_ACTION_TYPES or bare in seen:
            continue
        seen.add(bare)
        text = desc.get(key) or desc.get(bare) or ""
        cast = casts.get(bare) or casts.get(key) or ""
        if not text and not cast:
            skipped.append(key)          # no description anywhere: needs a human, do not guess
            continue
        kind = infer(text, cast, casts)
        tip = (text or f"Casting time: {cast}")[:110].rstrip()
        if len(text) > 110:
            tip = tip[: tip.rfind(" ")] + "…"
        tip = tip.replace('"', "'")
        lines.append(f'    "{bare}": ' + " " * max(1, 30 - len(bare)) + f'("{kind}", "{tip}"),')

    if not lines:
        print(f"register_action_gaps: {len(skipped)} key(s) have no description; needs a human: {skipped}")
        return 0

    print(f"register_action_gaps: {len(lines)} key(s) to register"
          + (f", {len(skipped)} left for a human" if skipped else ""))
    for line in lines:
        print("   " + line.strip())

    if dry:
        print("\nDRY RUN — data.py untouched.")
        return 0

    src = DATA_PY.read_text()
    if ANCHOR not in src:
        print("register_action_gaps: anchor not found in data.py — refusing to edit")
        return 1
    block = (MARK + "\n" if MARK not in src else "") + "\n".join(lines) + "\n"
    DATA_PY.write_text(src.replace(ANCHOR, "\n" + block + ANCHOR, 1))
    print(f"\nwrote {len(lines)} entr(ies) into data.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
