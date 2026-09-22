/* Shared portrait tile for lists built in the browser.
 *
 * Server-rendered pages use the Jinja macro in templates/_char_portrait.html;
 * this is the JS twin (DM tools' players panel, encounter participants, NPC
 * pickers, ...). Portraits are multi-MB base64 data URLs, so the tile always
 * points at the cacheable image route with a thumbnail size — never at a data:
 * URL.
 *
 * charPortraitTile(id, name, {size, hasPortrait, extraClass, kind})
 *   hasPortrait === false  → initial tile, no request
 *   hasPortrait === true   → <img>, falls back to the initial on load failure
 *   hasPortrait undefined  → <img> with the same onerror fallback
 *   kind === 'npc'         → /api/dm/npc/{id}/portrait-image instead of the
 *                            character route
 *   src                    → render this URL directly (shared reference art)
 */
function charPortraitTile(charId, name, opts) {
  opts = opts || {};
  const size = opts.size || 32;
  const mobile = Math.round(size * 0.86);
  const cls = 'char-portrait' + (opts.extraClass ? ' ' + opts.extraClass : '');
  const style = `--cp-size:${size}px;--cp-size-mobile:${mobile}px`;
  const label = String(name == null ? '?' : name);
  const initial = label.trim().charAt(0).toUpperCase() || '?';
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  // Reference art (shared library: monsters/items/NPCs). The caller passes the
  // full URL; a 404 (image not generated yet) falls back to the initial tile,
  // which is exactly the lazy-fill UX.
  const direct = opts.src || '';
  if (direct) {
    return `<img src="${esc(direct)}" alt="${esc(label)}" loading="lazy" decoding="async"
      class="${cls}" style="${style}" data-cp-fallback="${esc(initial)}"
      onerror="charPortraitFallback(this)">`;
  }
  if (!charId || opts.hasPortrait === false) {
    return `<span class="${cls} char-portrait-empty" style="${style};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${esc(initial)}</span>`;
  }
  const base = opts.kind === 'npc' ? '/api/dm/npc/' : '/api/character/';
  // Two sizes requested: the tile slot is 2x for crispness.
  return `<img src="${base}${charId}/portrait-image?size=${size * 2}" alt="${esc(label)} portrait"
    loading="lazy" decoding="async" class="${cls}" style="${style}"
    data-cp-fallback="${esc(initial)}" onerror="charPortraitFallback(this)">`;
}

/* Swap a failed portrait request for the initial tile (no portrait stored,
   or not visible to this user). */
function charPortraitFallback(img) {
  if (!img || !img.parentNode) return;
  const initial = img.getAttribute('data-cp-fallback') || '?';
  const span = document.createElement('span');
  span.className = img.className.replace(/\s*char-portrait-empty\s*/, ' ') + ' char-portrait-empty';
  span.setAttribute('style', (img.getAttribute('style') || '') +
    ';display:flex;align-items:center;justify-content:center;border-style:dashed;color:var(--text-muted);font-weight:600');
  span.setAttribute('aria-hidden', 'true');
  span.textContent = initial;
  img.parentNode.replaceChild(span, img);
}

/* Full-size reference art for a detail view (bestiary card, item popup, NPC
 * stat card). Floats right so the existing copy keeps its layout. A tile-sized
 * thumbnail is useless here — the point of a detail view is the picture — and a
 * not-yet-generated image hides itself instead of showing a broken glyph,
 * because the heading already names the thing.
 */
function refArtImg(kind, name, px) {
  if (!name) return '';
  const size = px || 256;
  return `<img src="/api/ref-image/${kind}/${encodeURIComponent(name)}?size=${size * 2}"
    alt="${String(name)}" loading="lazy" decoding="async"
    style="float:right;width:${size}px;max-width:40%;height:auto;border-radius:8px;margin:0 0 0.6rem 0.9rem;border:1px solid var(--border)"
    onerror="this.style.display='none'">`;
}

/* Read an <input type=file> pick and downscale it in the browser.
 *
 * Portraits are stored as data URLs in the DB, so an unresized phone photo
 * would add megabytes to every row that references it — the same thing that
 * made the old portraits 1.9-3.4 MB. Downscaling here keeps the stored image
 * to a couple hundred KB while staying sharp at tile sizes (tiles request
 * ?size=<=1024px thumbnails anyway).
 *
 * Returns a Promise<string dataURL>; resolves with '' when there is no file.
 */
function downscaleImageFile(file, maxPx) {
  return new Promise((resolve, reject) => {
    if (!file) { resolve(''); return; }
    const limit = maxPx || 1024;
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('could not read the file'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('that file is not a readable image'));
      img.onload = () => {
        try {
          const scale = Math.min(1, limit / Math.max(img.width, img.height));
          if (scale >= 1 && file.size < 400 * 1024) { resolve(reader.result); return; }
          const canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(img.width * scale));
          canvas.height = Math.max(1, Math.round(img.height * scale));
          canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
          const out = canvas.toDataURL('image/webp', 0.85);
          // Some browsers ignore the webp type and hand back a PNG; accept it
          // only if it is actually smaller than the original.
          if (out.startsWith('data:image/webp') || out.length < String(reader.result).length) {
            resolve(out);
          } else {
            resolve(reader.result);
          }
        } catch (err) { resolve(reader.result); }
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}
