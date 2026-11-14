/* Small adapters for markup whose behaviour is too data-heavy for a data-call
 * token list. Loaded on every page right after ui/events.js, so the names here
 * are what data-action="..." resolves to via window.
 */
(function () {
  'use strict';

  // Markup that only needs data-call ops (stop / hide / toggleClass) still
  // names a handler so the dispatcher has something to resolve.
  function noop() {}

  // --- global search bar -------------------------------------------------
  function searchManualsKey(ev) {
    if (!ev || ev.key !== 'Enter') return;
    ev.preventDefault();
    if (window.searchManuals) window.searchManuals();
  }

  function flashAndSearch(el, all) {
    var before = el.style.background;
    el.style.background = 'var(--success)';
    setTimeout(function () { el.style.background = before; }, 300);
    if (window.searchManuals) window.searchManuals(!!all);
  }

  function flashAndSearchAll(el) { flashAndSearch(el, true); }

  function toggleNav(el) {
    var links = document.querySelector('.nav-links');
    if (links) links.classList.toggle('open');
    el.textContent = el.textContent === '☰' ? '✕' : '☰';
  }

  // --- dashboard ---------------------------------------------------------
  function toggleParentCollapsed(el) {
    if (el && el.parentElement) el.parentElement.classList.toggle('collapsed');
  }

  function openCharacter(id) {
    window.location.href = '/character/' + id;
  }

  function importCharacterFromInput(el) {
    if (el && el.files && el.files[0] && window.importCharacterFile) {
      window.importCharacterFile(el.files[0]);
    }
    if (el) el.value = '';
  }

  // --- create page -------------------------------------------------------
  function filterRaceOptions(el) {
    if (window.filterOptions) window.filterOptions(el, ['race-grid', 'subrace-grid']);
  }

  function filterClassOptions(el) {
    if (window.filterOptions) window.filterOptions(el, ['class-grid', 'subclass-grid']);
  }

  function setCustomBackground(el) {
    if (window.state) window.state.custom_background = el.value;
  }

  // --- character sheet ---------------------------------------------------
  function openCharacterPdf(id) {
    window.open('/api/character/' + id + '/pdf?_=' + Date.now(), '_blank');
  }

  function toggleReactionsBody(btn) {
    var body = document.getElementById('reactions-body');
    if (!body) return;
    var arrow = btn.querySelector('.toggle-arrow');
    var open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    if (arrow) arrow.style.transform = open ? '' : 'rotate(90deg)';
  }

  function toggleRxArrow(btn) {
    var body = btn.nextElementSibling;
    if (!body) return;
    var open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    var arrow = btn.querySelector('.rx-arrow');
    if (arrow) arrow.textContent = open ? '▶' : '▼';
  }

  function toggleLocArrow(btn) {
    var body = btn.nextElementSibling;
    if (!body) return;
    body.style.display = body.style.display === 'none' ? '' : 'none';
    var arrow = btn.querySelector('.lc-arrow');
    if (arrow) arrow.classList.toggle('rotated');
  }

  function toggleCoreOnlyFilter(el) {
    el = el || null;
    var on = !window._coreOnly;
    window._coreOnly = on;
    try { localStorage.setItem('coreOnly', on); } catch (err) { /* private mode */ }
    if (el) {
      el.style.color = on ? 'var(--accent)' : 'var(--text-muted)';
      var mark = el.querySelector('span');
      if (mark) mark.textContent = on ? '☑' : '☐';
    }
    if (window.syncCoreToSpellToggle) window.syncCoreToSpellToggle();
  }

  window.noop = noop;
  window.searchManualsKey = searchManualsKey;
  window.flashAndSearch = flashAndSearch;
  window.flashAndSearchAll = flashAndSearchAll;
  window.toggleNav = toggleNav;
  window.toggleParentCollapsed = toggleParentCollapsed;
  window.openCharacter = openCharacter;
  window.importCharacterFromInput = importCharacterFromInput;
  window.filterRaceOptions = filterRaceOptions;
  window.filterClassOptions = filterClassOptions;
  window.setCustomBackground = setCustomBackground;
  window.openCharacterPdf = openCharacterPdf;
  window.toggleReactionsBody = toggleReactionsBody;
  window.toggleRxArrow = toggleRxArrow;
  window.toggleLocArrow = toggleLocArrow;
  window.toggleCoreOnlyFilter = toggleCoreOnlyFilter;
})();
