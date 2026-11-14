/* Shared DOM/text primitives — one escaper for the whole app.

   Before this file there were five near-copies of "escape HTML":
     layout.js      escapeHtml  (div.textContent -> innerHTML: misses quotes)
     dm_tools.js    dmEsc       (regex, all five metacharacters)
     entity-search.js esc       (div.textContent -> innerHTML: misses quotes)
     char-portrait.js esc       (regex, misses apostrophes)
     sheet.js       inline      (regex, misses quotes)
   The copies drifted, and the ones that missed quotes were one attribute
   interpolation away from a breakout. This is the single implementation:
   every metacharacter that matters inside text OR inside a quoted attribute.

   Loaded as a plain classic script (no bundler), so consumers just call the
   global `escapeHtml` / `escapeAttr`.

   null/undefined render as '' — the call sites interpolate optional fields
   straight from the DB, and "undefined" in markup is never the intent.
*/
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, function (c) {
    switch (c) {
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      default: return '&#39;';
    }
  });
}

// Same escaping rules; the separate name exists so call sites that build an
// attribute value say so, and a future stricter attribute escaper can split off.
var escapeAttr = escapeHtml;

window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
