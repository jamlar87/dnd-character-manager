"""Character PDF generation route.

Extracted from routes/characters/all.py (2026-07-31). Imports helpers
from main only — never from all.py (avoids circulars).
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

from main import get_db, require_user, _require_owned, _build_racial_traits, _build_character_attacks

router = APIRouter()


@router.get("/api/character/{char_id}/pdf")
async def character_pdf(char_id: int, request: Request):
    """Generate a printable D&D character sheet PDF."""
    user = require_user(request)
    db = get_db()
    row = _require_owned(db, user, "characters", char_id)
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Character not found")

    char = dict(row)
    # Build structured data for PDF generator
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pdf_generator import build_char_data, fill_official_sheet
    char_data = build_char_data(row, db, racial_traits=_build_racial_traits(char))

    # Populate Allies & Organizations from character relationships
    try:
        rels = db.execute(
            "SELECT name, relationship_type, description FROM character_relationships WHERE character_id = ? AND user_id = ? ORDER BY created_at DESC",
            (char_id, user["id"])
        ).fetchall()
        if rels:
            rel_lines = []
            existing = str(char_data.get("allies", "") or "").strip()
            if existing:
                rel_lines.append(existing)
            for r in rels:
                rname = r["name"]
                rdesc = (r["description"] or "").strip()
                # Flatten internal paragraph breaks so a single entry
                # doesn't have extra spacing within its own description
                rdesc = rdesc.replace("\n\n", "\n")
                rtype = (r["relationship_type"] or "ally").replace("_", " ").title()
                if rdesc:
                    rel_lines.append(f"{rname} ({rtype}): {rdesc}")
                else:
                    rel_lines.append(f"{rname} ({rtype})")
            char_data["allies"] = "\n\n".join(rel_lines)
    except Exception:
        pass

    # Check if allies text needs an appendix page (with or without relationships)
    try:
        allies_text = str(char_data.get("allies", "") or "")
        if len(allies_text) > 800:
            trunc_at = 700
            for brk in range(trunc_at, 500, -1):
                if allies_text[brk:brk+2] == "\n\n":
                    trunc_at = brk
                    break
            # Appendix gets the full text (some duplication with field is fine)
            char_data["allies_appendix"] = allies_text
            char_data["allies"] = allies_text[:trunc_at] + "\n... See Appendix"
    except Exception:
        pass

    db.close()

    # Rebuild attacks_data from current inventory/equipped + natural weapons
    # (the stored attacks_data may be stale if items were added later)
    fresh_attacks = _build_character_attacks(char_data)
    if fresh_attacks:
        char_data["attacks_data"] = fresh_attacks

    pdf_bytes = fill_official_sheet(char_data)
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{char_data.get("name", "character").replace(" ", "_")}_sheet.pdf"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )
