"""Source-attribution hygiene for reference records.

An ingested record whose book could never be determined keeps a placeholder like
"(Unknown Source, p.222)" and, crucially, no `_source_manual` slug — so
data_loader._normalize_manual_source() has nothing to rebuild from. The placeholder is
well-formed enough to pass a shape check, so it flows all the way to the UI and renders as
if it were a book: a "📚 (Unknown Source, p.222)" badge whose click (openSourceRef)
resolves to nothing.

Filtering here, at the points that pack a source for display, keeps a bad record from
reaching any badge even if it is reintroduced by a later ingest.
"""
import re

# Book names that are not books. Matched only against the book part of a source.
_PLACEHOLDER_BOOK = re.compile(
    r"^(unknown|n/?a|none|null|tbd|\?+|[-—]+"
    r"|generic(\s+(treasure|item|entry))?"      # "(Generic treasure)"
    r"|treasure|varies|see\s+(text|below|entry|page)|not\s+applicable)$",
    re.I,
)

# "(Book, p.12)" | "(Book, p 12)" | "(Book)" | bare "Book p.12" | "Book"
_SOURCE_SHAPE = re.compile(r"^\(?([^,()]+?)(?:,\s*p\.?\s*\d+)?\)?$", re.I)


def is_placeholder_source(source) -> bool:
    """True when `source` names no real book. An empty source is NOT a placeholder."""
    s = str(source or "").strip()
    if not s:
        return False
    m = _SOURCE_SHAPE.match(s)
    book = (m.group(1) if m else s).strip().strip("(,;")
    if _PLACEHOLDER_BOOK.match(book):
        return True
    return bool(re.search(r"\bunknown\s+(source|sourcebook)\b", s, re.I))


def clean_source_display(source, fallback: str = "") -> str:
    """A source that is safe to show: the real book, else `fallback`.

    Use with fallback="Manual" where the old code did `x.get("source", "Manual")`, and
    fallback="" where an absent source should render no badge at all.
    """
    s = str(source or "").strip()
    if not s or is_placeholder_source(s):
        return fallback
    return s
