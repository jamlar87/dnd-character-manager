"""Cross-cutting DM endpoints: manual search/summary and live HP changes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
import json
from core.security import _is_admin, require_user
from core.db import get_db
from routes.characters import _search_manuals, parse_source_filter
from ._shared import call_ai

router = APIRouter()


# ── DM Tools: Manual Search ───────────────────────────────────────────────
@router.post("/api/dm/search-manuals", response_class=JSONResponse)
async def dm_search_manuals(request: Request):
    """Full-text search across all D&D reference manuals (cached PDF text)."""
    require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"results": [], "error": "Query too short"})

    # rg + file reads are synchronous: run them off the event loop, or one
    # search stalls every request in the app for ~1 s.
    from starlette.concurrency import run_in_threadpool
    results = await run_in_threadpool(_search_manuals, query, 25,
                                      sources=parse_source_filter(data.get("source")))
    return JSONResponse({"results": results, "query": query, "total": len(results),
                         "sources": sorted(parse_source_filter(data.get("source")))})

@router.post("/api/dm/search-manuals/summarize", response_class=JSONResponse)
async def dm_search_manuals_summarize(request: Request):
    """AI-powered research summary: search manuals, then distill with LLM."""
    require_user(request)
    data = await request.json()
    query = (data.get("query", "") or "").strip()
    if not query or len(query) < 2:
        return JSONResponse({"summary": "", "error": "Query too short"})

    # rg + file reads are synchronous: run them off the event loop, or one
    # search stalls every request in the app for ~1 s.
    from starlette.concurrency import run_in_threadpool
    results = await run_in_threadpool(_search_manuals, query, 30,
                                      sources=parse_source_filter(data.get("source")))

    if not results:
        return JSONResponse({"summary": "No matches found across any reference manuals.", "results": []})

    # Build a research context from the search results
    context_blocks = []
    for r in results:
        page_str = f" (est. p.{r['page']})" if r.get("page") else ""
        context_blocks.append(f"[{r['book']}{page_str}] {r['snippet']}")
    research_text = "\n\n".join(context_blocks[:4000])  # cap for token budget

    ai_prompt = f"""You are a D&D 5e rules researcher. The user searched the reference manuals for:
\"{query}\"

Below are the relevant excerpts found across the manuals. Synthesize them into a comprehensive, well-organized summary. Include:
1. A clear, concise answer to what was searched
2. Key rules, mechanics, and page references where available
3. Any nuances, edge cases, or related rules
4. Differences between sources if applicable (e.g. PHB vs DMG vs XGE)

Format the response in plain text with clear section breaks. Keep it grounded in the excerpts — don't invent rules not present.

RESEARCH EXCERPTS:
{research_text}

RULES SUMMARY:"""

    summary = await call_ai(prompt=ai_prompt, label="manual-summary")
    if not summary:
        # Fallback: return raw results without AI
        return JSONResponse({"summary": None, "results": results, "note": "AI unavailable — raw results shown"})

    return JSONResponse({"summary": summary.strip(), "results": results, "query": query})

# ── AI Summary PDF ─────────────────────────────────────────────────────────
@router.post("/api/ai/summary/pdf", response_class=Response)
async def ai_summary_pdf(request: Request):
    """Generate a printable PDF of an AI manual search summary."""
    require_user(request)
    data = await request.json()
    query = data.get("query", "Manual Search")
    summary = data.get("summary", "")
    sources = data.get("sources", [])

    if not summary:
        return JSONResponse({"error": "No summary provided"}, status_code=400)

    from pdf_generator import generate_ai_summary_pdf
    pdf_bytes = generate_ai_summary_pdf(query, summary, sources)

    filename = query.replace(" ", "_")[:40] + "_ai_summary.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-cache",
        }
    )

@router.get("/api/dm/user-characters", response_class=JSONResponse)
def dm_user_characters(request: Request):
    """List user's characters (for campaign party picker)."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT id, name, race, class_name, level, subclass FROM characters WHERE user_id = ? ORDER BY name",
        (user["id"],)
    ).fetchall()]
    db.close()
    return JSONResponse({"characters": rows})

