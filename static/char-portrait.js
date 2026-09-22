/* Shared character portrait tile for lists built in the browser.
 *
 * Server-rendered pages use the Jinja macro in templates/_char_portrait.html;
 * this is the JS twin (DM tools' players panel, encounter participants, ...).
 * Portraits are multi-MB base64 data URLs, so the tile always points at the
 * cacheable image route with a thumbnail size — never at a data: URL.
 *
 * charPortraitTile(id, name, {size, hasPortrait, extraClass})
 *   hasPortrait === false  → initial tile, no request
 *   hasPortrait === true   → <img>, falls back to the initial on load failure
 *   hasPortrait undefined  → <img> with the same onerror fallback
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
  if (!charId || opts.hasPortrait === false) {
    return `<span class="${cls} char-portrait-empty" style="${style};font-size:${Math.round(size * 0.42)}px" aria-hidden="true">${esc(initial)}</span>`;
  }
  // Two sizes requested: the tile slot is 2x for crispness.
  return `<img src="/api/character/${charId}/portrait-image?size=${size * 2}" alt="${esc(label)} portrait"
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
