from __future__ import annotations
from pathlib import Path
import re


# ═══════════════════════════════════════════════════════════════════════════════
# Chapter detection
# ═══════════════════════════════════════════════════════════════════════════════

# Manuals whose PDF carries no usable table of contents AND whose running headers defeat the text
# heuristic. Left to itself the detector guesses, and a mis-bounded chapter is silently dropped or only
# partly read: that is how Tal'Dorei Reborn came back with "5 of 9 chapters", one race, and 12 entries
# from a 65-page bestiary. The ranges come from the book's own running headers.
CHAPTER_OVERRIDES = {
    "TCSR": [
        ("Chapter 1: Welcome to Tal'Dorei", 5, 31),
        ("Chapter 2: Allegiances of Tal'Dorei", 32, 67),
        ("Chapter 3: Tal'Dorei Gazetteer", 68, 149),
        ("Chapter 4: Character Options", 150, 207),
        ("Chapter 5: Game Master's Toolkit", 208, 217),
        ("Chapter 6: Allies and Adversaries", 218, 283),
    ],
    # Books whose PDF carries no level-1 chapter ToC (the detector only reads level 1, so these
    # collapsed to a single "Front Cover"/title chunk and a skipped chapter would have been
    # invisible). Ranges are the books' own ToC/contents-page page numbers (MPMM, WGE, KW come
    # from their printed contents page; WGE's chapter starts were read off its running heads).
    "EGW": [
        ("Preface", 5, 5),
        ("Welcome to Wildemount", 6, 11),
        ("Ch. 1: Story of Wildemount", 12, 35),
        ("Ch. 2: Factions and Societies", 36, 59),
        ("Ch. 3: Wildemount Gazetteer", 60, 161),
        ("Ch. 4: Character Options", 162, 205),
        ("Ch. 5: Adventures in Wildemount", 206, 265),
        ("Ch. 6: Wildemount Treasures", 266, 281),
        ("Ch. 7: Wildemount Bestiary", 282, 302),
        ("Glossary", 303, 305),
        ("Maps", 306, 306),
    ],
    "GGR": [
        ("Welcome to Ravnica", 7, 11),
        ("Chapter 1: Character Creation", 12, 29),
        ("Chapter 2: Guilds of Ravnica", 30, 99),
        ("Chapter 3: The Tenth District", 100, 123),
        ("Chapter 4: Creating Adventures", 124, 173),
        ("Chapter 5: Treasures", 174, 183),
        ("Chapter 6: Friends and Foes", 184, 257),
    ],
    "WDH": [
        ("Forward", 3, 5),
        ("Introduction", 6, 21),
        ("Ch. 1: A Friend In Need", 22, 31),
        ("Ch. 2: Trollskull Alley", 32, 43),
        ("Ch. 3: Fireball", 44, 57),
        ("Ch. 4: Dragon Season", 58, 99),
        ("Ch. 5: Spring Madness", 100, 115),
        ("Ch. 6: Hell of a Summer", 116, 131),
        ("Ch. 7: Maestro's Fall", 132, 147),
        ("Ch. 8: Winter Wizardry", 148, 163),
        ("Ch. 9: Volo's Waterdeep Enchiridion", 164, 189),
        ("App. A: Magic Items", 190, 193),
        ("App. B: Monsters and NPCs", 194, 221),
        ("App. C: Handouts", 222, 227),
    ],
    "ToA": [
        ("Foreword", 3, 5),
        ("Introduction", 6, 15),
        ("Ch. 1: Port Nyanzaru", 16, 37),
        ("Ch. 2: The Land of Chult", 38, 91),
        ("Ch. 3: Dwellers of the Forbidden City", 92, 111),
        ("Ch. 4: Fane of the Night Serpent", 112, 125),
        ("Ch. 5: Tomb of the Nine Gods", 126, 191),
        ("App. A: Character Backgrounds", 192, 193),
        ("App. B: Random Encounters", 194, 205),
        ("App. C: Discoveries", 206, 209),
        ("App. D: Monsters and NPCs", 210, 243),
        ("App. E: Player Handouts", 244, 256),
        ("App. F: Trickster Gods of Omu", 257, 259),
    ],
    "CotN": [
        ("Introduction: Answering the Call", 5, 18),
        ("Ch. 1: A Fateful Competition", 19, 36),
        ("Ch. 2: The Leave-Taking", 37, 48),
        ("Ch. 3: Bazzoxan", 49, 76),
        ("Ch. 4: The Jewel of Hope", 77, 120),
        ("Ch. 5: The Drowned City", 121, 138),
        ("Ch. 6: The Netherdeep", 139, 172),
        ("Ch. 7: The Heart of Despair", 173, 187),
        ("Appendix A: Creatures", 188, 212),
        ("Appendix B: Magic Items", 213, 217),
        ("Appendix C: Medals of Merit", 218, 218),
        ("Appendix D: Fragments of Suffering", 219, 219),
        ("Appendix E: Story Concept Art", 220, 223),
        ("Appendix F: Poster Map", 224, 225),
    ],
    "DTCOE": [
        ("Using This Book", 5, 7),
        ("Ch. 1: Character Options", 8, 83),
        ("Ch. 2: Group Patrons", 84, 105),
        ("Ch. 3: Magical Miscellany", 106, 139),
        ("Ch. 4: Dungeon Master's Tools", 140, 193),
    ],
    "TCE": [
        ("Using This Book", 5, 7),
        ("Ch. 1: Character Options", 8, 83),
        ("Ch. 2: Group Patrons", 84, 105),
        ("Ch. 3: Magical Miscellany", 106, 139),
        ("Ch. 4: Dungeon Master's Tools", 140, 193),
    ],
    "XGE": [
        ("Ch. 1: Character Options", 8, 77),
        ("Ch. 2: Dungeon Master's Tools", 78, 147),
        ("Ch. 3: Spells", 148, 172),
        ("App. A: Shared Campaigns", 173, 175),
        ("App. B: Character Names", 176, 194),
    ],
    "MPMM": [
        ("Using This Book", 4, 4),
        ("Ch. 1: Fantastical Races", 5, 36),
        ("Ch. 2: Bestiary", 37, 282),
        ("Appendix: Monster Lists", 283, 287),
    ],
    "DMPMOT": [
        ("Using This Book", 4, 4),
        ("Ch. 1: Fantastical Races", 5, 36),
        ("Ch. 2: Bestiary", 37, 282),
        ("Appendix: Monster Lists", 283, 287),
    ],
    "WGE": [
        ("Introduction", 4, 4),
        ("Chapter 1: What Is Eberron?", 5, 20),
        ("Chapter 2: Welcome to Eberron", 21, 58),
        ("Chapter 3: Races of Eberron", 59, 88),
        ("Chapter 4: Dragonmarks", 89, 112),
        ("Chapter 5: Magic Items", 113, 122),
        ("Chapter 6: Sharn, City of Towers", 123, 175),
    ],
    "KW": [
        ("Editorial: Hitting the Target", 6, 9),
        ("The Elven Archer", 10, 15),
        ("Arrows of the Arbonesse", 16, 17),
        ("Derro Ooze Magic", 18, 21),
        ("Bolt & Quiver", 22, 27),
        ("Servants from Beyond", 28, 35),
        ("Night Terrors", 36, 39),
        ("The Power of the Game Master", 40, 41),
        ("Captured in the Cartways", 42, 47),
        ("Putting the Band Back Together", 48, 49),
        ("Fey Hunters & Shadow Hounds", 50, 53),
        ("AGE of Specialization", 54, 55),
        ("Bardic Charisma", 56, 61),
        ("The Bardic Arts", 62, 63),
        ("Ask the Kobold", 64, 65),
        ("Small Spirits", 66, 69),
        ("Unearthed Ancestry", 70, 74),
        ("Fish of Legend", 75, 77),
        ("Book Reviews", 78, 79),
        ("Coming Next Issue", 80, 81),
        ("The Ruins of Arbonesse", 82, 84),
    ],
}


