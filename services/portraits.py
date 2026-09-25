"""Portrait prompts and image generation, shared by the web route and scripts.

Lives outside routes/characters/ai_routes.py so a batch job can build the same
prompts and call the same provider without importing the whole app (that route
module imports main, which builds the entity index and static assets at import
time).

The web path keeps thin aliases in ai_routes, so there is still exactly ONE
prompt builder and ONE provider implementation.
"""

from __future__ import annotations
import asyncio
import os
import random
import base64
import re
import urllib.parse
from pathlib import Path

import httpx

# ── Load .env (local config, not committed) ──────────────────────
# The web app does this in main.py, but a batch script never imports main (that module builds the
# entity index at import time), so a key placed in .env reached the app and not the generator. This
# module is the one thing both halves import, so the load lives here instead. setdefault, not
# assignment: a real environment variable must still beat the file.
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
# ─────────────────────────────────────────────────────────────────

# Providers, in the order the default tries them.
#
# Stable Horde is the default: keyless, no account, no card, and — unlike Pollinations — it does not
# answer instant 429s. A bare curl to Pollinations returned 429 in 0.4s while every request from a
# bulk run was refused, which is what forced the move. Horde is crowdsourced, so the anonymous key
# sits in a slow queue (measured ~3.5 min for one image) and a free registered key is much faster.
#
# Pollinations stays as the fallback, OpenRouter (paid, effectively spent) only when named.
HORDE_ASYNC = "https://stablehorde.net/api/v2/generate/async"
HORDE_STATUS = "https://stablehorde.net/api/v2/generate/status"
HORDE_AGENT = "dnd-character-manager:1.0:characters.jamlarnet.stream"
#: AlbedoBase XL rather than "SDXL 1.0", for worker pool size rather than style: measured from
#: /api/v2/status/models at the time of the switch, SDXL 1.0 had 2 workers against AlbedoBase XL 3.1's
#: 7. Queue priority is driven by kudos, and a 0-kudos account (anonymous included) queues last, so
#: pool size was the only lever available. Both are SDXL-class and fantasy-tuned, so the look is close.
#: Deliberately not an NSFW/illustrious fine-tune — this is a family fantasy reference library.
HORDE_MODELS = ("AlbedoBase XL 3.1",)

# ── House style ───────────────────────────────────────────────────────────────────────────────────
#: The house style, applied to EVERY prompt from every path.
#:
#: IT MUST BE SHORT. SDXL's CLIP encoders truncate at 77 tokens, and ComfyUI does not chunk — whatever
#: falls past that point is silently discarded before generation. With a long style tail the subject
#: description is what gets cut, so the model receives a style instruction and no subject and simply
#: invents one: a construct came back as a naked figure, a vessel as empty terrain. Every prompt here
#: is budgeted by test_prompt_token_budget.py, which is what keeps this from regressing.
FANTASY_STYLE = "high fantasy painting"
#: "blank" is kept in the wording on purpose: this is the user's own requirement, stated verbatim as
#: "always set on a 'Blank' background", and a test pins it. It costs one word and the budget holds.
BLANK_BG = "plain blank background, no scenery, no people, no text"


#: Scenery phrases that live inside the 11 curated character prompts. Each one names a SETTING, which
#: is the same instruction that turned a dwarf barbarian into a figure in a desert — leaving them in
#: while appending "no scenery" gives the model two opposite orders and it picks whichever it likes.
#: They are listed exactly rather than matched by keyword so nothing well-written is stripped by
#: accident, and test_prompt_house_style.py asserts the list stays complete.
DEAD_SCENERY = (
    "Snow-capped peaks and storm clouds behind.",
    "Stone temple interior.",
    "Mountain fortress stonework behind.",
    "ancient library backdrop",
    "Ancient forest bokeh behind.",
    "Castle wall stonework behind.",
    "Stormy sky background.",
    "Shadowy ruins at midnight.",
    "Stormlit cathedral behind.",
    "Volcanic glow behind.",
    "Radiant glow from behind.",
    # Not scenery, but the same defect: these duplicate the house style that gets appended afterwards.
    # Leaving them costs tokens twice over — once here and once in the style tail — and the prompt
    # budget is the whole reason this list exists.
    "high fantasy oil painting style with dramatic lighting.",
    "High fantasy oil painting, dramatic lighting, detailed face.",
    "Faint magical aura,",
)


