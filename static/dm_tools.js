
// Counting words: "1 players" and "1 quests" read as bugs. One helper, used wherever a
// number is next to a noun.
function plural(n, word) {
  const v = Number(n) || 0;
  return `${v} ${word}${v === 1 ? '' : 's'}`;
}

// ── Source reference: click 📚 badge → open PDF ──
/* SOURCE_SLUG_MAP and NAMED_ITEM_TYPES set by template */  // server-side weapon/armor classification
// openSourceRef() lives in static/ui/source_ref.js. It reads window.SOURCE_SLUG_MAP,
// matches exact slug → display name → substring, and alerts once per unknown source.

// ── Tab switching (persisted to localStorage) ──
function activateTab(tabName) {
  document.querySelectorAll('.dm-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.dm-panel').forEach(p => p.classList.remove('active'));
  const tabBtn = document.querySelector(`.dm-tab[data-tab="${tabName}"]`);
  const panel = document.getElementById('panel-' + tabName);
  if (tabBtn) tabBtn.classList.add('active');
  if (panel) panel.classList.add('active');
  localStorage.setItem('dmToolsTab', tabName);
  // Showing a tab loads it, whoever asked. The map list is fetched on demand rather than shipped in
  // the page, and doing that only from the tab's click handler left a direct load — the restore below,
  // or a bookmark — staring at "Loading maps…" until the tab was clicked away and back.
  if (tabName === 'maps') renderMaps();
}

// Restore last active tab on page load.
//
// Deferred to a task, not run inline: this block sits near the top of the file and the tab it
// restores may load content, which reaches state declared with `let` much further down. Reading that
// state before the declaration is evaluated is a temporal-dead-zone ReferenceError — and because the
// loader is `async`, the failure arrives as a rejected promise with no console error and no fallback,
// leaving the tab's static "Loading maps…" on screen forever. Deferring past the script's own
// evaluation makes the order irrelevant.
setTimeout(function() {
  try {
    const saved = localStorage.getItem('dmToolsTab');
    const validTabs = ['campaigns','maps','encounters','combat','monsters','spells','npcs','items','traps','manuals'];
    const tab = validTabs.includes(saved) ? saved : 'campaigns';
    activateTab(tab);
  } catch(e) {
    activateTab('campaigns');
  }
}, 0);

document.querySelectorAll('.dm-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    activateTab(tab.dataset.tab);   // which loads whatever that tab needs
  });
});

// ── Modal helpers ──
let _modalZBase = 1000;
function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  _modalZBase += 10;
  el.style.zIndex = _modalZBase;
  el.classList.add('open');
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('open');
}

function toggleCollapse(header) {
  const arrow = header.querySelector('.collapse-arrow');
  const body = header.nextElementSibling;
  if (!body || !body.classList.contains('collapse-body')) return;
  const isOpen = !body.classList.contains('hidden');
  if (isOpen) {
    body.classList.add('hidden');
    arrow.classList.remove('open');
  } else {
    body.classList.remove('hidden');
    arrow.classList.add('open');
  }
}
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', (e) => { if (e.target === m) m.classList.remove('open'); });
});

// ── Character Sheet Preview ──
function previewCharSheet(charId, charName) {
  document.getElementById('charSheetTitle').textContent = charName || 'Character Sheet';
  document.getElementById('charSheetFrame').src = `/character/${charId}?dm_preview=1`;
  openModal('charSheetModal');
}

// ── Core Content Toggle (DM Tools) ──
let _coreOnlyDM = localStorage.getItem('coreOnly') === 'true';
const _CORE_BOOKS_DM = ['phb','phb 2014','dmg','dmg 2014','mm','monster manual','xge','xanathar','tce','tasha','tce 2020','vgm','volo','mtf','mordenkainen','scag','sword coast','erlw','eberron','ftd','fizban','mpmm','mordenkainen presents','monsters of the multiverse','bpgg','bigby','glory of the giants'];
function isCoreSourceDM(src) {
  if (!src || src === 'SRD' || src.startsWith('SRD')) return true;
  const lower = src.toLowerCase();
  // Extract abbreviation before " p." if present
  const abbr = lower.split(' p.')[0].trim();
  for (const b of _CORE_BOOKS_DM) {
    if (abbr === b || abbr.startsWith(b) || lower.includes(b)) return true;
  }
  return false;
}
function toggleCoreDM() {
  _coreOnlyDM = !_coreOnlyDM;
  localStorage.setItem('coreOnly', _coreOnlyDM);
  document.getElementById('dm-core-box').textContent = _coreOnlyDM ? '☑' : '☐';
  document.getElementById('dm-core-toggle').style.color = _coreOnlyDM ? 'var(--accent)' : 'var(--text-muted)';
  // Re-filter whichever panel is active
  const active = document.querySelector('.dm-panel.active');
  if (active) {
    if (active.id === 'panel-monsters') filterMonsters();
    else if (active.id === 'panel-spells') filterSpells();
    else if (active.id === 'panel-traps') filterTraps();
    else if (active.id === 'panel-items' && _itemsCampId) loadAllItems();
    else if (active.id === 'panel-combat') filterCombatCreatures();
  }
  // Also re-filter encounter builder palette if visible
  if (typeof filterCreaturePalette === 'function') filterCreaturePalette();
}
// Init toggle UI from localStorage
if (_coreOnlyDM) {
  document.getElementById('dm-core-box').textContent = '☑';
  document.getElementById('dm-core-toggle').style.color = 'var(--accent)';
}

