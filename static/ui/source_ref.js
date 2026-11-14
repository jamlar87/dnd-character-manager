/* One source-reference resolver for the whole app.

   openSourceRef() existed three times (dm_tools.js, sheet.js, create.js) and
   the copies had drifted:
     dm_tools  slug param + empty-map guard + exact/display/substring matches
               + a normalised last-resort match + alert once per unknown source
     sheet     no slug param, no normalised match, silent on failure
     create    slug param, silently returned when the slug map was empty
   Same click, three answers depending on which page you were on. This is the
   union: every match strategy from every copy, one alert, one place to fix.

   The slug map is read at call time, not at load time, because each page
   supplies it differently:
     dm_tools / create : window.SOURCE_SLUG_MAP (source-slug-map.js)
     sheet             : window.SHEETREF.sourceSlugMap
     anything else     : window.SourceRef.map = {...}

   Loaded as a plain classic script; inline onclick handlers still call the
   global openSourceRef(src, slug).
*/
(function () {
  var _missingSourceAlerted = new Set();

  function slugMap() {
    if (window.SourceRef && SourceRef.map) return SourceRef.map;
    if (window.SOURCE_SLUG_MAP) return window.SOURCE_SLUG_MAP;
    if (window.SHEETREF && window.SHEETREF.sourceSlugMap) return window.SHEETREF.sourceSlugMap;
    return null;
  }

  function openAt(slug, page) {
    window.open('/api/reference/open/' + slug + (page ? '?page=' + page + '#page=' + page : ''), '_blank');
  }

  function norm(s) { return String(s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

  function openSourceRef(src, slug) {
    if (!src || src.startsWith('SRD')) return;

    var pageMatch = String(src).match(/\b[pP]\.?\s*(\d+)/);
    var page = pageMatch ? parseInt(pageMatch[1], 10) : 0;

    // A known slug needs no matching at all.
    if (slug) { openAt(slug, page); return; }

    var map = slugMap();
    // Pages without a slug map (or with it not yet loaded) cannot resolve
    // anything; staying silent beats an alert on every reference.
    if (!map) return;

    var bookPart = String(src).replace(/\s+[pP]\.?\s*\d+.*$/, '').replace(/\s+\d{4}$/, '').trim();
    var b = bookPart.toLowerCase();
    var entries = Object.keys(map).map(function (slugKey) { return [slugKey, map[slugKey] || {}]; });

    var strategies = [
      function (key, info) { return key.toLowerCase() === b; },
      function (key, info) { return String(info.display || '').toLowerCase() === b; },
      function (key, info) {
        var d = String(info.display || '').toLowerCase();
        return !!d && !!b && (d.indexOf(b) !== -1 || b.indexOf(d) !== -1);
      },
      function (key, info) {
        var d = norm(info.display || '');
        var bn = norm(bookPart);
        return !!d && !!bn && (d.indexOf(bn) !== -1 || bn.indexOf(d) !== -1);
      }
    ];

    for (var i = 0; i < strategies.length; i++) {
      for (var j = 0; j < entries.length; j++) {
        var key = entries[j][0], info = entries[j][1];
        if (strategies[i](key, info)) { openAt(key, page); return; }
      }
    }

    // Alert once per source instead of on every click.
    if (!_missingSourceAlerted.has(src)) {
      _missingSourceAlerted.add(src);
      alert('📚 Could not find the source book for:\n"' + src + '"\n\nIt may reference a book not in the library.');
    }
  }

  window.openSourceRef = openSourceRef;
  window.SourceRef = {
    open: openSourceRef,
    map: null,
    reset: function () { _missingSourceAlerted.clear(); }
  };
})();
