/* The app shell, cached so a phone at a table with bad wifi still opens — and nothing else.

   The rules here are about what a service worker must NOT do to a live table tool:

   - Writes are never touched. A roll, a sheet save or a tracker HP change that appears to succeed
     offline would be a lie; failing loudly is correct.
   - /api/ is never intercepted, and neither is anything with "stream" in the path. The table
     channel is an EventSource: a worker sitting in front of a long-lived stream is exactly how a
     live table goes quiet with no error. Streams bypass this file entirely.
   - Static assets are content-versioned by ?v=<sha1> (see static_asset_version in main.py), so
     cache-first is safe: a new build asks for a different URL and misses by construction.
   - Navigations are network-first, and never cached. A sheet is live state and a cached sheet is a
     stale lie; offline they fall back to /offline, which says so.

   CACHE is versioned: changing these rules or the shell list means bumping it, and activate
   deletes every cache that is not the current one.
*/

const CACHE = 'dnd-shell-v2';
const OFFLINE = '/offline';
const SHELL = [OFFLINE, '/static/icons/icon-192.png'];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE)
      // one at a time: addAll is atomic, so a single missing URL would leave no shell at all
      .then(function (cache) {
        return Promise.all(SHELL.map(function (url) {
          return cache.add(url).catch(function () {});
        }));
      })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; })
          .map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  const req = event.request;
  if (req.method !== 'GET') return;                 // never in the path of a write

  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;  // only this app's own bytes

  // live traffic: the API in general, and any stream (EventSource) in particular
  if (url.pathname.indexOf('/api/') === 0 || url.pathname.indexOf('stream') !== -1) return;

  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      caches.match(req).then(function (hit) {
        if (hit) return hit;
        return fetch(req).then(function (res) {
          if (res && res.ok && res.type === 'basic') {
            const copy = res.clone();
            caches.open(CACHE).then(function (c) { return c.put(req, copy); }).catch(function () {});
          }
          return res;
        });
      })
    );
    return;
  }

  if (req.mode === 'navigate') {
    // cache: 'no-store' is load-bearing. Without it fetch() may be answered by the browser's own
    // HTTP cache, so a "live" page can come back stale and — worse — a device with no network can
    // be handed a cached page instead of the offline notice. A live sheet is never served from
    // any cache, and this is what makes the failure real enough to fall back from.
    event.respondWith(
      fetch(req, {cache: 'no-store'}).catch(function () { return caches.match(OFFLINE); })
    );
  }
});