// ── Monster filtering ──
// ── Book-source scope (window.SourceFilter, static/source-filter.js) ──
function _srcOk(src, key) {
  return !window.SourceFilter || SourceFilter.matches(src || '', SourceFilter.slugs(key));
}

// ── Monster cards ────────────────────────────────────────────────────────────
// The library is ~2,600 monsters. It used to be server-rendered on every DM
// tools load (~3 MB of HTML); the cards now come from /static/dm-monsters.js,
// which the browser and the edge cache. Markup + data-* attributes match what
// the template used to emit, so filterMonsters() is unchanged.
// Shared implementation in static/ui/dom.js — one escaper for the app.
var dmEsc = window.escapeHtml;

// ── Delegated actions for player-owned names ────────────────────────────────
// Character and item names are player-supplied. They used to be interpolated into inline
// handlers — onclick="previewCharSheet(${ch.id}, '${ch.name}')" — so a name containing a
// quote closed the attribute and executed on click. Names now travel in dmEsc'd data-*
// attributes and one listener per event type dispatches to the same functions.
document.addEventListener('click', function(e) {
  const actionEl = e.target.closest('[data-char-action]');
  if (actionEl) {
    const handlers = {
      previewCharSheet: previewCharSheet,
      loadCharacterSummons: loadCharacterSummons,
      openSummonModalDM: openSummonModalDM,
    };
    const fn = handlers[actionEl.dataset.charAction];
    if (fn) {
      e.stopPropagation();
      const args = [Number(actionEl.dataset.charId), actionEl.dataset.charName || ''];
      if (actionEl.dataset.charLevel !== undefined) args.push(Number(actionEl.dataset.charLevel));
      fn.apply(null, args);
      return;
    }
  }
  const npcEl = e.target.closest('[data-npc-action]');
  if (npcEl) {
    e.stopPropagation();
    showNpcInfo(Number(npcEl.dataset.npcId), npcEl.dataset.npcName || '');
    return;
  }
  // Monster index is a catalog slug: keep it in a data attribute so a malformed entry
  // can't break out of a handler string.
  const monsterEl = e.target.closest('[data-monster-action]');
  if (monsterEl) {
    e.stopPropagation();
    if (monsterEl.dataset.monsterAction === 'showMonster') {
      showMonster(monsterEl.dataset.monsterIndex || '');
      return;
    }
  }
  const addCreatureEl = e.target.closest('[data-add-creature-enc]');
  if (addCreatureEl) {
    e.stopPropagation();
    addCreatureToEncounter(Number(addCreatureEl.dataset.addCreatureEnc),
                           Number(addCreatureEl.dataset.addCreatureIdx));
    return;
  }
  // Condition badges: names come from a free-text "custom condition" box, so they never
  // travel through an inline handler. Dismiss is matched first — it sits inside the badge.
  const condExpand = e.target.closest('[data-cond-expand-en]');
  if (condExpand) {
    e.stopPropagation();
    combatToggleCondExpand(Number(condExpand.dataset.condExpandEn));
    return;
  }
  const condDismiss = e.target.closest('[data-cond-dismiss-en]');
  if (condDismiss) {
    e.stopPropagation();
    combatDismissCond(Number(condDismiss.dataset.condDismissEn), condDismiss.dataset.condName || '');
    return;
  }
  const condEl = e.target.closest('[data-cond-en]');
  if (condEl) {
    e.stopPropagation();
    combatToggleCond(e, Number(condEl.dataset.condEn), condEl.dataset.condName || '',
                     condEl.dataset.condDesc || '', condEl);
    return;
  }
  // A combatant row's detail button: no name travels, only the encounter id.
  const detailEl = e.target.closest('.init-btn[data-en-id]');
  if (detailEl) {
    e.stopPropagation();
    showCombatantDetails(Number(detailEl.dataset.enId));
    return;
  }
  // Source badge anywhere in the DOM (initiative rows, library cards).
  const srcEl = e.target.closest('.src-badge[data-src]');
  if (srcEl) {
    e.stopPropagation();
    openSourceRef(srcEl.dataset.src || '');
    return;
  }
  const awardEl = e.target.closest('.item-card-award[data-item-id]');
  if (awardEl) {
    e.stopPropagation();
    openAwardPopover(Number(awardEl.dataset.itemId), awardEl.dataset.itemName || '',
                     Number(awardEl.dataset.itemQty || 1));
    return;
  }
  const pickerRow = e.target.closest('.picker-result-row');
  if (pickerRow) {
    const srcBadge = e.target.closest('.src-badge');
    if (srcBadge) {
      e.stopPropagation();
      openSourceRef(srcBadge.dataset.src || '');
      return;
    }
    showItemDetail(pickerRow.dataset.itemName || '');
  }
  // Participant row toggle: index from a data attribute, not an inline handler.
  const participantRow = e.target.closest('[data-participant-index]');
  if (participantRow) {
    toggleParticipantStats(Number(participantRow.dataset.participantIndex));
    return;
  }
});

document.addEventListener('dragstart', function(e) {
  const card = e.target.closest('.item-card[data-item-source="pool"]');
  if (!card) return;
  itemsDragStart(e, card.dataset.itemName || '', 'pool', Number(card.dataset.itemId));
});

