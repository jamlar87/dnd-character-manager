/* Keep the screen awake while the app is open (tablet / phone use at the table).
 *
 * Moved out of layout.html: a Screen Wake Lock needs no server data, so it has
 * no business in a template, and the browser only grants it inside a user
 * gesture / visible document — which is what the visibilitychange retry is for.
 */
(function () {
  var wakeLock = null;

  function requestWakeLock() {
    if (!navigator.wakeLock || !navigator.wakeLock.request) return;
    navigator.wakeLock.request('screen').then(function (lock) {
      wakeLock = lock;
      lock.addEventListener('release', function () { wakeLock = null; });
    }).catch(function () {
      // API unsupported or denied — the screen simply sleeps as usual.
    });
  }

  document.addEventListener('visibilitychange', function () {
    // Android releases the lock when the tab is hidden; take a fresh one.
    if (document.visibilityState === 'visible') requestWakeLock();
  });

  requestWakeLock();
})();