def house_style(prompt: str) -> str:
    """Force any prompt into the house style: fantasy-coded, on a blank background.

    Strips the old backdrop-requesting phrases first rather than appending after them — leaving
    "detailed background bokeh" or "Snow-capped peaks behind" in place and adding "blank background"
    gives the model two contradictory instructions, and it resolves that by picking whichever it likes.

    Idempotent: the reference NPC branch delegates to npc_prompt, which applies this too, so without
    the guard those prompts would carry the clause twice.
    """
    p = prompt or ""
    if FANTASY_STYLE in p and BLANK_BG in p:
        return p
    for dead in (("Rich colors, detailed background bokeh.", "detailed background bokeh",
                  "detailed background", "plain parchment background", "plain dark background",
                  # Removing the background phrase from the item prompts left "Single object centred
                  # on a," dangling, which reads as damage to a diffusion model.
                  "Single object centred on a")
                 + DEAD_SCENERY):
        p = p.replace(dead, "")
    return _tidy(p)


def _tidy(p: str) -> str:
    """Close the gaps the removals leave: ", ," and " ." read as damage to a diffusion model too."""
    p = " ".join((p or "").split())
    for _ in range(4):                       # a removal can expose another, e.g. ", , ,"
        p = (p.replace(", ,", ",").replace(" ,", ",").replace(",,", ",")
               .replace(" .", ".").replace("..", ".").replace("( ", "(")
               .replace(". ,", ". ").replace("..", ".")
               .replace(", on a,", ",").replace(" on a,", ",").replace("  ", " "))
    p = p.rstrip(" .,;:")
    return f"{p}. {FANTASY_STYLE} {BLANK_BG}"


HORDE_MODELS = ("AlbedoBase XL 3.1",)

# ── Local generation on the bazzite box (AMD RX 9060 XT, ROCm) ────────────────────────────────────
# ComfyUI in a podman container, reachable over the LAN. No queue, no rate limit, no watermark and
# no per-image cost — measured ~40s/image against 6-92 minutes from the crowdsourced queue, which is
# what makes the full ~1,500-image backfill tractable at all.
#
# Ask for it with PORTRAIT_PROVIDER=comfy. It is deliberately not the default: it depends on another
# machine being awake, and that machine is a gaming PC, so its GPU is not always ours to take.
COMFY_URL = os.environ.get("COMFY_URL", "http://192.168.1.31:8188")
#: An ILLUSTRATION model, not a photographic one. The first choice was
#: "Juggernaut-XL_v9_RunDiffusionPhoto_v2" — a photorealism checkpoint, picked from a recommendation
#: without checking what it was built for. It produced aerial terrain, a nude figure, a shaggy animal
#: and a dead fish for prompts about clockwork constructs: photographic subjects, because that is what
#: it is trained to make. DreamShaper XL is painterly/illustration, which is what a fantasy reference
#: library needs, and the -SFW build is the explicit safe-for-work variant — the photo model's
#: photoreal humans were also how inappropriate output kept appearing.
COMFY_CKPT = os.environ.get("COMFY_CKPT", "DreamShaperXL_Turbo_V2-SFW.safetensors")
#: Turbo (distilled) models are trained for very few steps at low CFG. Running one at the 28/6.5 that
#: a full SDXL wants burns 4x the time and produces the over-saturated, over-detailed mush the
#: distilled weights were never tuned for.
COMFY_STEPS = int(os.environ.get("COMFY_STEPS", "7"))
COMFY_CFG = float(os.environ.get("COMFY_CFG", "2.0"))
COMFY_SAMPLER = os.environ.get("COMFY_SAMPLER", "dpmpp_sde")
COMFY_SCHEDULER = os.environ.get("COMFY_SCHEDULER", "karras")
#: SDXL is trained near 1024px; 832x1216 is the standard 2:3 portrait bucket and far better than
#: 768x1024 for this family.
COMFY_NEGATIVE = ("photo, photorealistic, 3d render, modern clothing, cars, racing suit, "
                  "scenery, landscape, horizon, people, humans, portrait, "
                  "blurry, low quality, watermark, text, signature, deformed, extra limbs")

