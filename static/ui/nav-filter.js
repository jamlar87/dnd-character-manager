/* Global nav: manual-source filter on the search bar (every page).
 *
 * Moved out of layout.html — inline script is what a CSP without
 * 'unsafe-inline' forbids, and this block was identical on every page anyway.
 * Runs after source-filter.js (loaded just above it in layout.html).
 */
(function () {
  document.addEventListener('DOMContentLoaded', function () {
    if (!window.SourceFilter) return;
    var host = document.getElementById('navManualFilter');
    if (!host) return;
    window.SourceFilter.init(host, {
      key: 'nav-manuals',
      compact: true,
      onChange: function () {
        var inp = document.getElementById('manualSearchInput');
        if (inp && inp.value.trim().length >= 2) window.searchManuals(false);
      }
    });
  });
})();
