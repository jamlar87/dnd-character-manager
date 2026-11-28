from __future__ import annotations
from pathlib import Path
import re
import subprocess
from .core import CACHE_DIR, CHUNK_CHARS, CHUNK_OVERLAP
from .jsonio import _fix_ocr_artifacts


# ═══════════════════════════════════════════════════════════════════════════════
# Text Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text(manual: dict) -> str | None:
    """Extract text from a PDF manual. Cached to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{manual['slug']}.txt"

    pdf_path = Path(manual["abs_path"])
    if not pdf_path.exists():
        print(f"  ERROR: PDF not found: {pdf_path}")
        return None

    # Use cache if valid AND has page markers (pdftotext caches lack markers)
    if cache_path.exists():
        pdf_mtime = pdf_path.stat().st_mtime
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= pdf_mtime:
            text = cache_path.read_text(encoding="utf-8", errors="replace")
            if "--- PAGE " in text:
                print(f"  Text cached ({len(text):,} chars)")
                return text
            else:
                print("  Cache has no page markers (old pdftotext cache) — re-extracting with pymupdf")

    print("  Extracting text...")
    text = _extract_pymupdf(str(pdf_path), str(cache_path))
    if not text:
        text = _extract_pdftotext(str(pdf_path), str(cache_path))
    if not text:
        return None
    return text


def _extract_pymupdf(pdf_path: str, cache_path: str) -> str | None:
    """Try pymupdf (fitz) for better multi-column text extraction.
    Falls back to OCR for scanned/image-only PDFs.
    Injects page markers for chapter detection."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages = []
        for i, page in enumerate(doc):
            page_text = page.get_text("text").strip()
            if page_text:
                pages.append(f"--- PAGE {i + 1} ---\n{page_text}")
        doc.close()
        text = "\n\n".join(pages)

        # If pymupdf extracted very little text for many pages, try OCR
        if len(text) < 500 and total_pages > 5:
            ocr_text = _ocr_pymupdf(pdf_path)
            if ocr_text and len(ocr_text) > len(text):
                text = ocr_text

        if len(text) > 500:
            # Fix OCR artifacts before caching — downstream consumers
            # (LLM prompts, chapter extraction) all get clean text.
            text = _fix_ocr_artifacts(text)
            Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
            print(f"  Extracted {len(text):,} chars ({total_pages} pages) via pymupdf → {Path(cache_path).name}")
            return text
    except ImportError:
        pass
    except Exception as e:
        print(f"  pymupdf error: {e}")
    return None


def _ocr_pymupdf(pdf_path: str) -> str | None:
    """Fallback OCR for scanned/image-only PDFs using tesseract."""
    print("  PDF appears scanned — running OCR (this may take a while)...")
    try:
        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(pdf_path)
        total = len(doc)
        all_pages = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            page_text = pytesseract.image_to_string(img).strip()
            if page_text:
                all_pages.append(f"--- PAGE {i + 1} ---\n{page_text}")
            if (i + 1) % 10 == 0:
                print(f"    OCR progress: {i+1}/{total} pages")
        doc.close()
        text = "\n\n".join(all_pages)
        print(f"  OCR complete: {len(text):,} chars from {len(all_pages)} pages")
        return text
    except ImportError as e:
        print(f"  OCR not available: {e}. Install pytesseract + tesseract-ocr.")
        return None
    except Exception as e:
        print(f"  OCR error: {e}")
        return None


def _extract_pdftotext(pdf_path: str, cache_path: str) -> str | None:
    """Fallback: pdftotext extraction."""
    print("  Extracting text via pdftotext...")
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, cache_path],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            print(f"  pdftotext error: {result.stderr[:200]}")
            return None
        text = Path(cache_path).read_text(encoding="utf-8", errors="replace")
        # Fix OCR artifacts in pdftotext output too
        text = _fix_ocr_artifacts(text)
        Path(cache_path).write_text(text, encoding="utf-8", errors="replace")
        print(f"  Extracted {len(text):,} chars → {Path(cache_path).name}")
        return text
    except FileNotFoundError:
        print("  ERROR: pdftotext not installed. Install poppler-utils.")
        return None
    except subprocess.TimeoutExpired:
        print("  ERROR: pdftotext timed out after 3 min")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Chunking
# ═══════════════════════════════════════════════════════════════════════════════

def chunk_text(text: str, chunk_size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Split text into overlapping chunks on natural boundaries.

    Returns [{index, offset, length, text}].
    """
    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{3,}', '  ', text)

    # Split on paragraph breaks
    paragraphs = re.split(r'\n\s*\n', text)
    
    chunks = []
    current = ""
    offset = 0
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 > chunk_size and current:
            # Save current chunk
            chunks.append({
                "index": idx,
                "offset": offset,
                "length": len(current),
                "text": current.strip(),
            })
            # Start new chunk with overlap from end of previous
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + "\n\n" + para
            offset += len(current) - len(para) - 2
            idx += 1
        else:
            if current:
                current += "\n\n" + para
            else:
                current = para

    # Don't forget the last chunk
    if current.strip():
        chunks.append({
            "index": idx,
            "offset": offset,
            "length": len(current),
            "text": current.strip(),
        })

    # Merge tiny final chunks
    if len(chunks) >= 2 and len(chunks[-1]["text"]) < 500:
        chunks[-2]["text"] += "\n\n" + chunks[-1]["text"]
        chunks[-2]["length"] += chunks[-1]["length"]
        chunks.pop()

    return chunks
