/* Publishes the DM page's character roster as the COMBAT_CHARACTERS global.
 *
 * The roster used to arrive as `const COMBAT_CHARACTERS = {...};` inside an inline
 * <script>, which a CSP without 'unsafe-inline' blocks. It now ships as a
 * type="application/json" block (routes/dm/page.py already escapes < > so the
 * payload cannot close its own tag) and this file lifts it onto window, which is
 * where static/dm/combat.js reads it.
 */
(function () {
  var node = document.getElementById('combat-characters-data');
  var data = [];
  if (node) {
    try {
      data = JSON.parse(node.textContent || '[]');
    } catch (e) {
      // Malformed roster should not take the whole DM page down.
      data = [];
    }
  }
  window.COMBAT_CHARACTERS = data;
})();
