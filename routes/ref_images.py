"""Reference art route: monsters, items and NPCs from the shared library.

  GET /api/ref-image/{kind}/{name}[?size=80]

Serves static/ref-portraits/<kind>/<slug>.webp — a file, not a DB blob — and
409s → 404s into a background generation the first time an entity is viewed, so
the whole library fills in as it is used instead of costing thousands of
generations up front.

Cached hard at the edge: these images are shared by every user and never change
for a given entity. The middleware must not set cookies on this path or
Cloudflare answers BYPASS (see the /static/ exception in main.py).
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from services import ref_portraits
from services.images import thumbnail_bytes

router = APIRouter()


@router.get("/api/ref-image/{kind}/{name}")
async def ref_image(kind: str, name: str, request: Request, size: int = 0):
    from main import require_user

    require_user(request)                     # any signed-in user; the library is shared
    if kind not in ref_portraits.KINDS:
        return Response(status_code=404)

    path = ref_portraits.path_for(kind, name)
    if not path.is_file():
        # Nothing yet: start generating and let the caller keep its letter tile.
        subtitle, snippet, known = "", "", False
        try:
            from services.entity_search import entity_detail
            row = entity_detail(kind, name) or {}
            known = bool(row)
            subtitle = (row.get("subtitle") or "")[:160]
            snippet = (row.get("snippet") or "")[:240]
        except Exception:
            pass                          # a kick without context is fine
        # Only spend a generation on something that actually exists in the index —
        # a typo or a probe name must not cost a free-tier request.
        started = ref_portraits.kick(kind, name, subtitle, snippet) if known else False
        return Response(status_code=404,
                        headers={"Cache-Control": "no-store",
                                 "X-Ref-Image": "generating" if started else "unknown"})

    blob = path.read_bytes()
    # Not 604800. Regenerating a portrait rewrites the same URL, so a week-long cache meant the app
    # kept showing the old image long after the file changed. must-revalidate keeps it cheap.
    headers = {"Cache-Control": "public, max-age=300, must-revalidate", "X-Ref-Image": "ready"}
    if size:
        thumb = thumbnail_bytes(blob, size)
        if thumb:
            blob, media = thumb
            headers["Content-Type"] = media
            return Response(blob, headers=headers)
    headers["Content-Type"] = "image/webp"
    return Response(blob, headers=headers)


@router.get("/api/ref-image-stats")
async def ref_image_stats(request: Request):
    from fastapi.responses import JSONResponse
    from main import require_user
    require_user(request)
    return JSONResponse(ref_portraits.stats())
