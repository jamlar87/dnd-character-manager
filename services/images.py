"""Portrait image helpers shared by the character and NPC routes.

Portraits are stored in the DB as base64 `data:` URLs (a few MB each). Every
read path serves them through an image route and asks for a thumbnail sized for
the slot — see `thumbnail_bytes` and the `?size=` parameter on
/api/character/{id}/portrait-image and /api/dm/npc/{id}/portrait-image.
"""

import threading
from collections import OrderedDict

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


#: Sized variants of the same source image, keyed by (hash of the bytes, size). Portrait art arrives
#: as a data URL in the DB rather than a file, so it cannot be cached next to a path — but re-encoding
#: a 60KB portrait into a 1KB tile on every request is the expensive part, and the bytes never change
#: for a given portrait. Bounded so a library-sized page cannot grow this without limit.
_BLOB_THUMBS: "OrderedDict[tuple[str, int], tuple[bytes, str]]" = OrderedDict()
_BLOB_THUMBS_MAX = 512
_BLOB_THUMBS_LOCK = threading.Lock()


def thumb_for_blob(blob: bytes, size: int) -> tuple[bytes, str] | None:
    """`thumbnail_bytes` with an in-memory cache. Same contract: None means "serve the original"."""
    import hashlib

    key = (hashlib.md5(blob).hexdigest(), int(size))
    with _BLOB_THUMBS_LOCK:
        hit = _BLOB_THUMBS.get(key)
        if hit is not None:
            _BLOB_THUMBS.move_to_end(key)
            return hit
    got = thumbnail_bytes(blob, size)
    if got:
        with _BLOB_THUMBS_LOCK:
            _BLOB_THUMBS[key] = got
            while len(_BLOB_THUMBS) > _BLOB_THUMBS_MAX:
                _BLOB_THUMBS.popitem(last=False)
    return got


def cached_thumb(path, size: int) -> bytes | None:
    """The sized WebP of a file on disk, generated once and read after that.

    Keyed on the source's mtime and size rather than on the URL: regenerating a portrait rewrites the
    same path, so a URL-keyed cache would serve the old image — this way new art is a new key, and the
    stale variant for that size is deleted. `thumbnail_bytes` is deliberately still the primitive:
    this only avoids paying it twice.
    """
    from pathlib import Path

    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    target = path.parent / "thumbs" / f"{path.stem}-{int(size)}-{int(st.st_mtime)}-{st.st_size}.webp"
    try:
        if target.is_file():
            return target.read_bytes()
    except OSError:
        pass                                # unreadable cache entry: re-encode below
    got = thumbnail_bytes(path.read_bytes(), size)
    if not got:
        return None
    blob = got[0]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(blob)
        tmp.replace(target)
        for old in target.parent.glob(f"{path.stem}-{int(size)}-*.webp"):
            if old != target:
                old.unlink()                # only the current key can ever be read again
    except OSError:
        pass                                # serving beats caching
    return blob


def fit_blob(blob: bytes, max_px: int, quality: int = 90) -> tuple[bytes, str, int, int, int, int] | None:
    """Fit a battle map to `max_px` on the long edge — and only if it is actually bigger.

    Deliberately NOT `thumbnail_bytes`: that clamps the request to MAX_SIZE (1024, sized for
    portraits) and always re-encodes. Two things go wrong if a map is quietly squeezed through it:

    1. a 4000px battle map lands at 1024 and reads as a blur when the DM zooms in;
    2. worse, the pixel dimensions CHANGE, so a grid the DM had aligned to the art no longer
       matches it — a 50px grid over a map that was 40 squares across now covers 20.

    So: return the original bytes untouched when it already fits (no generational loss), and
    report the pixel dimensions either way so the caller can keep the grid aligned.

    Returns (bytes, media_type, out_w, out_h, src_w, src_h) — the source dimensions included
    because the caller has to know whether the art was shrunk, and by how much, to keep the grid
    in step with it. None if the image cannot be read at all.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(blob))
        img.load()
    except Exception:
        return None
    w, h = img.size
    cap = max(MIN_SIZE, int(max_px))
    if max(w, h) <= cap:
        return (blob, _media_of(blob), w, h, w, h)
    scale = cap / float(max(w, h))
    tw, th = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    try:
        out = img.convert("RGBA")
        out.thumbnail((tw, th), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="WEBP", quality=quality, method=4)
        resized = buf.getvalue()
    except Exception:
        return (blob, _media_of(blob), w, h, w, h)
    if not resized or len(resized) >= len(blob) * 1.5:
        return (blob, _media_of(blob), w, h, w, h)
    return (resized, "image/webp", out.size[0], out.size[1], w, h)


def _media_of(blob: bytes) -> str:
    """The media type of an image we are passing through unchanged."""
    try:
        import io
        from PIL import Image
        fmt = (Image.open(io.BytesIO(blob)).format or "PNG").lower()
    except Exception:
        return "image/png"
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
            "gif": "image/gif", "bmp": "image/bmp"}.get(fmt, "image/png")


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
