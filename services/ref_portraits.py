"""Reference art for monsters, items and the manual NPC library.

A different problem from character portraits: these are thousands of *shared*
rows (1,936 creatures, 1,559 items, 414 NPCs) and they are not DB rows at all —
they come from the entity index. Storing base64 in a row is impossible here and
a data URL in the HTML is exactly what made /dm-tools 3.3 MB.

So reference art lives as FILES under static/ref-portraits/<kind>/, served by a
route that offers `?size=` thumbnails, and **file existence is the state**:

  - lazy: a request for a missing image starts a background generation and
    answers 404, so the caller keeps showing its letter tile and the picture is
    there on the next view;
  - bulk: scripts/generate_portraits.py --kind prewarms the same directory.

Both paths write through here, so they cannot disagree, and both are resumable.
Generation is serialised (one request at a time) because the provider is a free
tier with rate limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
ROOT = STATIC / "ref-portraits"

#: kind -> (width, height). Busts are 3:4, objects square on the canvas.
KINDS: dict[str, tuple[int, int]] = {
    "creature": (768, 1024),
    "npc": (768, 1024),
    "item": (768, 768),
}

_INFLIGHT: set[str] = set()
_LOCK = threading.Lock()
# one generation at a time — the free provider rate limits hard
_SEM = asyncio.Semaphore(1)


def slug_for(name: str) -> str:
    """Deterministic, collision-free, human-readable file name."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80] or "unnamed"
    digest = hashlib.md5((name or "").encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def path_for(kind: str, name: str) -> Path:
    return ROOT / kind / f"{slug_for(name)}.webp"


def have(kind: str, name: str) -> bool:
    p = path_for(kind, name)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


#: Constructs are not animals. The bestiary wording — "full body, single creature" — is exactly why
#: the battering ram came out fleshy and the clockwork entries came out organic; the prompt asserts
#: the thing is alive. Each family gets its own material vocabulary, and every variant states plainly
#: that nothing here is living. Order matters: the first match wins, so the specific cues lead.
#:
#: These are kept SHORT on purpose. SDXL truncates at 77 tokens, so a wordy cue pushes the subject off
#: the end of the prompt entirely (see FANTASY_STYLE in services/portraits.py). The material words are
#: what matter; the connective prose is not worth a token.
CONSTRUCT_CUES = (
    (re.compile(r"\bclockwork\b|\bgearwork\b|\bmechanical\b", re.I),
     "a clockwork machine of brass and blackened steel, exposed gears, ratchets, riveted plates, "
     "a glowing arcane core, no flesh"),
    (re.compile(r"\bgolem\b", re.I),
     "a golem of fitted metal plates and stone, bolted seams, jointed limbs, a rune-lit core, "
     "no flesh"),
    (re.compile(r"\bairship\b|\bflying ship\b|\bvehicle\b|\bcarriage\b|\bwagon\b|\bboat\b", re.I),
     "a built vessel of timber, canvas and metal fittings, rigging and a rigid frame, "
     "no people and no crew"),
    (re.compile(r"\bbattering ram\b|\bsiege\b|\bballista\b|\btrebuchet\b|\bcatapult\b|\bmangonel\b", re.I),
     "a siege engine of heavy timber beams, iron bands, rope and counterweight, a built machine, "
     "no living thing"),
    (re.compile(r"\banimated\b|\bconstruct\b|\bautomaton\b|\bmodron\b|\bcauldronborn\b|\bdreadnought\b", re.I),
     "an artificial construct of metal, wood and stone, bolted plates, glowing seams, no flesh"),
)


def construct_cue(name: str, detail: str = "") -> str | None:
    """The material description for a construct, or None when this is an ordinary living thing.

    The name is checked against every cue. The subtitle only counts for the plain type words
    ("Construct · CR 5"), never for the specific families: a "Battering Shield" is an ordinary
    shield whose subtitle can mention siege equipment, and giving it a siege engine's description
    would be the same class of mistake as the battering ram looking alive.
    """
    for pattern, description in CONSTRUCT_CUES:
        if pattern.search(name or ""):
            return description
    if re.search(r"\bconstruct\b|\bautomaton\b|\bmodron\b", detail or "", re.I):
        return CONSTRUCT_CUES[-1][1]
    return None


def prompt_for(kind: str, name: str, subtitle: str = "", snippet: str = "") -> str:
    """Every reference prompt, forced into the house style (fantasy-coded, blank background).

    A thin wrapper rather than a clause on each return: there are four exits below, and a rule applied
    in four places is a rule that eventually gets applied in three. The import is local because
    services.portraits already imports this module's caller — see _base_prompt.
    """
    from services.portraits import house_style
    return house_style(_base_prompt(kind, name, subtitle, snippet))


# --- who the record actually is ------------------------------------------------------------------
# Two failures this pair exists to prevent, both seen in real output:
#
#   1. "Khelkur the Gull" is a DWARF. The portrait came back as a white eagle in armour, because the
#      only concrete noun the model had was the nickname. The race is stated in the subtitle and was
#      simply never used.
#   2. "Insight Acuere" is a tiefling who is referred to as "She" in her own description. The
#      portrait came back male: the records carry no gender field, so nothing said otherwise.
#
# Both are fixed by reading what the record already says. Neither guesses: when the evidence is not
# there, the prompt stays silent, and the model picks something plausible rather than contradicting.

_SHE_PRONOUN = re.compile(r"\b(she|her|hers|herself)\b", re.I)
_HE_PRONOUN = re.compile(r"\b(he|him|his|himself)\b", re.I)

# Subtitles that are not races. "any race" is a placeholder on 17 records; the rest are filing labels.
_NOT_A_RACE = {"any race", "varies", "custom", "unknown", "n/a", "none"}
_RACE_MAX_WORDS = 3

# "Khelkur the Gull" is a dwarf. The portrait came back as a white eagle in armour — and repeating it
# produced a dwarf, so the prompt does not DETERMINISTICALLY mean "draw a bird": it merely permits
# that reading, and the model takes it some of the time. A one-in-N wrong portrait is still a wrong
# portrait, so the ambiguous noun is removed rather than out-voted.
#
# Deliberately only animals. Of 42 names shaped "X the <Word>", the rest are trapper, trader, crown,
# mountain, tempest, guildpact — those describe a person's trade or standing and hurt nothing, so they
# stay. Only these words can turn a humanoid into a beast.
_BEAST_EPITHET = {
    "gull", "crow", "raven", "hawk", "falcon", "eagle", "owl", "sparrow", "wren", "swan", "heron",
    "crane", "stork", "vulture", "kite", "magpie", "jackdaw", "starling", "finch", "lark", "robin",
    "wolf", "fox", "bear", "boar", "stag", "hart", "lion", "tiger", "panther", "lynx", "badger",
    "otter", "hare", "rabbit", "rat", "mouse", "weasel", "marten", "hound", "hound", "bull", "ram",
    "goat", "horse", "stallion", "mare", "snake", "serpent", "adder", "viper", "cobra", "spider",
    "scorpion", "crab", "shark", "eel", "pike", "trout", "salmon", "tuna", "ray", "whale", "seal",
    "moth", "beetle", "hornet", "wasp", "locust",
}

_EPITHET_RE = re.compile(r"\s+the\s+([A-Za-z'’-]+)\s*$", re.I)


def name_for_portrait(name: str) -> str:
    """Drop an animal nickname from a humanoid's name — 'Khelkur the Gull' -> 'Khelkur'.

    Only animal epithets, and only the trailing one. A pronoun of the trade ('the Trapper') or of
    standing ('the Crown') is kept, because its presence does not invite the model to draw the wrong
    species. See _BEAST_EPITHET for why this is a list and not a rule: a heuristic that turns every
    nickname into a deletion would quietly rename people.
    """
    text = (name or "").strip()
    m = _EPITHET_RE.search(text)
    if m and m.group(1).lower() in _BEAST_EPITHET:
        return text[:m.start()].strip() or text
    return text


def race_from_subtitle(subtitle: str) -> str:
    """The race from a 'Race · Class' subtitle, or '' when it cannot be read as one.

    The subtitle shape is what 788 of 799 NPC records use, and the race always leads. Deliberately
    strict about what counts: a title like 'Chapter 4 | The Jewel of Hope' must not be handed to the
    model as a species, because a wrong race is worse than an absent one — absent leaves the
    description to decide, wrong actively fights it.
    """
    first = (subtitle or "").split("·")[0].strip()
    if not first or first.startswith("(") or "|" in first or len(first) > 20:
        return ""
    if first.lower() in _NOT_A_RACE:
        return ""
    if len(first.split()) > _RACE_MAX_WORDS:
        return ""
    return first


def gender_from_text(text: str) -> str:
    """'female' / 'male' from the prose, '' when it is not clear.

    The descriptions do use pronouns — Insight Acuere's says "She has resistance to fire damage" — but
    there is no gender field anywhere in the records.

    Both pronoun sets, or neither, returns ''. This is the whole point: the model invents something
    plausible when nothing constrains it, but it will not contradict a stated gender, so a wrong
    guess is strictly worse than saying nothing. 293 of 799 records stay unspecified on purpose.
    """
    body = text or ""
    she = len(_SHE_PRONOUN.findall(body))
    he = len(_HE_PRONOUN.findall(body))
    if she and not he:
        return "female"
    if he and not she:
        return "male"
    return ""


def _base_prompt(kind: str, name: str, subtitle: str = "", snippet: str = "") -> str:
    """Prompt per kind. Same shape as the character prompts (bust/3:4 language
    comes from services.portraits) so the library looks consistent."""
    # Caps are in words, not characters. The real limit is TOKENS (77, see
    # tests/test_prompt_token_budget.py) and the old 120/200-char caps let a verbatim book name and
    # snippet into the prompt, pushing the actual SUBJECT past the window where CLIP silently drops it.
    # A record called "Clockwork Oaken Bolter of the Nine Gilded Spires of Mechanus" alone ate 10 words.
    detail = " ".join((subtitle or "").split()[:7])
    tail = " ".join((snippet or "").split()[:15])
    short_name = " ".join((name or "").split()[:8])
    cue = construct_cue(name, detail)
    if kind == "creature":
        if cue:
            return ("Fantasy construct: " + (short_name or "a construct")
                    + (f", {detail}." if detail else ".")
                    + f" {cue}."
                    + " Full body, single subject." + (f" {tail}" if tail else ""))
        return ("Fantasy bestiary: " + (short_name or "a monster")
                + (f", {detail}." if detail else ".")
                + " Full body, single creature." + (f" {tail}" if tail else ""))
    if kind == "item":
        if cue:
            # The cue-bearing items are the vehicles and engines, and the old wording hurt them
            # twice: "RPG item illustration" leads a diffusion model toward a small hand-held object,
            # and interpolating the record's category produced "Carriage (Mounts and Vehicles)" — a
            # filing label, not a description. The object itself now leads.
            return ("Fantasy construct: " + (short_name or "an object")
                    + f". {cue}." + (f" {tail}" if tail else ""))
        return ("Fantasy object: " + (short_name or "an item")
                + (f" ({detail})" if detail else "")
                + "." + (f" {tail}" if tail else ""))
    # npc / anything else -> the character prompt builder keeps the look consistent.
    # Gender is read from the FULL snippet, before the 15-word cap above: Insight Acuere's "She" sits
    # past the cut, which is exactly how that portrait ended up male.
    from services.portraits import npc_prompt
    return npc_prompt(name_for_portrait(name), notes=tail or snippet or "",
                      race=race_from_subtitle(subtitle),
                      gender=gender_from_text(snippet))


def save(kind: str, name: str, data_url: str) -> int:
    """Write the image atomically. Returns bytes written (0 = nothing stored)."""
    from services.images import decode_data_url, thumbnail_bytes
    decoded = decode_data_url(data_url)
    if not decoded:
        return 0
    blob, _media = decoded
    if not blob:
        return 0
    # same shrink rule as every portrait write: WebP, capped at 1024px
    thumb = thumbnail_bytes(blob, 1024)
    out = thumb[0] if thumb else blob
    dest = path_for(kind, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".webp.tmp")
    tmp.write_bytes(out)
    tmp.replace(dest)          # atomic: a reader never sees a half-written file
    return len(out)


async def generate(kind: str, name: str, subtitle: str = "", snippet: str = "",
                   max_wait: float = 120, retries: int = 2,
                   force: bool = False) -> tuple[Path | None, str | None]:
    """Generate one image. Returns (path, error).

    `force` regenerates over an existing file. Without it an existing portrait is handed straight
    back, which is correct for the lazy path and for resuming a bulk run — but it silently defeats a
    redo: --force selected the rows and every one returned "in 0s" carrying the old picture, logged
    as success.
    """
    if kind not in KINDS:
        return None, f"unknown kind '{kind}'"
    if not force and have(kind, name):
        return path_for(kind, name), None

    key = f"{kind}/{name}"
    with _LOCK:
        if key in _INFLIGHT:
            return None, "already generating"
        _INFLIGHT.add(key)
    try:
        from services.portraits import generate_portrait_image
        width, height = KINDS[kind]
        prompt = prompt_for(kind, name, subtitle, snippet)
        last = "no attempt made"
        async with _SEM:
            for attempt in range(retries + 1):
                data, err = await generate_portrait_image(prompt, max_wait=max_wait,
                                                          width=width, height=height)
                if data:
                    size = save(kind, name, data)
                    if size:
                        return path_for(kind, name), None
                    last = "generated image could not be stored"
                else:
                    last = err or "no image returned"
                if attempt < retries:
                    # Rate limits are the norm on the free tier: wait a minute,
                    # then longer, rather than skipping the entity and hoping a
                    # later re-run catches it.
                    rate_limited = "rate limit" in (last or "").lower()
                    await asyncio.sleep((60 if rate_limited else 15) * (attempt + 1))
        return None, last
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


#: Written by scripts/generate_portraits.py while a bulk run is in progress. A
#: browser page with thousands of tiles would otherwise kick thousands of lazy
#: generations that duplicate the bulk run and get both rate limited to death.
BULK_MARKER = ROOT / ".bulk-running"
BULK_MARKER_MAX_AGE = 12 * 3600


def bulk_running() -> bool:
    """True while a recent bulk run owns the provider (stale markers ignored)."""
    import time
    try:
        return BULK_MARKER.is_file() and (time.time() - BULK_MARKER.stat().st_mtime) < BULK_MARKER_MAX_AGE
    except OSError:
        return False


# ── interactive priority ─────────────────────────────────────────────────────
# The web app's portrait generation and the batch backfill draw on one shared
# free quota. A user clicking Generate must not lose that race to a backfill, so
# the app marks its in-flight generations and the batch stands down. The marker
# ages out (below), so a crashed request can never stall the batch forever.
INTERACTIVE_MARKER = ROOT / ".user-generating"
INTERACTIVE_MAX_AGE = 240
_interactive_lock = threading.Lock()
_interactive_depth = 0


def mark_interactive() -> None:
    global _interactive_depth
    with _interactive_lock:
        _interactive_depth += 1
        try:
            INTERACTIVE_MARKER.write_text(str(_interactive_depth))
        except OSError:
            pass


def clear_interactive() -> None:
    """Release one claim; the marker only goes away when the last one ends."""
    global _interactive_depth
    with _interactive_lock:
        _interactive_depth = max(0, _interactive_depth - 1)
        if _interactive_depth == 0:
            try:
                INTERACTIVE_MARKER.unlink()
            except OSError:
                pass


def interactive_active(window: int = INTERACTIVE_MAX_AGE) -> bool:
    try:
        return (INTERACTIVE_MARKER.is_file()
                and (time.time() - INTERACTIVE_MARKER.stat().st_mtime) < window)
    except OSError:
        return False


class interactive:  # noqa: N801 - used as a context manager, reads as one
    """with interactive(): ... — hold the batch off while generating."""

    def __enter__(self):
        mark_interactive()
        return self

    def __exit__(self, *exc):
        clear_interactive()
        return False


def kick(kind: str, name: str, subtitle: str = "", snippet: str = "") -> bool:
    """Start a background generation if we can. True = one is now running.

    Used by the image route: the caller gets a 404 and its letter tile, and the
    picture is ready for the next request. Never raises.
    """
    if kind not in KINDS or have(kind, name):
        return False
    if bulk_running():
        return False                   # the bulk job is filling the library right now
    key = f"{kind}/{name}"
    with _LOCK:
        if key in _INFLIGHT:
            return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False                      # no loop (CLI) — the batch script is the caller
    loop.create_task(generate(kind, name, subtitle, snippet))
    return True


def stats(kinds: list[str] | None = None) -> dict:
    """How many images exist per kind. Cheap: directory listing, no indexing."""
    out = {}
    for kind in (kinds or list(KINDS)):
        d = ROOT / kind
        n = len([f for f in d.glob("*.webp")]) if d.is_dir() else 0
        try:
            mb = sum(f.stat().st_size for f in d.glob("*.webp")) / 1024 / 1024
        except OSError:
            mb = 0.0
        out[kind] = {"images": n, "mb": round(mb, 1)}
    return out