@router.get("/api/dm/characters-for-combat", response_class=JSONResponse)
def dm_characters_for_combat(request: Request):
    """List all characters with full combat stats for Quick Add."""
    user = require_user(request)
    db = get_db()
    rows = [dict(r) for r in db.execute("""
        SELECT id, name, race, subrace, class_name, level, subclass,
               hp_max, hp_current, ac, strength, dexterity, constitution,
               intelligence, wisdom, charisma, speed, proficiency_bonus
        FROM characters WHERE user_id = ? ORDER BY name
    """, (user["id"],)).fetchall()]
    db.close()
    return JSONResponse({"characters": rows})

@router.post("/api/dm/character-hp", response_class=JSONResponse)
async def dm_character_hp(request: Request):
    """Set a character's HP from the DM side, for a character their campaign holds."""
    user = require_user(request)
    data = await request.json()
    try:
        char_id = int(data.get("character_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad character_id"}, status_code=400)
    if not char_id or data.get("hp_current") is None:
        return JSONResponse({"error": "character_id and hp_current are required"}, status_code=400)
    try:
        hp = max(0, min(int(data["hp_current"]), 9999))
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad hp_current"}, status_code=400)

    db = get_db()
    try:
        char = db.execute("SELECT id, name, user_id, hp_current, concentration FROM characters WHERE id = ?",
                          (char_id,)).fetchone()
        if not char:
            return JSONResponse({"error": "Character not found"}, status_code=404)
        if not _is_admin(user) and char["user_id"] != user["id"]:
            # Authority the DM does have: the character is on a roster of one of their campaigns.
            from routes.table import campaign_for_character

            camp = campaign_for_character(db, char_id)
            if not camp or camp.get("user_id") != user["id"]:
                return JSONResponse({"error": "Character not found"}, status_code=404)
        db.execute("UPDATE characters SET hp_current = ? WHERE id = ?", (hp, char_id))
        from services.undo import record_safe

        record_safe(db, user["id"], "hp", f"{char['name']}: HP {char['hp_current']} → {hp}",
                    [{"character_id": char_id,
                      "before": {"hp_current": char["hp_current"]},
                      "after": {"hp_current": hp}}])
        db.commit()
    finally:
        db.close()

    # The player's sheet, and every other screen, hears about it through the channel that exists —
    # and a concentrating character hears the DC of the save this damage just forced.
    from routes.table import publish_hp_change

    publish_hp_change(char_id, char["hp_current"], hp, concentration=char["concentration"] or "")
    return JSONResponse({"ok": True, "character_id": char_id, "hp_current": hp})

@router.post("/api/dm/summon-hp", response_class=JSONResponse)
async def dm_summon_hp(request: Request):
    """Set one of a character's summons' HP from the DM side, same campaign authority as above."""
    user = require_user(request)
    data = await request.json()
    try:
        char_id = int(data.get("character_id") or 0)
        idx = int(data.get("summon_idx"))
        hp = max(0, min(int(data.get("hp_current")), 99999))
    except (TypeError, ValueError):
        return JSONResponse({"error": "character_id, summon_idx and hp_current are required"},
                            status_code=400)

    db = get_db()
    try:
        char = db.execute("SELECT id, user_id, summons FROM characters WHERE id = ?",
                          (char_id,)).fetchone()
        if not char:
            return JSONResponse({"error": "Character not found"}, status_code=404)
        if not _is_admin(user) and char["user_id"] != user["id"]:
            from routes.table import campaign_for_character

            camp = campaign_for_character(db, char_id)
            if not camp or camp.get("user_id") != user["id"]:
                return JSONResponse({"error": "Character not found"}, status_code=404)
        try:
            summons = json.loads(char["summons"] or "[]")
        except (json.JSONDecodeError, TypeError):
            summons = []
        if idx < 0 or idx >= len(summons):
            return JSONResponse({"error": "Invalid summon index"}, status_code=400)
        summons_before = json.dumps(summons)
        summons[idx]["hp_current"] = hp
        summons_after = json.dumps(summons)
        db.execute("UPDATE characters SET summons=? WHERE id=?", (summons_after, char_id))
        from services.undo import record_safe

        record_safe(db, user["id"], "summon", f"summon {idx + 1} HP → {hp}",
                    [{"character_id": char_id,
                      "before": {"summons": summons_before},
                      "after": {"summons": summons_after}}])
        db.commit()
    finally:
        db.close()

    from routes.table import publish_character_state

    publish_character_state(char_id, kind="sheet")      # summons ride the 'sheet' kind
    return JSONResponse({"ok": True, "character_id": char_id, "summon_idx": idx, "hp_current": hp})
