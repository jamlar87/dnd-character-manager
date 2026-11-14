/* Shared fetch wrapper: CSRF header, JSON bodies, honest errors.

   The app has ~130 hand-rolled fetch() calls, and each one re-decided the
   Content-Type, the CSRF token and what to do with a non-2xx response. The
   security middleware in main.py answers a write without `x-csrf-token` with
   403 {"error": "CSRF token required"} — a call site that forgot the header
   looked like a mysterious failure. This helper cannot forget it.

   api(url, opts)
     opts.method   default POST when a body is given, else GET
     opts.body     a plain object/array -> JSON-encoded + Content-Type set;
                   string/FormData/URLSearchParams/Blob pass through untouched
     opts.headers  plain object or Headers, merged with the CSRF header
     returns       the parsed JSON body (or text for a non-JSON response,
                   or null for 204)
     throws        Error with .status, .data and .response on any non-2xx,
                   message taken from the response's error/detail field

   Additive on purpose: untouched call sites keep using raw fetch(). layout.js
   still patches window.fetch for the CSRF header, so both paths work.
*/
(function () {
  function csrfToken() {
    if (typeof document === 'undefined' || !document.cookie) return '';
    var parts = document.cookie.split('; ');
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf('csrf_token=') === 0) {
        try { return decodeURIComponent(parts[i].slice('csrf_token='.length)); }
        catch (e) { return parts[i].slice('csrf_token='.length); }
      }
    }
    return '';
  }

  function isPassThroughBody(body) {
    if (body === undefined || body === null) return true;
    if (typeof body === 'string') return true;
    if (typeof FormData !== 'undefined' && body instanceof FormData) return true;
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return true;
    if (typeof Blob !== 'undefined' && body instanceof Blob) return true;
    if (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) return true;
    return false;
  }

  function makeError(res, data) {
    var msg = '';
    if (data && typeof data === 'object') {
      msg = data.error || data.detail || data.message || '';
      if (typeof msg !== 'string') msg = '';   // FastAPI validation detail is a list
    } else if (typeof data === 'string' && data.trim()) {
      // An HTML error page: quote a bounded slice, never the whole document.
      msg = data.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
    }
    if (!msg) msg = 'Request failed (HTTP ' + res.status + ')';
    var err = new Error(msg);
    err.name = 'ApiError';
    err.status = res.status;
    err.data = data;
    err.response = res;
    return err;
  }

  async function api(url, opts) {
    opts = opts || {};
    var method = String(opts.method || (opts.body === undefined ? 'GET' : 'POST')).toUpperCase();
    var headers = new Headers(opts.headers || {});
    var body = opts.body;

    if (!isPassThroughBody(body)) {
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
      body = JSON.stringify(body);
    }

    var isWrite = ['GET', 'HEAD', 'OPTIONS'].indexOf(method) === -1;
    if (isWrite && !headers.has('X-CSRF-Token')) {
      var token = csrfToken();
      if (token) headers.set('X-CSRF-Token', token);
    }

    var init = {
      method: method,
      headers: headers,
      credentials: opts.credentials || 'same-origin'
    };
    if (opts.cache) init.cache = opts.cache;
    if (body !== undefined && body !== null) init.body = body;
    if (opts.signal) init.signal = opts.signal;

    var res = await fetch(url, init);

    var data = null;
    if (res.status !== 204 && res.status !== 205) {
      var ct = (res.headers.get('content-type') || '').toLowerCase();
      if (ct.indexOf('json') !== -1) {
        data = await res.json().catch(function () { return null; });
      } else {
        data = await res.text().catch(function () { return ''; });
      }
    }

    if (!res.ok) throw makeError(res, data);
    return data;
  }

  api.csrfToken = csrfToken;
  window.api = api;
  window.ApiError = makeError;  // exposed so call sites can type-check failures
})();
