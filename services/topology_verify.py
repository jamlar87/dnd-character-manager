"""Generate-then-verify: resample a portrait until a vision model confirms the clause's topology.

Why this exists. The 2026-11-18 experiments (see services/limb_placement.py) showed the prompt is
not a working control surface for exotic anatomy: the same Xorn clause rendered 2 legs / 2 arms /
2 eyes on one roll and 3 legs / 3 eyes on the next. Wording was exhausted; the variance is real.
So instead of trying to *say* the anatomy, we *check* it: roll N candidates, ask a vision model
to count, keep the one that matches the manual.

The vision model lives on the ComfyUI host (bazzite, 17 GB VRAM) under ollama, same box that
renders the image, so nothing new has to be installed on the app host. Both hold VRAM at different
times: ComfyUI renders the batch first, then the VLM loads once and scores the whole batch. Loading
per image would thrash the GPU and cost more than the render.

Counting accuracy was measured on 2026-11-18 against portraits whose limb counts were settled by
eye, because "the small VLM looks fine" is exactly the assumption that produces silent bad gates:

  qwen2.5vl:7b  unusable. Ignored the image on direct count questions (identical answers for two
                different Xorn renders) and agreed with whatever the prompt asserted.
  qwen3-vl:8b   reads the picture — it found the third eye, the horse's four legs and zero arms —
                but undercounts crowded limbs. ~5-6 s per image once resident. Default backend.
  gemini-2.5-flash via freellmapi  fast (~1.8 s) and free, right on the easy cases, but it invented
                eye counts on a batch of four fresh Xorn renders (5, 7, 6, 5 on images that hold
                three) while the local model stayed at 2-4. Kept as fallback only, never primary.

The sobering result from best-of-4 on a three-legged clause: every roll came back bipedal, and the
one good candidate already on disk (three splayed legs, three eyes) was counted as "2 legs" by
every model tried. So this module cannot manufacture anatomy the diffusion model will not draw, and
it can reject the closest match. It is an audit aid — "no candidate looks wrong" — not a gate that
should be allowed to overwrite a human-approved portrait.

Thinking models spend num_predict on invisible reasoning, so the budget below is generous; a tight
cap returns an empty answer and looks like a tooling failure.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import ref_portraits

VLM_URL = os.environ.get("VLM_URL", "http://192.168.1.31:11434").rstrip("/")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen3-vl:8b")
VLM_TIMEOUT = float(os.environ.get("VLM_TIMEOUT", "180"))

#: Free, read-only aggregator already used as the primary text route; key comes from config so a
#: key rotation does not silently degrade the verifier to the weaker local model.
FREELLMAPI_URL = os.environ.get("FREELLMAPI_URL", "http://127.0.0.1:3010/v1").rstrip("/")
FREELLMAPI_KEY = os.environ.get("FREELLMAPI_KEY", "")
FREELLMAPI_MODEL = os.environ.get("FREELLMAPI_VLM_MODEL", "gemini-2.5-flash")
#: Local GPU model first: it stayed sane on fresh renders where the cloud fallback invented counts.
VLM_ORDER = [p.strip() for p in os.environ.get("VLM_ORDER", "ollama,freellmapi").split(",") if p.strip()]

#: Noun -> key the VLM is asked to count. Order matters only for prompt wording.
COUNT_NOUNS = {
    "leg": "legs",
    "arm": "arms",
    "eye": "eyes",
    "head": "heads",
    "wing": "wings",
    "tentacle": "tentacles",
    "eyestalk": "eyestalks",
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "single": 1, "pair": 2,
    "twin": 2, "triple": 3, "quadruple": 4,
}

#: Clauses write anatomy as compounds far more often than as counts: "three-legged", "two-headed",
#: "six-armed". Splitting on the hyphen turns those into a noun the table does not know ("legged"),
#: which is why the first version of this parser found counts in 0 of the creatures that matter.
_STEM_RE = re.compile(r"^(leg|arm|eye|head|wing|tentacle|eyestalk)(s|es|ed|ged|gged)?$")

#: A count next to one of these is not a count of what is drawn: "one big central eye and *many*
#: eyestalks" fixes the eye at one while the visible eye count is whatever the stalks add up to.
#: Treating 1 as the expectation scored every correct beholder as a two-eye failure.
_VAGUE_QTY = {"many", "several", "numerous", "countless", "dozens", "multiple", "myriad",
              "scores", "row", "rows", "multitude", "masses"}

#: Parts whose drawn count is shared: a vague quantity on either makes both uncountable.
_SHARED_COUNT = {"eyes": {"eyes", "eyestalks"}, "eyestalks": {"eyes", "eyestalks"}}


def _stem(word: str) -> str | None:
    m = _STEM_RE.match(word)
    return m.group(1) if m else None


def expected_from_clause(clause: str) -> dict[str, int]:
    """Pull countable anatomy out of a clause: "three clawed stone arms" -> {"arms": 3}.

    Deliberately conservative. Only a number word or digit within three tokens before the noun
    counts, so "four arms and two heads" yields both while prose like "eyes that never blink"
    yields nothing — guessing there would make the verifier reject good art.
    """
    words = re.findall(r"[a-z]+|\d+", clause.lower().replace("-", " ").replace("–", " "))
    out: dict[str, int] = {}
    vague: set[str] = set()
    for i, w in enumerate(words):
        stem = _stem(w)
        if not stem:
            continue
        key = COUNT_NOUNS[stem]
        window = words[max(0, i - 3):i]
        if any(v in _VAGUE_QTY for v in window):
            vague |= _SHARED_COUNT.get(key, {key})
            continue
        for back in (1, 2, 3):
            if i - back < 0:
                break
            prev = words[i - back]
            n = NUMBER_WORDS.get(prev)
            if n is None:
                m = re.match(r"\d+", prev)
                n = int(m.group()) if m else None
            if n:
                out[key] = n
                break
    for key in vague:            # a vague quantity wins over any fixed number in the same clause
        out.pop(key, None)
    return out


def _vlm_prompt(expect: dict[str, int], negatives: list[str]) -> str:
    parts = list(expect) + [k for k in negatives if k not in expect]
    lines = "\n".join(f"{k}: <number>" for k in parts)
    return (
        "Look at this creature illustration and count the parts that are actually drawn. "
        "Count every limb, including limbs raised off the ground; ignore background, style and colour. "
        "Answer with exactly these lines and nothing else, replacing <number> with how many you "
        "counted:\n" + lines
    )


def _post(path: str, payload: dict) -> urllib.request.Request:
    return urllib.request.Request(
        VLM_URL + path, json.dumps(payload).encode(),
        {"Content-Type": "application/json"})


def _freellmapi_key() -> str:
    """Env first, else the key Hermes already holds for the same local aggregator.

    The app has no reason to keep a second copy of a key that lives on this host anyway; if neither
    is present the backend is skipped rather than called unauthenticated.
    """
    if FREELLMAPI_KEY:
        return FREELLMAPI_KEY
    cfg = Path.home() / ".hermes" / "config.yaml"
    try:
        for i, line in enumerate(cfg.read_text().splitlines()):
            if line.strip() == "freellmapi:":
                for follow in cfg.read_text().splitlines()[i + 1:i + 8]:
                    m = re.match(r"\s+api_key:\s*(\S+)", follow)
                    if m:
                        return m.group(1).strip("'\"")
                break
    except OSError:
        pass
    return ""


def _ollama_counts(image_path: Path, prompt: str, keys: list[str]):
    img = base64.b64encode(Path(image_path).read_bytes()).decode()
    body = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": [img]}],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            # qwen3-vl spends part of this budget on hidden reasoning; 400 came back empty.
            "num_predict": int(os.environ.get("VLM_MAX_TOKENS", "1200")),
        },
    }
    try:
        with urllib.request.urlopen(_post("/api/chat", body), timeout=VLM_TIMEOUT) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:                       # noqa: PERF203 — small, explicit
        return None, f"ollama HTTP {e.code}: {e.read().decode()[:120]}"
    except Exception as e:                                    # noqa: BLE001 — any transport failure
        return None, f"ollama {type(e).__name__}: {e}"
    raw = ((data.get("message") or {}).get("content") or "").strip()
    return parse_counts(raw, keys) or None, raw.replace("\n", " ")[:120]


def _freellmapi_counts(image_path: Path, prompt: str, keys: list[str]):
    key = _freellmapi_key()
    if not key:
        return None, "freellmapi: no key"
    mime = "image/webp" if str(image_path).lower().endswith(".webp") else "image/png"
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    body = {
        "model": FREELLMAPI_MODEL,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
    }
    req = urllib.request.Request(
        FREELLMAPI_URL + "/chat/completions", json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=VLM_TIMEOUT) as r:
            data = json.load(r)
        raw = (data["choices"][0]["message"]["content"] or "").strip()
    except urllib.error.HTTPError as e:
        return None, f"freellmapi HTTP {e.code}: {e.read().decode()[:120]}"
    except Exception as e:                                    # noqa: BLE001
        return None, f"freellmapi {type(e).__name__}: {e}"
    return parse_counts(raw, keys) or None, raw.replace("\n", " ")[:120]


_BACKENDS = {"freellmapi": _freellmapi_counts, "ollama": _ollama_counts}


async def count_in_image(image_path: Path, expect: dict[str, int],
                         negatives: list[str] | None = None) -> tuple[dict[str, int] | None, str]:
    """Ask a vision model how many of each part it sees. Returns (counts, note/error).

    Backends are attempted in VLM_ORDER; the note records which one answered so a run can be audited
    later. The ollama path uses /api/chat, not /api/generate: on this build /api/generate accepted the
    `images` field and answered without ever looking at them — two different Xorn renders came back
    with the identical count in one second, which is a default answer, not a measurement.
    """
    negatives = negatives or []
    keys = list(expect) + [k for k in negatives if k not in expect]
    prompt = _vlm_prompt(expect, negatives)
    loop = asyncio.get_running_loop()
    notes: list[str] = []
    for name in VLM_ORDER:
        fn = _BACKENDS.get(name)
        if not fn:
            notes.append(f"{name}: unknown backend")
            continue
        counts, note = await loop.run_in_executor(None, fn, image_path, prompt, keys)
        if counts:
            return counts, f"[{name}] {note}"
        notes.append(note)
    return None, "; ".join(notes)[:300]


def parse_counts(raw: str, keys: list[str]) -> dict[str, int]:
    """Read "legs: 3" lines (also "legs - 3", "**legs**: 3", JSON blobs) out of a VLM answer."""
    out: dict[str, int] = {}
    if raw.lstrip().startswith("{"):
        try:
            parsed = json.loads(raw)
            inner = parsed.get("counts") if isinstance(parsed.get("counts"), dict) else parsed
            for k, v in inner.items():
                try:
                    out[str(k).lower()] = int(v)
                except (TypeError, ValueError):
                    continue
            return out
        except json.JSONDecodeError:
            pass
    for key in keys:
        # Markdown decoration ("**arms**: 2") sits between the label and the number, so it is
        # stripped rather than woven into the pattern.
        m = re.search(rf"\b{key}\b\s*[:=\-–]?\s*(\d+)", raw.replace("*", "").replace("#", ""), re.I)
        if m:
            out[key] = int(m.group(1))
    return out


async def score_bytes(data: bytes, expect: dict[str, int], negatives: list[str] | None = None,
                      suffix: str = ".webp") -> int:
    """Score an installed portrait's bytes with the same instrument as the candidates.

    Separate from count_in_image because that one wants a path, and the "previous" portrait of a
    record exists only in memory here.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        got, _note = await count_in_image(tmp, expect, negatives)
        return score(got or {}, expect)
    finally:
        tmp.unlink(missing_ok=True)


