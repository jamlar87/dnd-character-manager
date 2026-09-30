"""A manual with no usable TOC must be given an explicit chapter map.

Tal'Dorei Campaign Setting Reborn has no PDF table of contents (get_toc() returns 0 entries), so the
detector fell back to a text heuristic. It guessed nine chapters, mis-bounded them, then silently
discarded any chapter whose text slice came out under 100 characters. The book came back as "5 of 9
chapters" with one race and 12 entries from a 65-page bestiary, and nothing was logged as an error.

The ranges in CHAPTER_OVERRIDES come from the book's own running headers.
"""
import ingest_manual


def _pages(n=283, chars=400):
    return "\n".join(f"--- PAGE {i} ---\n" + ("word " * (chars // 5)) for i in range(1, n + 1))


def test_tcsr_has_an_explicit_map():
    assert "TCSR" in ingest_manual.CHAPTER_OVERRIDES, "the book that exposed this must be pinned"
    chapters = ingest_manual.CHAPTER_OVERRIDES["TCSR"]
    assert len(chapters) == 6, f"TCSR has six chapters, got {len(chapters)}"
    # ordered, non-overlapping, and covering the book
    for (t1, s1, e1), (t2, s2, e2) in zip(chapters, chapters[1:]):
        assert s1 <= e1, f"{t1} ends before it starts"
        assert s2 == e1 + 1, f"gap or overlap between {t1} and {t2}: {e1} -> {s2}"
    assert chapters[-1][2] == 283, "the last chapter must reach the end of the book"
    # the chapter the heuristic dropped outright - the Gazetteer holds the maps
    gaz = [c for c in chapters if "Gazetteer" in c[0]]
    assert gaz and gaz[0][1] == 68, "Chapter 3 is the Gazetteer and starts at p68"


def test_the_override_is_used_instead_of_the_heuristics():
    """With the map present the detector must not guess - and must return all six chapters."""
    manual = {"slug": "TCSR", "abs_path": "/nonexistent.pdf", "title": "Tal'Dorei Campaign Setting Reborn"}
    chapters = ingest_manual._detect_chapters(_pages(), manual)
    assert len(chapters) == 6, f"expected six chapters, got {len(chapters)}: {[c['title'] for c in chapters]}"
    assert [c["start_page"] for c in chapters] == [5, 32, 68, 150, 208, 218]
    assert all(c["text"] for c in chapters), "every chapter must carry text"


def test_a_manual_without_an_override_still_uses_the_fallbacks():
    """The override must not become a requirement for the other 70-odd manuals."""
    manual = {"slug": "ZZNOTREAL", "abs_path": "/nonexistent.pdf", "title": "Some Other Book"}
    chapters = ingest_manual._detect_chapters(_pages(40), manual)
    assert chapters, "a manual with no override must still yield something (the whole-book fallback)"
