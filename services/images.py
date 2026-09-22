"""Portrait image helpers shared by the character and NPC routes.

Portraits are stored in the DB as base64 `data:` URLs (a few MB each). Every
read path serves them through an image route and asks for a thumbnail sized for
the slot — see `thumbnail_bytes` and the `?size=` parameter on
/api/character/{id}/portrait-image and /api/dm/npc/{id}/portrait-image.
"""

# A single portrait must not be able to bloat the DB (and every payload that
# touches the row). Generous: a 4000x4000 PNG is ~8 MB.
MAX_PORTRAIT_BYTES = 12 * 1024 * 1024
MIN_SIZE = 16
MAX_SIZE = 1024


def decode_data_url(src: str) -> tuple[bytes, str] | None:
    """('data:image/png;base64,...') -> (bytes, media_type), or None if unparseable."""
    import base64
    if not src or not src.startswith("data:"):
        return None
    try:
        header, b64 = src.split(",", 1)
        media = header[5:].split(";")[0] or "image/png"
        return base64.b64decode(b64), media
    except Exception:
        return None


def thumbnail_bytes(blob: bytes, size: int) -> tuple[bytes, str] | None:
    """Downscale an image to at most `size` px on the long edge (WebP).

    Returns None (rather than raising) whenever it can't help: Pillow missing,
    odd bytes, or the re-encode came out no smaller. Callers then serve the
    original, so a thumbnail request can never break an image.
    """
    try:
        size = max(MIN_SIZE, min(int(size), MAX_SIZE))
    except (TypeError, ValueError):
        return None
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(blob))
        img.load()
        img = img.convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=82, method=4)
        out = buf.getvalue()
        return (out, "image/webp") if out and len(out) < len(blob) else None
    except Exception:
        return None


def portrait_payload_error(value) -> str | None:
    """Validate a portrait value before storing it. None = acceptable.

    `data:` URLs are size-checked (a multi-MB blob is stored verbatim otherwise);
    plain http(s) URLs are allowed as references; anything else is rejected.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return "portrait_url must be a string"
    v = value.strip()
    if not v:
        return None
    if v.startswith("data:"):
        if len(v) > MAX_PORTRAIT_BYTES * 4 // 3 + 1024:
            mb = MAX_PORTRAIT_BYTES / (1024 * 1024)
            return f"portrait is too large (limit {mb:.0f} MB of image data)"
        if decode_data_url(v) is None:
            return "portrait data URL is not decodable"
        if not decode_data_url(v)[0]:
            return "portrait data URL contains no image data"
        return None
    if v.startswith(("http://", "https://", "/")):
        return None
    return "portrait_url must be a data: URL or a http(s) URL"


def normalize_portrait(value, max_px: int = 1024) -> tuple[str, str | None]:
    """Validate AND shrink a portrait before it reaches the DB — one entry point.

    Every write path (character upload, AI generation, NPC create/update, the
    create wizard, NPC→character builds) funnels through here so the stored
    value is always: empty, a http(s) reference, or a downscaled data URL. The
    sheet's older uploads stored 1.9-3.4 MB originals because nothing checked;
    a phone photo would do the same today.

    Returns (value_to_store, error). On error the value is '' — callers must
    reject the request rather than store it.
    """
    err = portrait_payload_error(value)
    if err:
        return "", err
    if not isinstance(value, str):
        return "", None
    v = value.strip()
    if not v:
        return "", None
    if not v.startswith("data:"):
        return v, None                      # external URL — nothing to shrink
    decoded = decode_data_url(v)
    if not decoded:
        return "", "portrait data URL is not decodable"
    blob, _media = decoded
    thumb = thumbnail_bytes(blob, max_px)
    if not thumb:
        return v, None                      # already small — keep the original
    import base64
    out, media = thumb
    return f"data:{media};base64,{base64.b64encode(out).decode()}", None
