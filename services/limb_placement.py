"""Turn a limb COUNT into a limb LAYOUT before the prompt reaches the model.

Measured on the local Qwen-Image-2.1 back end (bazzite, comfy gateway :8189), 2026-11-18:

  * "three thick clawed stone arms and three stumpy stone legs"        -> 4 arms, 2 legs
  * "standing upright on three stone legs"                            -> 3 legs, wrong subject
  * "three arms — one growing from the centre of its chest and one    -> 3 arms, 3 legs, 3 eyes
     from each shoulder — and three stumpy stone legs"                    (matches the manual)
  * the same clause on DreamShaper XL (sdxl)                          -> 2 arms, 2 legs, 2 eyes

So the number is the part the encoder drops: it has priors for "arm" and "leg" but not for the
arithmetic. Where each limb sits is a thing it has drawn a thousand times, so the layout is what
carries the count. Plain integers ("3 arms") measured no better than the word.

Layouts live here rather than in data/creature_clauses.json for two reasons: the clause file is the
audit's evidence and must stay a description of the creature, and a layout is a rendering
instruction with a known-wrong default (a biped bias) that has to be stated every time.

Punctuation note: no commas inside a layout. _creature_clause_prompt trims an over-budget clause by
dropping comma-separated parts from the end, so a comma in the layout would make the layout the
first thing thrown away.

RESULT — wired in, measured, reverted, 2026-11-18. Do not re-add this to ref_portraits without
solving the budget first. Eleven hard-bodied creatures were regenerated through the live path with
the expansion active and vision-audited:

  * Xorn     -> 2 legs, 2 arms, 2 eyes, mouth on the front of the face (clause says 3/3/3 + mouth on
                top). The layout cost the words that were previously working: "three eyes and a huge
                gaping mouth on top of its head" was trimmed away to admit the layout.
  * Sleipnir -> lead trimmed until the creature's own name was gone from the prompt.
  * Tlincalli-> the trim cut "a segmented scorpion lower body" and left the leg layout hanging off
                "humanoid upper torso", i.e. a worse clause than the one it replaced.
  * Retriever, Giant Fly, Froghemoth, Spectator, Drider, Duodrone, Owlbear, Wolf: no topology gain.
  * Prompt budget is 48 words / 77 CLIP tokens. A layout costs 6-10 words, and trimming to pay for
    it deletes the descriptive facts (eyes, maw, name) that were doing the work.

Conclusion: on this back end the winning lever is not the wording at all — it is resampling. The one
Xorn that came out 3/3/3 was a lucky sample of the count-only clause, and the same clause re-rolled
to 2/2/2. Fixing multi-limb topology needs either a back end with real pose conditioning (SDXL +
topology LoRA, ControlNet from a reference plate) or a generate-then-verify loop with a vision model
that keeps the best of N. Prompt vocabulary is exhausted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_COUNTS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12,
}

#: Default layouts by (limb, count). Only counts a biped/quadruped prior gets wrong are listed: four
#: legs is what the model draws unprompted, so expanding it would only spend words.
_LAYOUT = {
    ("arm", 3): "one from the chest and two from the shoulders",
    ("arm", 4): "two on each side one above the other",
    ("arm", 6): "three on each side",
    ("arm", 8): "four on each side",
    ("leg", 3): "two in front and one behind",
    ("leg", 5): "two in front and three behind",
    ("leg", 6): "three on each side",
    ("leg", 8): "four on each side along a long body",
    ("tentacle", 4): "four tentacles ringing a central beak",
    ("tentacle", 6): "six tentacles ringing a central beak",
    ("tentacle", 8): "eight tentacles ringing a central beak",
    ("tentacle", 10): "ten tentacles ringing a central beak",
    ("eyestalk", 4): "four eyestalks on top of a floating orb",
}

#: Per-record layouts where the generic one is wrong for that body. Keyed exactly as the clause file.
_OVERRIDE = {
    "Xorn": {
        "arm": "one from the chest and two from the shoulders",
        "leg": "two in front and one behind",
    },
    "Duodrone": {"arm": "two on each side one above the other"},
    "Four-Armed Troll": {"arm": "two on each side one above the other"},
    "Molydeus": {"arm": "two on each side one above the other"},
    "Girallon": {"arm": "two on each side one above the other"},
    "Girallon Zombie": {"arm": "two on each side one above the other"},
    "Lunarchidna": {"arm": "two on each side one above the other",
                    "leg": "two on each side one behind the other"},
    "Sleipnir": {"leg": "four on each side of a long horse body"},
    "Drider": {"leg": "four on each side"},
    "Retriever": {"leg": "four on each side"},
    "Tlincalli": {"leg": "six scorpion legs three on each side below the humanoid torso"},
    "Giant Fly": {"leg": "three on each side"},
    "Froghemoth": {"tentacle": "ringing a central fang-filled maw"},
    "Mind Flayer": {"tentacle": "four tentacles ringing a central mouth"},
    "Spectator": {"eyestalk": "on top of a floating orb"},
}

_CLAUSE = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|twelve)\s+"
    r"((?:[a-z][a-z-]*\s+){0,2}?)"
    r"(arms?|legs?|tentacles?|eyestalks?)\b",
    re.I,
)


def _limbs(name: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in _OVERRIDE.get((name or "").strip(), {}).items()}


def extra_limbs() -> dict[str, dict[str, str]]:
    """Optional on-disk addition, so a new hard body does not need a code change."""
    path = Path(__file__).resolve().parent.parent / "data" / "creature_limbs.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for record, limbs in (raw or {}).items():
        if isinstance(limbs, dict):
            out[str(record)] = {str(k).rstrip("s").lower(): str(v) for k, v in limbs.items()}
    return out


def expand(clause_lead: str, name: str = "") -> str:
    """Rewrite each countable limb group in the clause as a layout. Idempotent, and silent when there
    is nothing odd to say: four legs, two arms and two legs are left exactly as written."""
    text = clause_lead or ""
    if not text:
        return text
    layouts = _limbs(name)
    if not layouts:
        on_disk = extra_limbs().get((name or "").strip())
        layouts = on_disk or {}

    def one(m: re.Match) -> str:
        word, adjectives, noun = m.group(1).lower(), m.group(2), m.group(3).lower()
        count = _COUNTS.get(word, 0)
        key = noun.rstrip("s")
        if not count:
            return m.group(0)
        layout = layouts.get(key) or _LAYOUT.get((key, count))
        if not layout:
            return m.group(0)
        tail = text[m.end():m.end() + 2]
        # already laid out (an em dash follows) - do not stack a second description
        if tail.lstrip().startswith("—") or tail.lstrip().startswith("-"):
            return m.group(0)
        return f"{word} {adjectives}{noun} — {layout}"

    return _CLAUSE.sub(one, text)
