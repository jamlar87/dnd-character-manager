/* PWA wiring: registration, and the install nudge on the players' surface.

   Deliberately small, and every part of it is optional: if the service worker never registers, or
   the browser has no install prompt, the app is exactly what it was. Nothing here may throw its
   way into the page — a failed registration is not an error a player needs to see. */

(function () {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      // updateViaCache: 'none' — the browser must not answer the worker's update check out of its
      // own HTTP cache. Combined with the no-store on /sw.js, a new build reaches devices on the
      // next load instead of whenever a cache happens to expire.
      navigator.serviceWorker.register('/sw.js', {updateViaCache: 'none'}).catch(function () {
        /* offline-first is a bonus, not a requirement */
      });
    });
  }

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const isStandalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
                       navigator.standalone === true;

  function hint() {
    return document.getElementById('installHint');
  }

  function alreadyDismissed() {
    try { return localStorage.getItem('hideInstallHint') === '1'; } catch (e) { return true; }
  }

  let deferredPrompt = null;

  // Chrome/Edge/Android: the prompt exists, so offer a button that uses it.
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    const h = hint();
    if (h && !alreadyDismissed() && !isStandalone) h.style.display = 'flex';
  });

  // iOS fires no such event: the only honest thing to offer is the two taps it takes.
  if (isIOS && !isStandalone && !alreadyDismissed()) {
    document.addEventListener('DOMContentLoaded', function () {
      const h = hint();
      if (!h) return;
      const btn = h.querySelector('[data-install-button]');
      if (btn) btn.textContent = '📲 Share → Add to Home Screen';
      h.style.display = 'flex';
    });
  }

  window.installApp = function () {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    deferredPrompt = null;
    const h = hint();
    if (h) h.style.display = 'none';
  };

  window.dismissInstallHint = function () {
    const h = hint();
    if (h) h.style.display = 'none';
    try { localStorage.setItem('hideInstallHint', '1'); } catch (e) { /* private mode */ }
  };
})();