PORTRAIT_PROVIDER = os.environ.get("PORTRAIT_PROVIDER", "horde").strip().lower()


def npc_prompt(name: str, notes: str = "", race: str = "", role: str = "") -> str:
    """Prompt for a row with no race/class (DM NPCs) — name and notes only."""
    who = name or "a mysterious figure"
    bits = [b for b in (race, role) if b]
    detail = " ".join(bits)
    summary = " ".join((notes or "").split())[:300]
    tail = f" {summary}" if summary else ""
    return house_style(
        "Bust portrait, 3:4 aspect ratio. " + who
        + (f", {detail}," if detail else ",")
        + " upper body only, close-up composition. High fantasy oil painting,"
          " dramatic lighting, detailed face." + tail)


async def fetch_openrouter_image(prompt: str, max_wait: int = 120) -> str | None:
    """Generate image via OpenRouter (free image models). Returns base64 data URL or None."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("[IMG] No OPENROUTER_API_KEY set")
        return None

    model = "google/gemini-2.5-flash-image"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://characters.jamlarnet.stream",
                    "X-OpenRouter-Title": "D&D Character Manager",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": (
                        f"Generate a character portrait image based on this prompt. "
                        f"Return ONLY the image, no explanation text.\n\n{prompt[:1000]}"
                    )}],
                    "max_tokens": 1000,
                },
            )
            if resp.status_code != 200:
                err_body = resp.text[:300]
                if "limit exceeded" in err_body.lower() or "402" in err_body:
                    print("[IMG] OpenRouter daily limit reached — upgrade at https://openrouter.ai/settings/credits")
                else:
                    print(f"[IMG] OpenRouter image gen failed: {resp.status_code} {err_body}")
                return None

            data = resp.json()
            msg = data.get("choices", [{}])[0].get("message", {})
            images = msg.get("images", [])
            if images:
                url = images[0].get("image_url", {}).get("url", "")
                if url and url.startswith("data:"):
                    print(f"[IMG] OpenRouter image generated via {model}")
                    return url

            # Fallback: check content for markdown image URLs
            content = msg.get("content") or msg.get("reasoning") or ""
            md_urls = re.findall(r"https?://[^\s<>\"']+\.(?:png|jpg|jpeg|webp)", content)
            if md_urls:
                async with httpx.AsyncClient(timeout=30) as dl:
                    ir = await dl.get(md_urls[0])
                    ct = ir.headers.get("Content-Type", "image/png")
                    b64 = base64.b64encode(ir.content).decode()
                    print(f"[IMG] OpenRouter image from URL ({len(ir.content)} bytes)")
                    return f"data:{ct};base64,{b64}"

            print(f"[IMG] No image in response: {str(data)[:300]}")
            return None

    except Exception as e:
        print(f"[IMG] OpenRouter image error: {e}")
        return None


async def fetch_pollinations_image(prompt: str, max_wait: float = 90,
                                  width: int = 768, height: int = 1024,
                                  referrer: str | None = None):
    """Keyless image generation. Returns (data_url, error)."""
    quoted = urllib.parse.quote(prompt[:900], safe="")
    seed = random.randint(1, 2_000_000_000)
    # Documented as a hint to give an app better treatment than anonymous scrapes.
    ref = referrer if referrer is not None else os.environ.get(
        "POLLINATIONS_REFERRER", "https://characters.jamlarnet.stream")
    url = (f"https://image.pollinations.ai/prompt/{quoted}"
           f"?width={int(width)}&height={int(height)}&nologo=true&model=flux&seed={seed}"
           + (f"&referrer={urllib.parse.quote(ref, safe='')}" if ref else ""))
    try:
        async with httpx.AsyncClient(timeout=max_wait, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:
        return None, f"image service unreachable ({type(exc).__name__})"
    if resp.status_code == 429:
        return None, "image service is rate limiting — try again in a minute"
    if resp.status_code != 200:
        return None, f"image service returned HTTP {resp.status_code}"
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
    if not ctype.startswith("image/") or not resp.content:
        return None, "image service returned no picture"
    return "data:" + ctype + ";base64," + base64.b64encode(resp.content).decode(), None


async def fetch_horde_image(prompt: str, max_wait: int = 600,
                            width: int = 768, height: int = 1024,
                            poll_every: float = 6.0):
    """Keyless crowdsourced generation via Stable Horde. Returns (data_url, error).

    Submit is instant; the work happens on volunteers' GPUs, so this polls. The anonymous key is
    documented and needs no signup, but it queues behind everyone with kudos — a free registered key
    (STABLEHORDE_API_KEY) is the intended speed-up, and nothing about the flow changes when it is set.
    """
    key = (os.environ.get("STABLEHORDE_API_KEY", "") or "").strip() or "0000000000"
    headers = {"apikey": key, "Client-Agent": HORDE_AGENT}
    body = {
        "prompt": prompt[:1500],
        "params": {"width": int(width), "height": int(height), "steps": 20, "n": 1},
        "models": [os.environ.get("STABLEHORDE_MODEL", HORDE_MODELS[0])],
        "nsfw": False,
        "censor_nsfw": True,
    }
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            resp = await client.post(HORDE_ASYNC, json=body, headers=headers)
            # 202 Accepted is the success code here — a submit queues the job rather than doing it.
            # Requiring 200 rejected every real job while the mocked tests (which assumed 200) passed.
            if not 200 <= resp.status_code < 300:
                return None, f"Stable Horde rejected the job (HTTP {resp.status_code})"
            job = (resp.json() or {}).get("id")
            if not job:
                return None, "Stable Horde returned no job id"
            for _ in range(max(1, int(max_wait / poll_every))):
                await asyncio.sleep(poll_every)
                try:
                    poll = await client.get(f"{HORDE_STATUS}/{job}", headers=headers)
                except Exception:
                    continue                      # a dropped poll is not a failed job
                if not 200 <= poll.status_code < 300:
                    continue
                state = poll.json() or {}
                if state.get("faulted"):
                    return None, "Stable Horde could not fulfil the job"
                generations = state.get("generations") or []
                if not (state.get("done") and generations):
                    continue
                image_url = generations[0].get("img")
                if not image_url:
                    return None, "Stable Horde finished without an image"
                got = await client.get(image_url)
                if got.status_code != 200 or not got.content:
                    return None, "Stable Horde's image could not be downloaded"
                ctype = (got.headers.get("content-type") or "").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    ctype = "image/webp"
                return ("data:" + ctype + ";base64,"
                        + base64.b64encode(got.content).decode()), None
    except Exception as exc:
        return None, f"Stable Horde unreachable ({type(exc).__name__})"
    return None, f"Stable Horde did not finish within {int(max_wait)}s (the free queue is slow)"


def comfy_workflow(prompt: str, width: int, height: int, steps: int | None = None,
                   cfg: float | None = None, seed: int | None = None,
                   ckpt: str | None = None) -> dict:
    """The API-format SDXL txt2img graph ComfyUI's /prompt endpoint expects.

    Node ids are arbitrary strings; the graph is load checkpoint -> two text encodes (positive and
    negative) -> empty latent -> sampler -> VAE decode -> save. Kept as data rather than a saved
    workflow JSON file so a prompt can be injected without editing anything on the other machine.

    Sampling settings come from the module constants, so switching checkpoint families is a config
    change rather than an edit here: a turbo model at 28 steps/CFG 6.5 is slow AND wrong.
    """
    if seed is None:
        seed = random.randint(1, 2_000_000_000)
    return {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt or COMFY_CKPT}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt[:2000], "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": COMFY_NEGATIVE, "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": int(seed),
                         "steps": int(COMFY_STEPS if steps is None else steps),
                         "cfg": float(COMFY_CFG if cfg is None else cfg),
                         "sampler_name": COMFY_SAMPLER, "scheduler": COMFY_SCHEDULER, "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "dnd_ref", "images": ["8", 0]}},
    }


async def fetch_comfy_image(prompt: str, max_wait: int = 300,
                            width: int = 832, height: int = 1216,
                            poll_every: float = 2.0):
    """Local generation via a ComfyUI instance on the LAN. Returns (data_url, error).

    Submitting returns a prompt_id immediately; the work happens on the GPU, so this polls /history
    and then fetches the finished file from /view. Unlike Horde there is no queue to lose a place
    in, so the only failure modes are the machine being down or the checkpoint missing.
    """
    base = COMFY_URL.rstrip("/")
    client_id = f"dnd-character-manager-{os.getpid()}"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.post(f"{base}/prompt",
                                     json={"prompt": comfy_workflow(prompt, width, height),
                                           "client_id": client_id})
            if not 200 <= resp.status_code < 300:
                # ComfyUI reports a bad graph as 400 with the reason in the body; surface it, because
                # "validation failed" on its own says nothing about which node was wrong.
                return None, (f"ComfyUI rejected the workflow (HTTP {resp.status_code}): "
                              f"{resp.text[:300]}")
            job = (resp.json() or {}).get("prompt_id")
            if not job:
                return None, "ComfyUI returned no prompt id"
            for _ in range(max(1, int(max_wait / poll_every))):
                await asyncio.sleep(poll_every)
                try:
                    hist = await client.get(f"{base}/history/{job}")
                except Exception:
                    continue                      # a dropped poll is not a failed job
                if not 200 <= hist.status_code < 300:
                    continue
                entry = (hist.json() or {}).get(job)
                if not entry:
                    continue                      # still queued or running
                status = (entry.get("status") or {})
                if status.get("status_str") == "error":
                    return None, f"ComfyUI failed to generate: {str(status)[:300]}"
                images = ((entry.get("outputs") or {}).get("9") or {}).get("images") or []
                if not images:
                    continue
                img = images[0]
                got = await client.get(f"{base}/view", params={
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                })
                if got.status_code != 200 or not got.content:
                    return None, "ComfyUI's image could not be downloaded"
                ctype = (got.headers.get("content-type") or "").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    ctype = "image/png"
                return ("data:" + ctype + ";base64,"
                        + base64.b64encode(got.content).decode()), None
    except Exception as exc:
        return None, f"ComfyUI unreachable at {base} ({type(exc).__name__})"
    return None, f"ComfyUI did not finish within {int(max_wait)}s"


async def generate_portrait_image(prompt: str, max_wait: float = 90,
                                  width: int = 768, height: int = 1024):
    """Ask the configured provider for one image; returns (data_url, error)."""
    from services.images import normalize_portrait
    if PORTRAIT_PROVIDER == "openrouter":
        raw = await fetch_openrouter_image(prompt, max_wait=90)
        error = None if raw else "OpenRouter returned no image (check credit/limits)"
    elif PORTRAIT_PROVIDER == "pollinations":
        raw, error = await fetch_pollinations_image(prompt, max_wait=max_wait,
                                                    width=width, height=height)
    elif PORTRAIT_PROVIDER == "comfy":
        # Local GPU on the LAN. SDXL-native 2:3 portrait bucket rather than the 768x1024 the hosted
        # providers use.
        raw, error = await fetch_comfy_image(prompt, max_wait=max(int(max_wait), 300),
                                             width=832, height=1216)
    else:
        # Horde is the default, so give it a real queue window rather than a per-request timeout.
        #
        # There is deliberately no automatic fallback. Pollinations stamps a pollinations.ai
        # watermark even when asked not to (nologo=true), and watermarked reference art is worse
        # than no art — it would appear silently in the library and be hard to notice among 5,700
        # records. Ask for it by name (PORTRAIT_PROVIDER=pollinations) if its queue is ever the
        # smaller problem than Horde's.
        raw, error = await fetch_horde_image(prompt, max_wait=max(int(max_wait), 600),
                                             width=width, height=height)
    if error or not raw:
        return None, error or "no image returned"
    raw, err = normalize_portrait(raw, max_px=1024)
    if err or not raw:
        return None, err or "the generated image was unusable"
    return raw, None


def genderize(prompt: str, gender: str) -> str:
    """Name the subject's gender in the framing clause.

    The wizard knows the gender of the name it generated and used to throw it
    away (`gender` was read by the endpoint and never used), so a female
    character could come back bearded. Blank or unrecognised leaves the prompt
    alone — never guess at this.
    """
    g = (gender or "").strip().lower()
    if g not in {"male", "female"}:
        return prompt
    marker = "Bust portrait, 3:4 aspect ratio."
    if marker in prompt:
        return prompt.replace(marker, f"Bust portrait, 3:4 aspect ratio, {g} subject.", 1)
    return f"{prompt} The subject is {g}."


def portrait_prompt(race: str, class_name: str, subclass: str = "") -> str:
    """Deterministic portrait prompts by class/race — all bust/upper-body framed."""
    prompts = {
        ("Dwarf","Barbarian"): "Bust portrait, 3:4 aspect ratio. A stout mountain dwarf barbarian, thick braided auburn hair, scarred face, bare-chested under a heavy fur mantle, tribal tattoos on muscular arms, gripping a greataxe, battle-ready.",
        ("Dwarf","Cleric"): "Bust portrait, 3:4 aspect ratio. A venerable dwarf cleric, silver-streaked beard, kind eyes, deep blue robes with gold embroidery, a holy symbol of Moradin on a chain, one hand raised in blessing, candlelight.",
        ("Dwarf","Fighter"): "Bust portrait, 3:4 aspect ratio. A battle-hardened dwarf fighter in chainmail, a warhammer resting on one shoulder, braided copper beard, stern expression, a scar across one eyebrow.",
        ("Elf","Wizard"): "Bust portrait, 3:4 aspect ratio. A slender high elf wizard, silver-white hair flowing past pointed ears, deep purple robes with arcane sigils at the collar, a crystal-topped staff held diagonally across the frame.",
        ("Elf","Ranger"): "Bust portrait, 3:4 aspect ratio. A wood elf ranger, amber eyes, copper hair pulled back, green leather armor, a longbow over one shoulder, hood half up, alert expression, dappled light.",
        ("Elf","Rogue"): "Bust portrait, 3:4 aspect ratio. A sleek elven rogue, dark cropped hair, knowing smirk, black leather armor, dagger hilts at the collar, a cloak half-drawn across the shoulders, moonlit shadow.",
        ("Human","Paladin"): "Bust portrait, 3:4 aspect ratio. A noble human paladin, short-cropped blond hair, strong jaw, gleaming plate armor with a sunburst emblem on the chest, a longsword raised, radiant light.",
        ("Human","Fighter"): "Bust portrait, 3:4 aspect ratio. A weathered human fighter, close-cropped dark hair, a faint scar across the cheek, well-worn scale mail across the shoulders, a longsword hilt visible.",
        ("Half-Orc","Barbarian"): "Bust portrait, 3:4 aspect ratio. A towering half-orc barbarian, gray-green skin, tribal tattoos across face and shoulders, bald, tusked jaw, a fur mantle over a bare chest, a massive axe head visible.",
        ("Tiefling","Warlock"): "Bust portrait, 3:4 aspect ratio. A tiefling warlock, deep purple skin, curved horns sweeping back, eyes glowing with eldritch fire, dark robes with infernal patterns, a crackling tome held at the chest.",
        ("Dragonborn","Paladin"): "Bust portrait, 3:4 aspect ratio. A bronze dragonborn paladin, gleaming metallic scales, draconic snout, plate armor with a dragon emblem, sparks crackling between the teeth, a holy symbol grasped at the chest.",
        ("Dragonborn","Sorcerer"): "Bust portrait, 3:4 aspect ratio. A red dragonborn sorcerer, crimson scales, draconic frills framing the face, robes shimmering with arcane heat, eyes glowing, hands wreathed in flame.",
    }
    key = (race, class_name)
    if key in prompts:
        return house_style(prompts[key])
    # Generic fallback with comprehensive race features
    race_features = {
        "Dwarf": "stout build, braided hair or beard, rugged dwarven features",
        "Elf": "slender build, pointed ears, graceful elven features, sharp cheekbones",
        "Human": "determined expression, practical demeanor, varied appearance",
        "Half-Orc": "tusked jaw, muscular build, gray-green skin, prominent lower canines",
        "Halfling": "small stature, curly hair, cheerful round face, pointed ears",
        "Gnome": "small and bright-eyed, clever expression, prominent nose, delicate features",
        "Tiefling": "curved horns, pointed tail, violet or red skin tones, solid-color eyes, otherworldly presence",
        "Dragonborn": "draconic snout and frills, metallic or chromatic scales, reptilian eyes, clawed hands",
        "Half-Elf": "slightly pointed ears, mixed heritage beauty, human versatility with elven grace",
        "Aarakocra": "avian features, beak-like nose, feathery crest, large expressive bird-like eyes, taloned hands",
        "Aasimar": "angelic radiance, luminous eyes, metallic-flecked skin, faint glowing halo effect, celestial beauty",
        "Bugbear": "shaggy dark fur, long goblinoid ears, heavy brow, powerful long-limbed build, bestial features",
        "Centaur": "equine lower body suggested, human torso with strong build, wild flowing hair, nature-worn features",
        "Changeling": "pale skin, colorless white hair, large colorless eyes, subtly shifting features, androgynous",
        "Firbolg": "towering broad build, gray-blue skin, pointed ears, wide bovine nose, gentle giant features",
        "Genasi": "elemental features — skin and hair tinted with elemental colors, faint elemental energy, striking eyes",
        "Gith": "gaunt elongated features, yellow-green skin, sharp angular face, deep-set piercing eyes, thin build",
        "Goblin": "small and wiry, green skin, large pointed ears, sharp jagged teeth, cunning wide eyes",
        "Goliath": "towering muscular build, gray stone-like skin, lithoderms (bony growths) visible, bald or short dark hair, intense gaze",
        "Grung": "small frog-like build, bright colorful skin (orange/green/blue), large bulbous eyes, webbed hands",
        "Hobgoblin": "orange-red skin, broad muscular build, sharp goblinoid features, military bearing, dark swept-back hair",
        "Kalashtar": "human-like with subtle ethereal quality, slightly luminous eyes, serene composed expression, psychic presence",
        "Kenku": "raven-like features, glossy black feathers, beak-like face, dark avian eyes, slight hunched posture",
        "Kobold": "small reptilian build, scaly skin in earthy tones, draconic snout, large expressive eyes, small horns",
        "Lizardfolk": "reptilian scales in green-brown tones, elongated snout, slit-pupil eyes, crest or spines, muscular jaw",
        "Loxodon": "elephantine features, gray wrinkled skin, prominent tusks, large floppy ears, trunk, wise deep-set eyes",
        "Minotaur": "bull-like features, large curved horns, bovine snout, thick neck, muscular bullish build, dark fur",
        "Orc": "powerful muscular build, gray-green skin, prominent tusks, heavy brow, pig-like snout, battle scars",
        "Shifter": "humanoid with bestial features — feline eyes, pointed ears, subtle fur patches, predatory grace",
        "Simic Hybrid": "humanoid with grafted animal features — gills, tentacles, carapace plates, or fin-crests",
        "Tabaxi": "feline features, cat-like vertical-pupil eyes, fur in leopard/jaguar/tiger patterns, whiskers, pointed ears, tail",
        "Tlincalli": "scorpion-like features, chitinous plates, mandibles, multiple eyes, segmented frame, striking silhouette",
        "Tortle": "turtle-like features, domed shell visible behind shoulders, beaked mouth, leathery green-brown skin, wise ancient eyes",
        "Triton": "aquatic features, blue-green skin, fin-like ears, webbed hands, iridescent scales, flowing sea-colored hair",
        "Vedalken": "tall and slender, blue-gray skin, completely hairless, large analytical eyes, elongated smooth head",
        "Warforged": "constructed living armor of wood and metal, hinged jaw, glowing eyes, rune-etched plating, golem-like features",
        "Xvart": "small, bright blue skin, large bat-like ears, bulging eyes, hunched posture, sharp teeth",
        "Yuan-ti Pureblood": "human-like with serpentine features — slit-pupil eyes, small scales, forked tongue, cold calculating gaze",
    }
    rf = race_features.get(race, "distinctive features, adventurer's bearing")
    # "Wearing appropriate X attire, upper body visible" said nothing the framing and the class name had
    # not already said, and the style tail was appended a second time by house_style(). Every wasted word
    # here is a word of the SUBJECT pushed past CLIP's 77-token window, where it is silently dropped.
    return house_style(
        f"Bust portrait, 3:4 aspect ratio. A {race.lower()} {class_name.lower()} with {rf}. "
        "Confident expression.")