def score(got: dict[str, int], expect: dict[str, int]) -> int:
    """Higher is better. Exact match on a wanted part is worth 10; a miss by one costs only 1 because
    the 8B model measured above is off by one on crowded limbs and would otherwise reject its own
    best candidate. A miss by more than one costs 8 per step — categorically wrong topology is what
    this gate exists to catch. Unrequested extras cost 6: a nine-legged drider is not a near miss.

    The single-step leniency is granted ONCE per image, not once per part. A Xorn drawn with two
    arms and two legs read as 3/3/3-with-two-limb-slip-ons and scored a pass, which is exactly the
    prior reasserting itself — every part drifting the same way is a wrong render, not a shy
    counter, so the second and later one-short reads are charged the full miss penalty.
    """
    total = 0
    slips_left = 1
    for k, want in expect.items():
        if k not in got:
            total -= 5                    # part not even reported
            continue
        d = abs(got[k] - want)
        if d == 0:
            total += 10
        elif d == 1 and slips_left:
            slips_left = 0
            total -= 1
        else:
            total -= 8 * d
    for k, have in got.items():
        if k not in expect and have:
            total -= 6
    return total


async def generate_verified(kind: str, name: str, attempts: int = 4,
                            subtitle: str = "", snippet: str = "", **gen_kwargs):
    """Roll up to `attempts` candidates, keep the best match to the clause, install it.

    Returns (path, report). The report lists every attempt with its counts and score so a human can
    see what the verifier accepted — "trust me, it is the best of four" is not evidence.
    """
    clause = ref_portraits.creature_clause(name) if kind == "creature" else ""
    expect = expected_from_clause(clause)
    if not expect:
        path, err = await ref_portraits.generate(kind, name, subtitle, snippet, **gen_kwargs)
        return path, {"skipped": "no countable anatomy in clause", "error": err}

    negatives = [k for k in ("legs", "arms", "eyes", "heads") if k not in expect]
    dest = ref_portraits.path_for(kind, name)
    tmpdir = Path(tempfile.mkdtemp(prefix="topo-", dir=str(dest.parent)))
    report: dict = {"name": name, "expect": expect, "clause": clause, "attempts": []}
    # generate() writes straight to `dest`, so the portrait that existed before this run has to be
    # kept in memory: a batch whose every roll scores badly must not silently downgrade the art.
    original = dest.read_bytes() if dest.exists() else None
    best = (-10**6, None, None)
    # The bar cannot be an absolute score: the verifier undercounts crowded limbs (a correct 3-legged
    # render is routinely read as 2), so a fixed pass mark rejects every roll of the very creatures
    # this exists for. Score the portrait that is already installed with the same instrument and ask
    # only that the winner beat it - the undercount then lands on both sides and cancels out.
    baseline = None
    if original is not None:
        baseline = await score_bytes(original, expect, negatives, dest.suffix)
        report["baseline"] = baseline
    try:
        cands: list[Path] = []
        for i in range(attempts):
            p, err = await ref_portraits.generate(kind, name, subtitle, snippet,
                                                  force=True, **gen_kwargs)
            if not p:
                report["attempts"].append({"n": i, "error": err})
                continue
            c = tmpdir / f"{i}.webp"
            shutil.copy(p, c)          # generate() overwrites the live file; keep each roll
            cands.append(c)
        # One VLM session for the whole batch: the model stays resident, and the first call's load
        # is not paid per image.
        for i, c in enumerate(cands):
            got, note = await count_in_image(c, expect, negatives)
            s = score(got or {}, expect)
            report["attempts"].append({"n": i, "counts": got, "score": s, "note": note})
            if s > best[0]:
                best = (s, c, got)
        if best[1] and (best[0] >= 10 if baseline is None else best[0] > baseline):
            shutil.copy(best[1], dest)   # generate() already wrote the last roll; install the winner
            report["installed"] = {"score": best[0], "counts": best[2]}
        else:
            # Put the pre-run portrait back. The verifier undercounts ambiguous art, so "nothing
            # scored 10" often means "the VLM could not count it", not "the art is worse" — and
            # leaving the last roll live ships a downgrade on that guess.
            if original is not None:
                dest.write_bytes(original)
            report["installed"] = None
            report["why"] = ("no candidate met the clause; kept the previous portrait"
                             if original is not None else "no candidate met the clause")
        return dest, report
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