def _detect_chapters(text: str, manual: dict) -> list[dict]:
    """Detect chapter/section boundaries using fitz TOC + page markers in text.

    Returns [{title, start_page, end_page, text}].
    Falls back to a single 'Full Book' chapter if no TOC or page markers.
    """
    slug = manual["slug"]
    pdf_path = Path(manual["abs_path"])

    # An explicit map beats every heuristic. Consulted first, because the failure modes below are silent:
    # a chapter that cannot be found is not reported, it simply never appears.
    override = CHAPTER_OVERRIDES.get(slug)
    if override:
        page_texts = _split_by_page(text)
        chapters = [{"title": t, "start_page": s, "end_page": e,
                     "text": _extract_chapter_text(page_texts, s, e)} for t, s, e in override]
        kept = [ch for ch in chapters if ch.get("text") and len(ch["text"]) >= 100]
        print(f"  {len(kept)} chapter(s) from the explicit map for {slug}:")
        for ch in kept:
            print(f"    p{ch['start_page']}-{ch['end_page']}: {ch['title']} ({len(ch['text'])} chars)")
        return kept

    # Try fitz TOC
    chapters = _detect_from_toc(pdf_path)
    if chapters:
        # Split text by page markers
        page_texts = _split_by_page(text)
        for ch in chapters:
            ch["text"] = _extract_chapter_text(page_texts, ch["start_page"], ch["end_page"])
        # Filter out chapters with no extractable text (maps, image-only pages)
        chapters = [ch for ch in chapters if ch["text"] and len(ch["text"]) >= 100]
        if chapters:
            print(f"  {len(chapters)} chapter(s) with text retained")
            return chapters

    # Fallback: check for text-based chapter markers
    chapters = _detect_from_text(text)
    if chapters and len(chapters) > 1:
        page_texts = _split_by_page(text)
        for ch in chapters:
            ch["text"] = _extract_chapter_text(page_texts, ch["start_page"], ch["end_page"])
        chapters = [ch for ch in chapters if ch.get("text") and len(ch["text"]) >= 100]
        if chapters:
            print(f"  {len(chapters)} chapter(s) from text markers retained")
            return chapters

    # Ultimate fallback: single chapter
    return [{"title": manual["title"], "start_page": 1, "end_page": 9999, "text": text}]


