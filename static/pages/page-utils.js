/* Small helpers shared by the ES-module page entry points.
 *
 * readConfig: pulls a JSON blob a template rendered for the page. Modules
 * cannot read Jinja, so pages hand their server data over as
 * `<script type="application/json" id="...">` — parseable and CSP-safe.
 *
 * fail: one place to surface an api() rejection. api() throws an Error with
 * .message taken from the server's error field, so the message is already
 * user-readable; this only decides where it goes.
 */
export function readConfig(elementId) {
  const node = document.getElementById(elementId);
  if (!node) {
    console.warn('[page] missing config block #' + elementId);
    return {};
  }
  try {
    return JSON.parse(node.textContent || '{}');
  } catch (err) {
    console.error('[page] bad JSON in #' + elementId, err);
    return {};
  }
}

export function fail(err) {
  const msg = (err && err.message) || 'Request failed';
  console.error('[page]', msg, err);
  if (typeof alert === 'function') alert(msg);
}
