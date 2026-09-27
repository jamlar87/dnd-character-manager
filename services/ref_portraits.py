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

#: kind -> (width, height) for the HOSTED providers. The local comfy path ignores these and asks for the
#: SDXL-native 2:3 bucket (832x1216, see services.portraits), which is what the whole shelf is: item,
#: creature and npc tiles are all 701x1024 on disk. Item used to be the odd one out at 768x768, so a
#: hosted redo would have dropped a square tile into a portrait shelf.
KINDS: dict[str, tuple[int, int]] = {
    "creature": (768, 1024),
    "npc": (768, 1024),
    "item": (768, 1024),
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
    if re.search(r"\b(?:token|charm|talisman|figurine)\b", name or "", re.I):
        # A swan-boat token is a small carved charm, not a boat: the vessel cue below matched the
        # word "Boat" in its name and drew a full masted ship where the record describes a feather.
        return None
    for pattern, description in CONSTRUCT_CUES:
        if pattern.search(name or ""):
            return description
    if re.search(r"\bconstruct\b|\bautomaton\b|\bmodron\b", detail or "", re.I):
        return CONSTRUCT_CUES[-1][1]
    return None


#: The construct problem again, one shelf over, and found the same way — by looking at the pictures.
#:
#: Item records carry a `snippet`, and for gear that snippet is frequently the RULES line rather than a
#: description of the object:
#:
#:     Chain Mail        "Heavy armor. AC 16. Requires STR 13, disadvantage on Stealth. 55 lb. 75 gp."
#:     Chain (10 feet)   "A chain has 10 hit points. It can be burst with a successful DC 20 Strength check."
#:     Crossbow bolt     "Ammunition. 1.5 lb. 1 gp."
#:     Crowbar           "Using a crowbar grants advantage to Strength checks where the crowbar's leverage…"
#:
#: Handed to the model as the visual tail, those produced — in a 48-tile art audit — plate armour for
#: Chain Mail ("Heavy armor"), a clockwork gear assembly for Chain ("burst … DC 20"), brass rifle
#: cartridges for Crossbow bolt ("Ammunition") and a curved pick head for Crowbar ("leverage"). Rules
#: text names dice, weights and saves; it never names the object's SHAPE, so the model free-fills from
#: the noun alone — and "crossbow" then reliably draws a bow.
#:
#: Two rules follow, and they are deliberately narrow:
#:   1. when a family cue matches, that cue IS the visual statement and the snippet is not appended
#:      (a rules line can only fight the cue);
#:   2. otherwise a snippet that reads as rules text is dropped rather than fed.
#: Neither rule invents anything: the cue states the ordinary shape of the object the record names.
ITEM_CUES = (
    # Named records whose own words fix the shape, and whose snippets name something else entirely.
    # Each one is here because a tile was measured wrong in an art audit, and each cue states only
    # what the record already says (its name, its type line or its description).
    (re.compile(r"\bring[- ]?mail\b", re.I),
          "a mail coat of thousands of interlocking silver rings"),
    (re.compile(r"\bbaubles?\b", re.I),
          "four glass globes hanging from a small brass fixture"),
    (re.compile(r"\bphilter\b", re.I),
          "a small glass vial of glowing liquid"),
    (re.compile(r"\bhoming tree\b", re.I),
          "a long carved wooden quarterstaff set with glowing embers"),
    (re.compile(r"\bspire of conflux\b", re.I),
          "a tall rune-carved wooden staff"),
    (re.compile(r"\barkenstone\b", re.I),
          "a large round faceted white gemstone"),
    (re.compile(r"\brain and thunder seed\b", re.I),
          "a single dark seed pod"),
    (re.compile(r"\bring of obscuring\b", re.I),
          "a single silver ring set with a smoky dark gem"),
    (re.compile(r"\bprosthetic limb\b", re.I),
          "a single articulated metal hand and forearm, one hand only"),
    (re.compile(r"\bspies'? murmurs?\b", re.I),
          "a small curved dark metal earpiece worn over one ear"),
    (re.compile(r"\bscroll of\b|\bspell scroll\b", re.I),
          "a rolled parchment scroll tied with a ribbon"),
    (re.compile(r"\bcase\b[^.]*\bbolt|bolt\s+case", re.I),
          "an open shallow wooden case holding a row of short bolts with small iron tips"),
    (re.compile(r"\bchain\s*mail\b|\bchainmail\b|\bhauberk\b", re.I),
          "a hauberk of thousands of interlocking iron rings"),
    (re.compile(r"\bchain shirt\b", re.I),
          "a sleeveless tunic of thousands of interlocking iron rings"),
    (re.compile(r"\belven chain\b|\bmithral (?:chain|shirt)\b", re.I),
          "a shirt of fine interlocking mithral rings, bright and light"),
    (re.compile(r"\bscale mail\b", re.I),
          "a coat of overlapping iron scales over leather"),
    (re.compile(r"\bmail\b", re.I),
          "a coat of interlocking iron rings"),
    (re.compile(r"\bchain\b", re.I),
          "a coiled length of heavy iron chain, large interlocking oval links"),
    (re.compile(r"\bcrossbow bolt\b|\bquarrel\b", re.I),
          "a short thin shaft with a small iron point and feather fletchings"),
    (re.compile(r"\bcrossbow\b", re.I),
          "a steel bow mounted crosswise on a wooden stock, a cord and a catch"),
    (re.compile(r"\bcrowbar\b|\bpry bar\b", re.I),
          "a straight iron bar with a flattened split claw at one end"),
    (re.compile(r"\bflail\b", re.I),
          "a handle with a chain ending in a spiked metal ball"),
    (re.compile(r"\bglaive\b", re.I),
          "a long wooden pole with one curved blade at the top"),
    (re.compile(r"\bdart\b", re.I),
          "a short weighted iron-tipped spike with feathers"),
    (re.compile(r"\bgrappling hook\b|\bgrapple\b", re.I),
          "barbed iron hooks on a shank tied to a rope"),
    (re.compile(r"\bclub\b", re.I),
          "a thick tapered wooden cudgel"),
    (re.compile(r"\bdagger\b|\bknife\b|\bscalpel\b", re.I),
          "a very short stubby blade with a small guard and a pommel"),
    (re.compile(r"\bdisguise\b", re.I),
          "an open wooden case of face paints, a false beard, a wig and brushes"),
    (re.compile(r"\bclimber'?s?\s+kit\b|\bpiton\b", re.I),
          "a coil of hemp rope, iron pitons, a small hammer and leather straps"),
    (re.compile(r"\bclothes\b|\btraveller'?s?\s+clothes\b|\btraveler'?s?\s+clothes\b", re.I),
          "a wool cloak, a linen tunic, leather boots and a belt"),
    (re.compile(r"\bchariot\b", re.I),
          "an empty two-wheeled wooden chariot with spoked wheels"),
    (re.compile(r"\bdonkey\b|\bmule\b", re.I),
          "a single grey donkey standing alone wearing a pack saddle"),
    (re.compile(r"\belephant\b", re.I),
          "a single elephant with tusks and a raised trunk"),
    (re.compile(r"\bgalley\b|\bkeelboat\b|\browboat\b|\blongship\b", re.I),
          "a long wooden ship with rows of oars, one mast and a square sail"),
    # ── The batch-01..10 residue. Same rule as above: every cue states only what the record already
    # says, and each is here because the auditor's tile showed the model reading the name some other
    # way. The families that repeat across the shelf lead; the one-offs follow.
    (re.compile(r"\bammunition\b|\barrows?\b|\bquarrels?\b|\bbolts?\b(?!\s*case)", re.I),
          "arrows: several arrows with wooden shafts, feather fletching and iron points"),
    (re.compile(r"\bsling bullet|\bbullets?\b", re.I),
          "a handful of small round lead sling bullets"),
    (re.compile(r"\bblowgun needle|\bneedle\b", re.I),
          "a thin slender steel needle with a small tuft at one end"),
    (re.compile(r"\bblowgun\b", re.I),
          "a long dark wooden blowpipe, a hollow tube to blow darts through"),
    (re.compile(r"\bbagpipes?\b", re.I),
          "a single set of bagpipes laid flat on a plain surface: an inflated tartan bag with three long drones and a chanter rising from it"),
    (re.compile(r"\bpipes? of\b", re.I),
          "a set of reed pipes: hollow reed tubes of different lengths bound with cord, a "
          "mouthpiece at one end, the object alone in frame"),
    (re.compile(r"\bhand drum\b|\blonghorn\b|\bhorn of\b|"
                r"\binstrument of the bards\b|\bwand of conducting\b", re.I),
          "a wooden musical instrument with binding, keys or a drum skin, made to be played"),
    (re.compile(r"\bpipe[- ]?weed\b|\bpipe\b(?!s)", re.I),
          "a carved wooden smoking pipe with a long stem and a small bowl"),
    (re.compile(r"\bbell\b", re.I),
          "a small bronze hand bell with a looped handle"),
    (re.compile(r"\blamp\b", re.I),
          "an iron oil lamp with a hinged lid and a burning wick"),
    (re.compile(r"\blantern\b", re.I),
          "a lantern of hinged iron and horn panes with a candle inside"),
    (re.compile(r"\btorch\b|\bcandle\b", re.I),
          "a wooden torch with a pitch-soaked burning head"),
    (re.compile(r"\bwaterskin\b|\bwineskin\b", re.I),
          "a bulging leather flask with a narrow stoppered neck and a wooden spout, hung from a shoulder strap"),
    (re.compile(r"\bsaddlebags?\b", re.I),
          "a pair of worn leather saddlebags with buckled flaps"),
    (re.compile(r"\bbackpack\b|\bpriest'?s pack\b|\bexplorer'?s pack\b|\bdungeoneer'?s pack\b", re.I),
          "a leather pack with straps, buckles and a bedroll tied on top"),
    (re.compile(r"\bbridles?\b|\bbit and bridle\b|\bhalters?\b", re.I),
          "a leather bridle with an iron bit and reins hanging on a plain peg, the tack shown alone"),
    (re.compile(r"\bhorseshoes?\b", re.I),
          "a set of four iron horseshoes with nail holes, the shoes alone in frame"),
    (re.compile(r"\bsaddles?\b|\btack\b", re.I),
          "a leather saddle with stirrups and girth straps"),
    (re.compile(r"\bwaters?kin\b|\bwater, fresh\b", re.I),
          "a leather waterskin and a wooden cup of clear water"),
    (re.compile(r"\bnet\b", re.I),
          "a wide rope mesh net with weighted edges, spread flat"),
    (re.compile(r"\blasso\b", re.I),
          "a coiled rope with a running loop at one end"),
    (re.compile(r"\bpistols?\b|\bmuskets?\b", re.I),
     "a wooden-stocked flintlock hand cannon with a brass barrel and a carved grip"),
    (re.compile(r"\bplate\b|\bhalf-plate\b|\bbreastplate\b", re.I),
     "an empty suit of riveted steel plate armour, isolated on a plain blank grey background in soft even painted light"),
    (re.compile(r"\bbelt of [a-z ]*giant strength\b", re.I),
     "a broad studded leather belt with a heavy metal buckle, coiled and empty"),
    (re.compile(r"\bthrower\b|\bwarhammer\b", re.I),
     "a warhammer with a heavy engraved steel head and a short haft, isolated on a plain blank grey background in soft even painted light"),
    (re.compile(r"\beyes of the eagle\b", re.I),
     "a pair of round brass-framed crystal lenses on a slim bronze frame, on a seamless grey studio background, soft even light"),
    (re.compile(r"\bspyglass\b", re.I),
          "a brass telescope of two sliding tubes with leather binding"),
    (re.compile(r"\bspectacles\b|\beyeglasses\b", re.I),
          "a pair of small round brass-rimmed spectacles"),
    (re.compile(r"\bsignal whistle\b|\bwhistle\b", re.I),
          "a small rounded brass body the size of a thumb with a flat mouthpiece and a tiny air window, on a short cord"),
    (re.compile(r"\bpick,? miner'?s\b|\bminer'?s pick\b", re.I),
          "a miner's pick laid flat on a plain surface: a wooden haft with one narrow iron spike head"),
    (re.compile(r"\bsledge\b|\bmaul\b|\bhammer\b", re.I),
          "a heavy iron hammer head on a wooden haft"),
    (re.compile(r"\bportable ram\b|\bram,? portable\b|\bbattering ram\b", re.I),
          "a heavy timber battering beam with an iron-shod ram head, hung by ropes in a frame"),
    (re.compile(r"\bmace\b", re.I),
          "a short hafted weapon with a flanged iron head"),
    (re.compile(r"\bgreat ?club\b|\bclub\b|\bcudgel\b", re.I),
          "a thick knotted wooden club"),
    (re.compile(r"\bquarterstaff\b|\bwalking stick\b|\bwooden staff\b", re.I),
          "a plain smooth wooden staff with two iron-shod ends, laid flat on a plain surface"),
    (re.compile(r"\bsickle\b|\bscythe\b", re.I),
          "a harvest sickle laid flat on a plain surface: one short curved steel blade on a short wooden grip"),
    (re.compile(r"\bpikes?\b", re.I),
          "a very long ash pike shaft taller than a man, a small steel head and a butt spike"),
    (re.compile(r"\btrident\b", re.I),
          "a trident polearm: a long ash haft with three sharp parallel steel prongs at the top"),
    (re.compile(r"\bwarhammer\b|\bbroadsword\b|\bbastard sword\b", re.I),
          "a heavy steel weapon with a hardwood haft or grip, forged metal only"),
    (re.compile(r"\bthree-dragon ante\b|\bdice\b|\bplaying cards?\b", re.I),
          "a set of painted pasteboard cards and carved bone dice on a cloth"),
    (re.compile(r"\bhide (?:armou?r|armor)\b|\bhide armour\b", re.I),
          "a suit of hide armour laid out flat on a plain surface: raw hide and fur panels"),
    (re.compile(r"\bpadded (?:armou?r|armor)\b", re.I),
          "a suit of quilted padded cloth armour laid out flat on a plain surface"),
    (re.compile(r"\bdwarven plate\b|\bhalf-plate\b|\bbreastplates?\b|\bplate armour\b", re.I),
          "a fitted steel breastplate with riveted lames and shoulder guards, empty"),
    (re.compile(r"\bspears?\b|\bpolearm\b", re.I),
          "a long wooden shaft with a leaf-shaped iron spearhead"),
    (re.compile(r"\bbelt of .*strength\b|\bbelt\b", re.I),
          "a broad leather belt with a heavy studded metal buckle"),
    (re.compile(r"\bcloak\b|\brobes?\b|\braiment\b|\bclothes\b|\bturban\b", re.I),
          "a folded hooded cloth garment with a clasp"),
    (re.compile(r"\bheadbands?\b|\bcirclets?\b", re.I),
          "a slim plain silver circlet band, empty, the object alone in frame"),
    (re.compile(r"\bhelms?\b|\bhelmets?\b|\bheadband\b|\bturban\b|\bcap\b|\bhat\b", re.I),
     "a single metal or leather headpiece with straps, empty"),
    (re.compile(r"\bboots?\b|\bsnowboots?\b|\bshoes?\b", re.I),
          "a pair of empty leather boots standing side by side"),
    (re.compile(r"\bgauntlets?\b|\bgloves?\b|\bbracers?\b", re.I),
          "a pair of empty leather and iron arm guards"),
    (re.compile(r"\bpelts?\b|\bfurs?\b|\bwool\b|\bclothes, (?:spring|fall)\b", re.I),
          "a folded animal pelt with the fur showing"),
    (re.compile(r"\bapparatus of the crab\b", re.I),
          "a sealed iron barrel with brass fittings, riveted bands, small hatches and jointed legs"),
    (re.compile(r"\bfeather token\b", re.I),
          "a small carved feather-shaped charm of ivory on a cord"),
    (re.compile(r"\bhunting trap\b", re.I),
          "an open steel trap laid flat on a plain surface: two toothed iron jaws on a round pressure plate, a chain and a stake"),
    (re.compile(r"\bfigurine of wondrous power\b|\bfigurine\b|\btotem\b", re.I),
          "a small carved stone figurine of a crouching beast on a plain plinth, alone in frame"),
    (re.compile(r"\bsignet ring\b|\bband of\b|\bgold band\b|\bring of\b|\bring\b", re.I),
          "a single metal ring, band or seal ring"),
    (re.compile(r"\brods?\b|\bwands?\b|\bsceptres?\b", re.I),
          "a short carved rod of dark wood and metal"),
    (re.compile(r"\bstones?\b|\bjewel\b|\bpearls?\b|\bgem\b|\bmoonstone\b|\bcarbuncle\b", re.I),
          "a single polished stone or cut gem with engraved runes"),
    (re.compile(r"\boats?\b|\bration\b|\blembas\b|\bbread\b", re.I),
          "a wrapped bundle of waybread and a wooden bowl"),
    (re.compile(r"\bhoney\b|\bjams?\b|\bpreserves?\b", re.I),
          "a clay pot of honey with a wooden dipper and a cloth cover"),
    (re.compile(r"\bpoison\b|\bantitoxin\b|\boil\b|\bacid\b|\balchemist'?s fire\b|\bointment\b|"
                r"\bpotions?\b|\bphilters?\b|\belixirs?\b", re.I),
          "a small glass vial or clay flask of liquid with a sealed stopper, one object"),
    (re.compile(r"\bfireworks?\b|\bsparklers?\b", re.I),
          "a bundle of rolled paper fireworks with a paper fuse"),
    (re.compile(r"\blances?\b", re.I),
          "a long heavy wooden lance with a steel tip and a flared hand guard"),
    (re.compile(r"\blongbows?\b|\bshortbows?\b|\bbows?\b", re.I),
          "a tall yew bow stave with a braided linen string"),
    (re.compile(r"\bsling\b", re.I),
          "a leather sling pouch on two braided cords"),
    (re.compile(r"\bspikes?\b", re.I),
          "a single tapering iron spike with a flat head"),
    (re.compile(r"\bsled\b|\bsledge\b|\bcart\b|\bcoach\b|\bcab\b|\bwagon\b", re.I),
          "an empty wooden cart or sledge with spoked or bladed runners"),
    (re.compile(r"\banimal feed\b|\bfeed\b|\boats?\b|\bgrain\b", re.I),
          "a burlap sack of grain spilling oats beside a wooden bucket"),
    (re.compile(r"\bink\b", re.I),
          "a small glass ink bottle with a cork stopper and a trimmed quill"),
    (re.compile(r"\benergy cell\b", re.I),
          "a sealed brass and glass power cell with glowing green contacts and riveted bands"),
    (re.compile(r"\broot\b", re.I),
          "a gnarled dried root with knotted fibres"),
    (re.compile(r"\bleaf\b|\bleaves\b", re.I),
          "a handful of dried green leaves tied with twine"),
    (re.compile(r"\bberr(?:y|ies)\b", re.I),
          "a small pile of dark ripe berries on a folded cloth"),
    (re.compile(r"\bfruit\b", re.I),
          "a single round ripe fruit with a short stalk and a leaf"),
    (re.compile(r"\bhide\b", re.I),
          "a folded tanned animal hide"),
)

#: The wrong readings measured on the same audit, as NEGATIVES. The positive cue says what the object
#: is; this says what the model keeps drawing instead. Read `item_negative()` for why both are needed —
#: the short version is that "no plate" inside the positive prompt is not a ban, it is a mention.
ITEM_NEGATIVES = (
    (re.compile(r"\bsling bullet|\bbullets?\b", re.I),
     "pointed tip, conical bullet, rifle round, cartridge, projectile"),
    (re.compile(r"\bplate\b|\bhalf-plate\b|\bbreastplate\b", re.I),
     "wearer, body, mannequin, face, hands, legs, person"),
    (re.compile(r"\bbelt of [a-z ]*giant strength\b|\bbelts?\b", re.I),
     "giant, face, head, torso, waist, hips, wearer, person"),
    (re.compile(r"\bthrower\b|\bwarhammer\b", re.I),
     "dwarf, person, hands, helmet, skull, armour, face"),
    (re.compile(r"\beyes of the eagle\b", re.I),
     "eagle, bird, beak, feathers, head, animal"),
    (re.compile(r"\bring[- ]?mail\b|\bchain\s*mail\b|\bchainmail\b|\bhauberk\b|\bchain shirt\b|"
                r"\belven chain\b|\bmithral\b|\bm(?:ail)\b", re.I),
     "plate armour, metal pauldrons, jewellery ring, gold band, gemstones, chain necklace, "
     "cloth shirt"),
    (re.compile(r"\barmou?r\b|\bbreastplate\b|\bhide\b|\bpadded\b|\bhalf-plate\b|\bcloak\b|"
                r"\brobes?\b|\braiment\b|\bclothes\b|\bturban\b|\bpelts?\b|\bfurs?\b|\bbelts?\b|"
                r"\bboots?\b|\bgauntlets?\b|\bgloves?\b", re.I),
     "polished steel plate, riveted plackart, wearer, body, mannequin, face, hands, legs"),
    (re.compile(r"\bbell\b", re.I),
     "cathedral bell, church bell, wheeled yoke, cart, wagon, machinery"),
    (re.compile(r"\blamps?\b|\blanterns?\b|\btorch\b|\bcandles?\b", re.I),
     "electric lamp, light bulb, glass bulb, screw socket, power cord, plug, wall socket, chandelier"),
    (re.compile(r"\bammunition\b|\barrows?\b|\bquarrels?\b|\bbolts?\b|\bneedles?\b|\bbullets?\b|"
                r"\bdarts?\b|\bshaft\b", re.I),
     "brass cartridge, bullet casing, primer, jacketed bullet, metal round, shell casing"),
    (re.compile(r"\bbows?\b", re.I),
     "compound bow, metal riser, sight pin, scope, firearm, crossbow"),
    (re.compile(r"\bpistols?\b|\bmuskets?\b|\brifles?\b", re.I),
     "modern handgun, revolver, cylinder, automatic pistol, assault rifle, telescopic sight"),
    (re.compile(r"\bpick\b|\bminer'?s\b|\bpry bar\b|\bgrappling\b", re.I),
     "gear wheel, cog, toothed wheel, machine part, engine, plumbing, axe blade, crescent blade"),
    (re.compile(r"\bink\b", re.I),
     "screw cap, ribbed cap, printed label, plastic bottle, modern packaging"),
    (re.compile(r"\bnet\b", re.I),
     "landing net, fishing net frame, rigid hoop, long handle, butterfly net"),
    (re.compile(r"\bwhistles?\b", re.I),
     "flared bore, engraved cylinder, bugle, telescope, horn, trumpet, plumbing"),
    (re.compile(r"\bsickles?\b|\bscythe\b", re.I),
     "double blade, two blades, glaive, giant blade, polearm"),
    (re.compile(r"\btridents?\b", re.I),
     "table fork, dining fork, cutlery, tines, two prongs, crescent, openwork ring"),
    (re.compile(r"\bbattering ram\b|\bportable ram\b", re.I),
     "sheep, ram, goat, horns, wool, animal, livestock"),
    (re.compile(r"\bmace\b|\bclub\b|\bcudgel\b|\bquarterstaff\b|\bwalking stick\b|\bwarhammer\b", re.I),
     "sword, blade, sharpened edge, crossguard, axe, firearm, metal finial, ornate head"),
    (re.compile(r"\blances?\b|\bpikes?\b|\bspears?\b|\bglaive\b|\btrident\b|\bhalberd\b", re.I),
     "sword, short blade, dagger, crossguard, club"),
    (re.compile(r"\bhunting trap\b|\btrap\b", re.I),
     "spoked wheel, gear wheel, hub, rim, disc, wooden box, chest, crate"),
    (re.compile(r"\bvehicles?\b|\bcoach\b|\bcab\b|\bcart\b|\bwagon\b|\bsled\b|\bboat\b|\bship\b", re.I),
     "motor truck, motor car, motorboat, engine, exhaust, pneumatic tyre, smokestack, "
     "modern vehicle"),
    (re.compile(r"\bpotions?\b|\bphilters?\b|\belixirs?\b|\boils?\b|\bacid\b|\bpoison\b|\bointment\b|"
                r"\bflask\b|\bvials?\b", re.I),
     "screw cap, ribbed cap, printed label, plastic bottle, jerrycan, syringe, skull, bones, animal, creature"),
    (re.compile(r"\bhelms?\b|\bhelmets?\b|\bheadbands?\b|\bcaps?\b|\bturbans?\b|\bhats?\b", re.I),
     "face, head, wearer, person, portrait, eyes, nose, crown, hair"),
    (re.compile(r"\bshields?\b", re.I),
     "kite shield, heater shield, tower shield, heraldic charge"),
    (re.compile(r"\bclothes\b|\bcloak\b|\brobes?\b|\braiment\b|\btraveller'?s\b|\btraveler'?s\b", re.I),
     "zip, zipper, press stud, parka, cargo trousers, hiking boots, modern rucksack"),
    (re.compile(r"\bwaterskins?\b|\bwineskins?\b|\bsaddlebags?\b|\bbackpacks?\b", re.I),
     "satchel, handbag, purse, bag with a handle, D-ring, armour, cuirass, torso"),
    (re.compile(r"\bpipes?\b|\bbagpipes?\b|\bdrum\b|\blonghorn\b", re.I),
     "plumbing, barrel, drum, machinery, elbow joint, tap, drain, chain, trumpet"),
    (re.compile(r"\bbridles?\b|\bbit and bridle\b|\bhalters?\b", re.I),
     "horse, pony, muzzle, mane, ears, nostrils, rider, saddle"),
    (re.compile(r"\bscrolls?\b", re.I),
     "treasure chest, jewellery, gemstone, gold ring, amulet"),
    (re.compile(r"\brings?\b|\bbands?\b", re.I),
     "person, woman, man, hand, finger, wearer, water, splash, wave, ripple"),
    (re.compile(r"\bapparatus of the crab\b", re.I),
     "giant crab, live crab, sea creature, animal, armour"),
    (re.compile(r"\bprosthetic limb\b|\blimb\b", re.I),
     "two hands, extra fingers, duplicate limbs, human skin"),
    (re.compile(r"\bspies'? murmurs?\b|\bmurmur\b", re.I),
     "war helm, skull mask, face, full helmet, horned helmet"),
    (re.compile(r"\bsaddles?\b|\btack\b|\bhorseshoes?\b", re.I),
     "horse, hoof, leg, rider, cowboy, saddle, stirrup"),
    (re.compile(r"\bfigurines?\b|\btotems?\b|\bstatuettes?\b", re.I),
     "person, king, human, face, hands, crown, portrait"),
    (re.compile(r"\bstones?\b|\bgems?\b|\bjewels?\b|\bpearls?\b", re.I),
     "jewellery box, necklace, crown, treasure hoard"),
    (re.compile(r"\brods?\b|\bwands?\b|\bsceptres?\b", re.I),
     "person, hand, figure, scroll, staff"),
)

#: Words that make a snippet a rules line: ability/stat abbreviations, dice, coin and weight units, and
#: the weapon-property vocabulary. Deliberately narrow — "light" and "heavy" alone are ordinary English
#: in a real description, so the units and the abbreviations carry the decision.
_RULES_TOKEN = re.compile(
    r"^(ac|hp|dc|str|dex|con|int|wis|cha)$|"
    r"^\d+d\d+$|^\d+(\.\d+)?$|^(lb|gp|sp|cp|ft)\.?$|"
    r"^(hit|hits|point|points|advantage|disadvantage|check|checks|save|saving|requires|"
    r"proficiency|bludgeoning|piercing|slashing|finesse|versatile|ammunition|athletics|"
    # Field labels and rules vocabulary measured on the item shelf: "Type: Ring-mail.
    # Craftsmanship: Dwarven. Qualities: ..." scored below the share and went to the model as a
    # visual tail, which is how a mail coat was drawn as a finger ring.
    r"type|craftsmanship|qualities|rarity|attunement|charges?|modifier|rolls?)$", re.I)

#: A snippet that OPENS with a stat field label is a record's rules line whatever its share: there is no
#: reading of "Type: Ring-mail. Craftsmanship: Dwarven." that describes an object's shape.
_STAT_FIELD = re.compile(r"^\s*(?:type|craftsmanship|qualities|rarity|armou?r|weapon|wondrous item)\s*:",
                         re.I)

#: The same type line, written as prose. Measured on the batch-01..10 audit residues: the magic-item
#: summaries open "Wondrous item, legendary This item first appears to be a Large sealed iron barrel…",
#: "Weapon (arrow), very rare An arrow of slaying is a magic weapon meant to slay…", "Potion, uncommon
#: When you drink this potion…". The comma form carries no colon, so the rules test above waved it
#: through and the model received a category and a rarity where a shape belongs — the apparatus came
#: out as a live giant crab, the arrow of slaying as a polearm blade.
_TYPE_LINE = re.compile(
    r"^\s*(?:wondrous items?|weapons?|armou?rs?|potions?|scrolls?|staffs?|staves|rods?|wands?|rings?|"
    r"ammunition|shields?|wondrous)\b[^.]{0,48}?"
    r"\b(?:common|uncommon|rare|very rare|legendary|artifact|varies)\b", re.I)

#: Second-person address. A statement of an object's SHAPE never tells the reader what they can do;
#: rules and story text does it constantly ("You can use an action…", "you must be proficient…"),
#: and that reading is what puts a wearer, a driver or a crowd into a picture of a rope.
_SECOND_PERSON = re.compile(r"\b(?:you|your|yours|yourself)\b", re.I)

#: Hints that a snippet is about a scene rather than the object: it names a wearer doing something with
#: the item. Kept to verbs that only make sense of a person, so real descriptions pass untouched.
_USER_VERB = re.compile(r"\b(?:wears?|wearing|wore|wield\w*|holds?|holding|carries|carrying|drinks?|"
                        r"wears?|rides?|riding|dons?|strap\w* on)\b", re.I)


def snippet_is_type_line(snippet: str) -> bool:
    """True when the snippet opens with a category-and-rarity line rather than a description."""
    return bool(_TYPE_LINE.match((snippet or "").strip()))


def snippet_is_second_person(snippet: str) -> bool:
    """True when the snippet addresses the reader (rules/story prose), so it must not steer the art."""
    return bool(_SECOND_PERSON.search(snippet or "")) or bool(_USER_VERB.search(snippet or ""))


def snippet_is_mechanics(snippet: str, share: float = 0.34) -> bool:
    """True when the snippet is mostly rules text, so it must not be used as a visual tail.

    A share rather than a single hit: descriptions legitimately mention a weight or an AC in passing
    ("a shield of blackened steel, 6 lb."), and dropping those would throw away real description.
    """
    words = [w.strip(".,;:()!?\"'") for w in (snippet or "").split()]
    words = [w for w in words if w]
    if len(words) < 3:
        return False
    if _STAT_FIELD.match(snippet or ""):
        return True
    hits = sum(1 for w in words if _RULES_TOKEN.match(w))
    return hits / len(words) >= share


#: Words that cannot end a sentence: a capped snippet that stops on one of these is a severed clause,
#: and a diffusion model reads the fragment as damage. Measured case: the Arkenstone's tail ended
#: "...Named the Heart of the Mountain, the." — the "A globe with a thousand facets" that the audit
#: judged the tile against never reached the model.
_DANGLING = {
    "the", "a", "an", "of", "and", "or", "but", "with", "without", "in", "on", "at", "to", "for", "by",
    "from", "as", "that", "which", "who", "whose", "its", "his", "her", "their", "is", "are", "was",
    "were", "be", "been", "into", "over", "under", "than", "then", "when", "while",
}


def _trim_tail(snippet: str, cap: int = 15) -> str:
    """The visual tail: at most `cap` words, cut back to a sentence end rather than mid-clause."""
    words = (snippet or "").split()
    if len(words) <= cap:
        return " ".join(words)
    cut = " ".join(words[:cap])
    last = cut.rstrip(".,;:!?\"'").rsplit(" ", 1)[-1].lower()
    if cut[-1] not in ".!?" or last in _DANGLING:
        # Prefer the last complete sentence inside the cap; otherwise drop the dangling words only.
        for sep in (". ", "! ", "? "):
            j = cut.rfind(sep)
            if j > 0:
                return cut[:j + 1]
        while cut and cut.rsplit(" ", 1)[-1].rstrip(".,;:!?\"'").lower() in _DANGLING:
            cut = cut.rsplit(" ", 1)[0]
    return cut


def item_negative(name: str, base: str | None = None) -> str:
    """The ordinary item bans plus the wrong readings THIS object's name has actually produced.

    A positive cue is not always enough. "Ring Mail" carries the cue "a mail coat of thousands of
    interlocking silver rings, no plate, not a finger ring" and still came back as a polished plate
    harness; "+1 Mithral Chain Shirt" carries "a sleeveless tunic of thousands of interlocking iron
    rings" and came back as a navy cloth shirt hung with gold chains. The model follows the noun's
    prior and reads a "no X" clause inside the positive prompt as X. Negation is the mechanism that
    works (see COMFY_NEGATIVE), so each family that failed this way names its wrong readings here and
    they are appended to the standard item bans rather than replacing them.

    Kept to a dozen words per entry: the negative has its own 77-token window, and a longer list
    starts crowding out the bans that already work.
    """
    from services.portraits import COMFY_NEGATIVE
    base = base or COMFY_NEGATIVE
    for pattern, terms in ITEM_NEGATIVES:
        if pattern.search(name or ""):
            return f"{base}, {terms}"
    return base


def item_cue(name: str) -> str | None:
    """The ordinary shape of the object a gear record names, or None when the name needs no help."""
    for pattern, description in ITEM_CUES:
        if pattern.search(name or ""):
            return description
    return None


#: Records whose NAME outranks any shape clause, so the prompt's copy of the name is rewritten.
#: A diffusion model reads the noun at the head of the prompt first, and where the name carries a
#: different object the cue loses: "Silver Ring-mail of Girion" came back as a finger RING under every
#: cue wording tried (the shelf's own tile is a ring for the same reason), and "The Arkenstone (Heart of
#: the Mountain)" came back as a HEART under "a large round faceted white gemstone". The replacement
#: uses the record's own words for what the object is - it renames nothing in the database.
_NAME_OVERRIDES = {
    "dwarven plate": "Plate Armor",
    "dwarven thrower": "Warhammer of Returning",
    "eyes of the eagle": "Brass-framed Crystal Lenses",
    "hide armor": "Rough Hide and Fur Panels",
    "hunting trap": "Steel Jaw Spring Trap",
    "signal whistle": "Small brass whistle",
    "potion of animal friendship": "Glass Vial of Potion",
    "sling bullet": "Lead sling balls",
    "ammunition, +1, +2, or +3": "Arrows, +1, +2, or +3",
    "ammunition, +1": "Arrows, +1",
    "ammunition, +2": "Arrows, +2",
    "ammunition, +3": "Arrows, +3",
    "silver ring-mail of girion": "Silver Mail Coat of Girion",
    "the arkenstone (heart of the mountain)": "The Arkenstone",
}

#: A trailing parenthetical on an item name is an alias or a filing label ("(Heart of the Mountain)",
#: "(Rhingalad)"), and its nouns compete with the shape clause. Dropped from the prompt only.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


def prompt_name(name: str) -> str:
    """The name as it goes into the prompt: overrides first, then a trailing alias dropped."""
    key = (name or "").strip().lower()
    if key in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[key]
    return _PARENTHETICAL.sub("", name or "").strip() or (name or "")


#: Which species leads the prompt for a humanoid record: (pattern, lead noun, traits).
#:
#: Only species a reference record actually names are listed, and only from the record's own words —
#: there is no guessing from a name like "Sage". A species is not cosmetic here: the shared negative
#: prompt used to ban "people, humans" outright, so a humanoid record had nothing human left to draw
#: and came back horned and scaled (see COMFY_NEGATIVE_LIVING in services.portraits). The traits clause
#: is what makes the model put the horns DOWN: tieflings and dragonborn keep theirs because they have
#: them, and everyone else is told plainly that there are none. "human form" leads the table so a
#: werewolf's human shape wins over the wolf in its own name.
SPECIES_CUES = (
    (re.compile(r"\b(?:human|mortal|true)\s+form\b", re.I),
     "a human", "Ordinary human skin, no fur, no snout, no claws."),
    (re.compile(r"\bhalf[- ]elf\b", re.I),
     "a half-elf", "Smooth skin, slightly pointed ears, no horns, no tusks."),
    (re.compile(r"\bhalf[- ]orc\b", re.I),
     "a half-orc", "Green-grey skin, small lower tusks, no horns."),
    (re.compile(r"\bel(?:f|ves|ven|vish)\b", re.I),
     "an elf", "Smooth fair skin, pointed ears, no horns, no tusks."),
    (re.compile(r"\bdwar(?:f|ves|ven|vish)\b", re.I),
     "a dwarf", "Smooth skin, a long full beard, no horns."),
    (re.compile(r"\bgnom(?:e|es|ish)\b", re.I),
     "a gnome", "Smooth skin, a long nose, no horns."),
    (re.compile(r"\bhalfling\b", re.I),
     "a halfling", "Smooth skin, curly hair, bare feet, no horns."),
    (re.compile(r"\bgoliath\b", re.I),
     "a goliath", "Grey stone-grey skin with dark markings, no horns."),
    (re.compile(r"\btiefling\b", re.I),
     "a tiefling", "Human face, small curved brow horns, a thin tail."),
    (re.compile(r"\bdragonborn\b", re.I),
     "a dragonborn", "Scaled reptilian skin, a draconic snout, no hair."),
    (re.compile(r"\b(?:man|men|woman|women)\s+of\b", re.I),
     "a human", "Smooth skin, no horns, no tusks."),
    (re.compile(r"\bhumans?\b(?!-)|\bhumanoid\s*\(human\)", re.I),
     "a human", "Smooth skin, no horns, no tusks."),
)


def species_cue(text: str) -> tuple[str, str] | None:
    """('a human', traits) when the record's own words name a species, else None.

    "humanoid" on its own is deliberately NOT a match: it demotes the species to a vague adjective and
    the model falls back on its default fantasy face (the measured failure in services.portraits'
    npc_prompt — a dwarf came back slender with pointed ears). A record that says only "humanoid" gets
    no species clause rather than a wrong one.
    """
    for pattern, lead, traits in SPECIES_CUES:
        if pattern.search(text or ""):
            return lead, traits
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
    tail = _trim_tail(snippet)
    short_name = " ".join((name or "").split()[:8])
    cue = construct_cue(name, detail)
    if kind == "creature":
        if cue:
            return ("Fantasy construct: " + (short_name or "a construct")
                    + (f", {detail}." if detail else ".")
                    + f" {cue}."
                    + " Full body, single subject." + (f" {tail}" if tail else ""))
        if snippet_is_mechanics(snippet):
            # Stat blocks are the same trap as the equipment snippets: "Medium, humanoid (human),
            # neutral evil. AC 15. HP 78 (12d8 + 24). Speed 30 ft.. CR 5." names a size, an alignment
            # and some dice — never a body. Fed as the tail it does not describe the creature, it just
            # eats the 77-token window.
            tail = ""
        species = species_cue(" ".join((name or "", subtitle or "",
                                        " ".join((snippet or "").split()[:12]))))
        if species:
            lead, traits = species
            # The species leads the prompt, the same way npc_prompt fused "a female gnome" ahead of
            # the name and got 4/4 where a trailing "humanoid" got 0/4. It has to be here and not in
            # the tail: the tail is where the token window truncates.
            return ("Fantasy bestiary: " + lead + ", " + (short_name or "a figure")
                    + (f", {detail}." if detail else ".")
                    + f" Full body, single {lead.split(' ', 1)[1]}." + f" {traits}"
                    + (f" {tail}" if tail else ""))
        return ("Fantasy bestiary: " + (short_name or "a monster")
                + (f", {detail}." if detail else ".")
                + " Full body, single creature." + (f" {tail}" if tail else ""))
    if kind == "item":
        # The item's own prompt name: aliases dropped, and the measured load-bearing names rewritten
        # (see _NAME_OVERRIDES). Only items do this — a bestiary name is not a filing label.
        item_name = " ".join(prompt_name(name).split()[:8]) or "an item"
        # Two more readings of the same failure, both measured on the batch-01..10 residues: a
        # category-and-rarity LINE ("Wondrous item, legendary This item first appears to be a Large
        # sealed iron barrel") and prose ADDRESSED TO A READER ("You can use an action…", "you must be
        # proficient with wind instruments"). Neither names a shape; both drag the model toward the
        # category (a live crab for the apparatus) or toward a person handling the thing (a knight for
        # barding, a crowd for a service). Items only: a bestiary note may legitimately tell a story.
        if snippet_is_type_line(snippet) or snippet_is_second_person(snippet):
            tail = ""
        if cue:
            # The cue-bearing items are the vehicles and engines, and the old wording hurt them
            # twice: "RPG item illustration" leads a diffusion model toward a small hand-held object,
            # and interpolating the record's category produced "Carriage (Mounts and Vehicles)" — a
            # filing label, not a description. The object itself now leads.
            return ("Fantasy construct: " + item_name
                    + f". {cue}." + (f" {tail}" if tail else ""))
        shape = item_cue(short_name)
        if shape:
            # The family cue is the visual statement. The snippet is left out on purpose: for these
            # records it is the rules line, and a rules line can only fight the cue (see ITEM_CUES).
            return ("Fantasy object: " + item_name + f". {shape}.")
        if snippet_is_mechanics(snippet):
            # Rules text names dice and weights, never the shape, so the model free-fills from the
            # noun. Dropping it leaves the name and its category, which cannot mislead.
            tail = ""
        return ("Fantasy object: " + item_name
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
        from services.portraits import generate_portrait_image, COMFY_NEGATIVE_LIVING
        width, height = KINDS[kind]
        prompt = prompt_for(kind, name, subtitle, snippet)
        # Creatures and NPCs are mostly humanoids, so the item negative prompt's "people, humans" ban
        # would delete the subject of the very record it is describing — that is what turned a fifth of
        # the flagged creature tiles into horned demons. Items keep the ban: it is what keeps riders,
        # drivers and crowds out of a picture of a rope — and gain the family bans on top of it, because
        # for a dozen object families the model reads the positive cue's "no plate"/"not a ring" clause
        # as a mention of plate and of a ring (see ITEM_NEGATIVES).
        negative = COMFY_NEGATIVE_LIVING if kind in ("creature", "npc") else item_negative(name)
        last = "no attempt made"
        async with _SEM:
            for attempt in range(retries + 1):
                data, err = await generate_portrait_image(prompt, max_wait=max_wait,
                                                          width=width, height=height,
                                                          negative=negative)
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