def _detect_from_toc(pdf_path: Path) -> list[dict] | None:
    """Use fitz Table of Contents to get chapter boundaries."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        toc = doc.get_toc()
        doc.close()

        if not toc or len(toc) < 2:
            return None

        # Collect top-level (L1) entries as chapters
        chapters = []
        for _i, (level, title, page) in enumerate(toc):
            if level != 1:
                continue
            # Clean title
            title = title.strip()
            if not title or title.lower() in ("credits", "table of contents", "contents", "back cover"):
                continue
            chapters.append({"title": title, "start_page": page, "end_page": 9999})

        if not chapters:
            return None

        # Set end pages (each chapter ends at the next chapter's start - 1)
        for i in range(len(chapters) - 1):
            chapters[i]["end_page"] = chapters[i + 1]["start_page"] - 1

        print(f"  Detected {len(chapters)} chapters from TOC:")
        for ch in chapters:
            print(f"    p{ch['start_page']}-{ch['end_page']}: {ch['title']}")
        return chapters

    except Exception as e:
        print(f"  TOC detection skipped: {e}")
        return None


def _detect_from_text(text: str) -> list[dict] | None:
    """Fallback: use regex chapter markers in text."""
    marker_pattern = re.compile(
        r'^--- PAGE (\d+) ---.*?\n'           # page number
        r'(?:CHAPTER|Ch\.|Chapter)\s+(\d+)[:\s]',  # chapter marker
        re.MULTILINE | re.IGNORECASE
    )
    matches = list(marker_pattern.finditer(text))
    if not matches:
        return None

    chapters = []
    for _i, m in enumerate(matches):
        page = int(m.group(1))
        ch_num = m.group(2)
        title = f"Chapter {ch_num}"
        chapters.append({"title": title, "start_page": page, "end_page": 9999})

    for i in range(len(chapters) - 1):
        chapters[i]["end_page"] = chapters[i + 1]["start_page"] - 1

    print(f"  Detected {len(chapters)} chapters from text markers")
    return chapters


def _split_by_page(text: str) -> dict[int, str]:
    """Split text with page markers into {page_num: page_text}."""
    pages = {}
    pattern = re.compile(r'--- PAGE (\d+) ---\n(.*?)(?=\n--- PAGE \d+ ---|\Z)', re.DOTALL)
    for m in pattern.finditer(text):
        page_num = int(m.group(1))
        page_text = m.group(2).strip()
        pages[page_num] = page_text
    return pages


def _extract_chapter_text(page_texts: dict[int, str], start: int, end: int) -> str:
    """Extract text for page range [start, end]."""
    parts = []
    for p in range(start, end + 1):
        if p in page_texts:
            parts.append(page_texts[p])
    return "\n\n".join(parts)
