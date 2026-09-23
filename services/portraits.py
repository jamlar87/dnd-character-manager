"""Portrait prompts and image generation, shared by the web route and scripts.

Lives outside routes/characters/ai_routes.py so a batch job can build the same
prompts and call the same provider without importing the whole app (that route
module imports main, which builds the entity index and static assets at import
time).

The web path keeps thin aliases in ai_routes, so there is still exactly ONE
prompt builder and ONE provider implementation.
"""

from __future__ import annotations
import os
import random
import base64
import re
import urllib.parse

import httpx

# Pollinations needs no key, no account and no card; it is the default. The
# OpenRouter path stays available (PORTRAIT_PROVIDER=openrouter) for when that
# key has budget — it had a $0.30 cap that was fully spent, which is why every
# portrait silently failed before this existed.
PORTRAIT_PROVIDER = os.environ.get("PORTRAIT_PROVIDER", "pollinations").strip().lower()


def npc_prompt(name: str, notes: str = "", race: str = "", role: str = "") -> str:
    """Prompt for a row with no race/class (DM NPCs) — name and notes only."""
    who = name or "a mysterious figure"
    bits = [b for b in (race, role) if b]
    detail = " ".join(bits)
    summary = " ".join((notes or "").split())[:300]
    tail = f" {summary}" if summary else ""
    return ("Bust portrait, 3:4 aspect ratio. " + who
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


async def fetch_pollinations_image(prompt: str, max_wait: int = 90,
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


async def generate_portrait_image(prompt: str, max_wait: float = 90,
                                  width: int = 768, height: int = 1024):
    """Ask the configured provider for one image; returns (data_url, error)."""
    from services.images import normalize_portrait
    if PORTRAIT_PROVIDER == "openrouter":
        raw = await fetch_openrouter_image(prompt, max_wait=90)
        error = None if raw else "OpenRouter returned no image (check credit/limits)"
    else:
        raw, error = await fetch_pollinations_image(prompt, max_wait=max_wait,
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
        ("Dwarf","Barbarian"): "Bust portrait, 3:4 aspect ratio. A stout mountain dwarf barbarian with thick braided auburn hair and a scarred face. Bare-chested with a heavy fur mantle across broad shoulders, tribal tattoos visible on muscular upper arms. Gripping a massive greataxe, battle-ready expression, primal fury in eyes. Snow-capped peaks and storm clouds behind. Oil painting, dramatic lighting, high fantasy.",
        ("Dwarf","Cleric"): "Bust portrait, 3:4 aspect ratio. A venerable dwarf cleric with a silver-streaked beard and kind eyes. Robes of deep blue with gold embroidery across the chest, a holy symbol of Moradin hanging from a chain. One hand raised in blessing near the face, warm candlelight glow. Stone temple interior. Oil painting, high fantasy.",
        ("Dwarf","Fighter"): "Bust portrait, 3:4 aspect ratio. A battle-hardened dwarf fighter in chainmail armor visible across shoulders and chest, warhammer resting on one shoulder. Braided copper beard, stern expression, scar across one eyebrow. Mountain fortress stonework behind. Oil painting, dramatic lighting.",
        ("Elf","Wizard"): "Bust portrait, 3:4 aspect ratio. A slender high elf wizard with silver-white hair flowing past pointed ears. Deep purple robes with arcane sigils at the collar, a crystal-topped staff held diagonally across the frame. Faint magical aura, ancient library backdrop. Oil painting, ethereal lighting.",
        ("Elf","Ranger"): "Bust portrait, 3:4 aspect ratio. A wood elf ranger with amber eyes and copper hair pulled back. Leather armor in forest greens across shoulders, longbow visible over one shoulder. Hood partially up, alert expression, dappled sunlight. Ancient forest bokeh behind. Oil painting, high fantasy.",
        ("Elf","Rogue"): "Bust portrait, 3:4 aspect ratio. A sleek elven rogue with dark cropped hair and a knowing smirk. Black leather armor, dagger hilts visible at the collar, cloak half-drawn across shoulders. Moonlit shadows dancing across the face. Oil painting, chiaroscuro lighting.",
        ("Human","Paladin"): "Bust portrait, 3:4 aspect ratio. A noble human paladin with short-cropped blonde hair and a strong jaw. Gleaming plate armor with a sunburst emblem on the chestplate, longsword held near the face reflecting divine light. Radiant glow from behind. Oil painting, cinematic lighting.",
        ("Human","Fighter"): "Bust portrait, 3:4 aspect ratio. A weathered human fighter with close-cropped dark hair and a faint scar across the cheek. Well-worn scale mail across shoulders, longsword hilt visible at hip-level frame edge. Castle wall stonework behind. Oil painting, late afternoon light.",
        ("Half-Orc","Barbarian"): "Bust portrait, 3:4 aspect ratio. A towering half-orc barbarian with gray-green skin and tribal tattoos across the face and shoulders. Bald head, tusked jaw, fur mantle over bare chest. Massive axe head visible, primal snarl. Stormy sky background. Oil painting, dramatic lighting.",
        ("Tiefling","Warlock"): "Bust portrait, 3:4 aspect ratio. A tiefling warlock with deep purple skin and curved horns sweeping back from the forehead. Eyes glowing with eldritch fire, dark robes with infernal patterns at the collar. A crackling tome held near the chest. Shadowy ruins at midnight. Oil painting, occult atmosphere.",
        ("Dragonborn","Paladin"): "Bust portrait, 3:4 aspect ratio. A bronze dragonborn paladin with gleaming metallic scales and a prominent draconic snout. Plate armor with a dragon emblem across the chest, sparks of lightning crackling between teeth. Holy symbol grasped near the chest, righteous intensity. Stormlit cathedral behind. Oil painting, dramatic lighting.",
        ("Dragonborn","Sorcerer"): "Bust portrait, 3:4 aspect ratio. A red dragonborn sorcerer with crimson scales and draconic frills framing the face. Robes shimmering with arcane heat, eyes glowing with inner fire. Hands wreathed in flame near the chest. Volcanic glow behind. Oil painting, high fantasy.",
    }
    key = (race, class_name)
    if key in prompts:
        return prompts[key]
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
    return f"Bust portrait, 3:4 aspect ratio. A {race.lower()} {class_name.lower()} with {rf}. Wearing appropriate {class_name.lower()} attire, upper body visible. Confident expression, high fantasy oil painting style with dramatic lighting. Rich colors, detailed background bokeh."
