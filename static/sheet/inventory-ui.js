/* sheet/inventory-ui.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
function renderEquipped() {
  const list = document.getElementById('equipped-list');
  const emptyEl = document.getElementById('equipped-empty');
  console.log('renderEquipped: list found =', !!list, 'items =', equippedItems.length);
  if (!list) return;

  if (equippedItems.length === 0) {
    list.innerHTML = '';
    if (emptyEl) emptyEl.style.display = 'block';
    renderAttunementSlots();
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  list.innerHTML = equippedItems.map(eq => {
    const item = typeof eq === 'object' ? eq.name : eq;
    const eqQty = typeof eq === 'object' ? (eq.qty || 1) : 1;
    const enhancement = typeof eq === 'object' ? (eq.enhancement || 0) : 0;
    const eqDice = typeof eq === 'object' ? (eq.dice || '') : '';
    const diceBadge = eqDice ? `<span class="item-dice-badge" style="font-size:0.68rem;font-weight:600;background:var(--accent2);color:var(--accent);font-family:monospace;padding:0.1rem 0.25rem;border-radius:3px;flex-shrink:0">${eqDice}</span>` : '';
    const needsAttune = ITEM_NEEDS_ATTUNEMENT[item];
    const isAttuned = ATTUNED_ITEMS.includes(item);
    let attuneHtml = '';
    if (needsAttune) {
      if (isAttuned) {
        attuneHtml = `<span class="item-attune-badge attuned" onclick="toggleAttune('${item.replace(/'/g, "\\'")}')" title="Attuned — click to break">◆</span>`;
      } else {
        attuneHtml = `<span class="item-attune-badge" onclick="toggleAttune('${item.replace(/'/g, "\\'")}')" title="Needs attunement — click to attune">◇</span>`;
      }
    }
    const qtyBadge = eqQty > 1 ? `<span class="item-qty" style="cursor:default">${eqQty}</span>` : '';
    const enhBadge = isWeaponOrArmor(item)
      ? `<span class="enh-badge" onclick="event.stopPropagation();cycleEnhancement(this)">+${enhancement}</span>`
      : '';
    return `<div class="item-card equipped-card" draggable="true"
      data-item-name="${item.replace(/"/g, '&quot;')}"
      data-enhancement="${enhancement}"
      ondragstart="handleDragStart(event, '${item.replace(/'/g, "\\'")}', 'equipped', ${eqQty})"
      ondragend="handleDragEnd(event)">
      <span class="drag-handle" title="Drag to unequip">⋮⋮</span>
      <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
      <span class="item-card-name" style="cursor:default">${item}</span>
      ${qtyBadge}
      ${attuneHtml}
      ${enhBadge}
      ${diceBadge}
      <span class="wpn-badge item-tag" style="display:none">⚔️</span>
      <span class="arm-badge item-tag" style="display:none">🛡️</span>
      <button class="item-card-btn" onclick="quickUnequip('${item.replace(/'/g, "\\'")}', ${eqQty})" title="Unequip">⬇️</button>
      <div class="item-detail" data-item-name="${item.replace(/"/g, '&quot;')}"></div>
    </div>`;
  }).join('');
  // The badges above came from ATTUNED_ITEMS — redraw the slot row from the same
  // list so the two halves of the card can never disagree after a list redraw.
  renderAttunementSlots();
}

// Auto-load team items when opening inventory tab (merged with history tab hook below)

// ── Drag & Drop handlers ──
let _dragItem = null; // { name, source, qty }

function handleDragStart(e, name, source, qty) {
  _dragItem = { name, source, qty };
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', name);
  e.target.closest('.item-card')?.classList.add('dragging');
}

function handleDragEnd(e) {
  e.target.closest('.item-card')?.classList.remove('dragging');
  document.querySelectorAll('.drop-zone-active').forEach(el => el.classList.remove('drop-zone-active'));
  _dragItem = null;
}

function handleDragOver(e, zone) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const card = e.currentTarget;
  if (!card.classList.contains('drop-zone-active')) {
    card.classList.add('drop-zone-active');
  }
}

function handleDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) {
    e.currentTarget.classList.remove('drop-zone-active');
  }
}

async function handleDrop(e, targetZone) {
  e.preventDefault();
  e.currentTarget.classList.remove('drop-zone-active');
  if (!_dragItem) return;

  const { name, source, qty } = _dragItem;
  if (source === targetZone) return;

  if (source === 'inventory' && targetZone === 'equipped') {
    // Move card from inventory to equipped: remove from DOM, save inventory, then equip
    const card = document.querySelector(`#inv-list .item-card[data-item-name="${CSS.escape(name)}"]`);
    const enhancement = card ? parseInt(card.dataset.enhancement || '0') : 0;
    if (card) card.remove();
    await _flushInventory();
    await equipItem(name, qty, enhancement);
  } else if (source === 'equipped' && targetZone === 'inventory') {
    // Move from equipped to inventory: unequip first, then add card + save
    const eqQty = _equippedQtys[name] || 1;
    const eqCard = document.querySelector(`#equipped-list .item-card[data-item-name="${CSS.escape(name)}"]`);
    const eqEnhancement = eqCard ? parseInt(eqCard.dataset.enhancement || '0') : 0;
    await unequipItem(name);
    addInventoryCard(name, eqQty, eqEnhancement);
    await _flushInventory();
  } else if (source === 'inventory' && targetZone === 'team') {
    await shareToTeam(name, qty);
  } else if (source === 'team' && targetZone === 'inventory') {
    const teamCard = document.querySelector(`.team-item-card[data-item-name="${CSS.escape(name)}"]`);
    const itemId = teamCard?.dataset?.itemId;
    if (itemId) await claimTeamItem(parseInt(itemId), name);
  }
  _dragItem = null;
}

// ── Add a card to the inventory DOM ──
function addInventoryCard(name, qty, enhancement) {
  const list = document.getElementById('inv-list');
  if (!list) return;
  const emptyEl = document.getElementById('inv-empty');
  if (emptyEl) emptyEl.style.display = 'none';

  const qtyBadge = qty > 1 ? `<span class="item-qty" onclick="event.stopPropagation();toggleItemExpand(this.closest('.item-card').querySelector('.item-expand-btn'))" title="Click to expand" style="cursor:pointer">${qty}</span>` : '';
  const safeName = name.replace(/"/g, '&quot;');
  const safeNameAttr = name.replace(/'/g, "\\'");

  const card = document.createElement('div');
  card.className = 'item-card';
  card.draggable = true;
  card.setAttribute('data-item-name', safeName);
  card.dataset.enhancement = String(enhancement || 0);
  card.setAttribute('ondragstart', `handleDragStart(event, '${safeNameAttr}', 'inventory', ${qty})`);
  card.setAttribute('ondragend', 'handleDragEnd(event)');
  card.innerHTML = `<span class="drag-handle" title="Drag to equip">⋮⋮</span>
    <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
    <input type="text" value="${safeName}" class="item-card-name"
      onchange="autoSaveInventory();tagWeapons()"
      onfocus="this.closest('.item-card').classList.add('editing')"
      onblur="this.closest('.item-card').classList.remove('editing')">
    ${qtyBadge}
    <button class="item-card-btn" onclick="event.stopPropagation();showItemInfo(this.closest('.item-card').querySelector('.item-card-name').value)" title="Info">ℹ️</button>
    <span class="wpn-badge item-tag" style="display:none">⚔️</span>
    <span class="arm-badge item-tag" style="display:none">🛡️</span>
    <span class="enh-badge" onclick="event.stopPropagation();cycleEnhancement(this)" style="display:none">+1</span>
    ${CAMPAIGN_ID ? `<button class="item-card-btn" onclick="shareToTeam(this.closest('.item-card').querySelector('.item-card-name').value, ${qty})" title="Share to team">↗</button>` : ''}
    <button class="item-card-btn" onclick="quickEquip(this.closest('.item-card').querySelector('.item-card-name').value, ${qty})" title="Equip">⬆️</button>
    <button class="item-card-btn item-card-del" onclick="deleteInventoryItem(this.closest('.item-card').querySelector('.item-card-name').value)" title="Delete">✕</button>
    <div class="item-detail"></div>`;
  list.appendChild(card);
}

// ── Delete inventory item ──
async function deleteInventoryItem(name) {
  if (!confirm(`Remove "${name}" from inventory?`)) return;
  const card = document.querySelector(`#inv-list .item-card[data-item-name="${CSS.escape(name)}"]`);
  if (card) card.remove();
  await _flushInventory();
}

// ── Attunement ──
// ATTUNED_ITEMS is the client mirror of characters.attuned_items. Every write has
// to update it: renderEquipped() derives the ◆/◇ badge from this list, so a stale
// mirror redraws an attuned item as unattuned the next time anything is equipped
// (the slot row would still read 1/3 — the two halves of the card disagree).
// The slot row is derived from the same list, never read back out of the DOM.
function equippedNames() {
  return equippedItems.map(eq => (typeof eq === 'object' ? eq.name : eq));
}

function attunedEquipped() {
  const equipped = equippedNames();
  return ATTUNED_ITEMS.filter(n => equipped.includes(n));
}

function renderAttunementSlots(used) {
  const slots = document.querySelectorAll('#attunement-slots .attune-slot');
  if (typeof used !== 'number') used = attunedEquipped().length;
  slots.forEach((el, i) => {
    if (i < used) {
      el.classList.add('filled');
      el.style.background = 'var(--accent)';
      el.style.borderColor = 'var(--accent)';
      el.textContent = '◆';
    } else {
      el.classList.remove('filled');
      el.style.background = '';
      el.style.borderColor = '';
      el.textContent = '◇';
    }
  });
  const countEl = document.querySelector('#attunement-slots span:last-child');
  if (countEl) countEl.textContent = `${used}/3`;
}

function setAttuned(name, on) {
  const i = ATTUNED_ITEMS.indexOf(name);
  if (on && i === -1) ATTUNED_ITEMS.push(name);
  if (!on && i !== -1) ATTUNED_ITEMS.splice(i, 1);
}

async function toggleAttune(itemName) {
  const trimmed = itemName.trim();
  // Extract char_id from URL (/character/{id})
  const pathMatch = window.location.pathname.match(/\/character\/(\d+)/);
  if (!pathMatch) { alert('Could not determine character'); return; }
  const charId = pathMatch[1];
  try {
    const r = await fetch(`/api/character/${charId}/toggle-attune`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({item: trimmed})
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    // Keep the mirror in step BEFORE touching the DOM — renderEquipped() and the
    // slot row both read it.
    setAttuned(trimmed, d.action === 'attuned');
    // Update badge and slots without full reload
    const card = document.querySelector(`.equipped-card[data-item-name="${CSS.escape(trimmed)}"]`);
    if (card) {
      const badge = card.querySelector('.item-attune-badge');
      if (badge) {
        if (d.action === 'attuned') {
          badge.className = 'item-attune-badge attuned';
          badge.textContent = '◆';
          badge.title = 'Attuned — click to break';
        } else {
          badge.className = 'item-attune-badge';
          badge.textContent = '◇';
          badge.title = 'Needs attunement — click to attune';
        }
      }
    }
    renderAttunementSlots(typeof d.slots_used === 'number' ? d.slots_used : undefined);
  } catch(e) {
    alert('Failed to toggle attunement');
  }
}

// ── Save inventory (card-based) ──
let _invSaveBusy = false;
async function saveInventory() {
  if (_invSaveBusy) {
    // Save already in flight — schedule a retry that re-reads fresh DOM
    clearTimeout(window._invSaveQueued);
    window._invSaveQueued = setTimeout(saveInventory, 50);
    return;
  }
  _invSaveBusy = true;
  try {
    await _flushInventory();
  } catch(e) {
    console.error('saveInventory failed:', e);
  }
  _invSaveBusy = false;
}

async function _flushInventory() {
  const items = [];
  const cards = document.querySelectorAll('#inv-list .item-card');
  console.log('_flushInventory: found', cards.length, 'cards in #inv-list');
  cards.forEach(card => {
    const nameEl = card.querySelector('.item-card-name');
    const qtyBadge = card.querySelector('.item-qty');
    const name = (nameEl?.value || nameEl?.textContent || '').trim();
    if (!name) return;
    const qty = qtyBadge ? parseInt(qtyBadge.textContent) || 1 : 1;
    const enhancement = parseInt(card.dataset.enhancement || '0');
    items.push(enhancement > 0 ? { name, qty, enhancement } : { name, qty });
  });
  document.querySelectorAll('#inv-list .item-card').forEach(card => {
    const nameEl = card.querySelector('.item-card-name');
    if (!(nameEl?.value || nameEl?.textContent || '').trim()) card.remove();
  });
  const emptyEl = document.getElementById('inv-empty');
  if (emptyEl) emptyEl.style.display = items.length === 0 ? 'block' : 'none';
  console.log('Saving inventory:', JSON.stringify(items));
  try {
    await sheetApi('/update', { inventory: JSON.stringify(items) });
  } catch (e) {
    console.error('Inventory save failed:', e.status, e.message);
    return;
  }
  console.log('Inventory save OK');
  tagWeapons();
}

function autoSaveInventory() {
  clearTimeout(window._invSaveTimeout);
  window._invSaveTimeout = setTimeout(saveInventory, 600);
}

// ── Add inventory item (card-based) ──
function addInventoryItem() {
  const list = document.getElementById('inv-list');
  const emptyEl = document.getElementById('inv-empty');
  if (emptyEl) emptyEl.style.display = 'none';
  const card = document.createElement('div');
  card.className = 'item-card';
  card.draggable = true;
  card.setAttribute('data-item-name', '');
  card.dataset.enhancement = '0';
  card.setAttribute('ondragstart', "handleDragStart(event, this.querySelector('.item-card-name').value, 'inventory', 1)");
  card.setAttribute('ondragend', 'handleDragEnd(event)');
  card.innerHTML = `<span class="drag-handle" title="Drag to move">⋮⋮</span>
    <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
    <input type="text" placeholder="Item name" class="item-card-name"
      onchange="autoSaveInventory();tagWeapons();this.closest('.item-card').setAttribute('data-item-name',this.value)"
      onfocus="this.closest('.item-card').classList.add('editing')"
      onblur="this.closest('.item-card').classList.remove('editing')">
    <button class="item-card-btn" onclick="event.stopPropagation();showItemInfo(this.closest('.item-card').querySelector('.item-card-name').value)" title="Info">ℹ️</button>
    <span class="wpn-badge item-tag" style="display:none">⚔️</span>
    <span class="arm-badge item-tag" style="display:none">🛡️</span>
    <span class="enh-badge" onclick="event.stopPropagation();cycleEnhancement(this)" style="display:none">+1</span>
    <button class="item-card-btn" onclick="quickEquip(this.closest('.item-card').querySelector('.item-card-name').value, 1)" title="Equip">⬆️</button>
    ${CAMPAIGN_ID ? `<button class="item-card-btn" onclick="shareToTeam(this.closest('.item-card').querySelector('.item-card-name').value, 1)" title="Share to team">↗</button>` : ''}
    <button class="item-card-btn item-card-del" onclick="deleteInventoryItem(this.closest('.item-card').querySelector('.item-card-name').value)" title="Delete">✕</button>
    <div class="item-detail"></div>`;
  list.appendChild(card);
  card.querySelector('.item-card-name').focus();
}

// ── Item Picker (modal popup, same as DM tools) ──
let _pickerTimer = null;

function openItemPicker() {
  const popup = document.getElementById('item-picker-popup');
  if (!popup) return;
  popup.style.display = 'flex';
  document.getElementById('item-picker-results').innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">Loading...</div>';
  // Load all items initially
  fetch('/api/items/search')
    .then(r => r.json())
    .then(d => renderPickerResults(d.results || []))
    .catch(() => {});
  document.getElementById('item-picker-search').value = '';
  setTimeout(() => document.getElementById('item-picker-search').focus(), 100);
}

function closeItemPicker() {
  document.getElementById('item-picker-popup').style.display = 'none';
}

// Re-runs the item picker with its current query (used when the manual filter changes)
function refreshSheetItemPicker() {
  const inp = document.getElementById('item-picker-search');
  searchPickerItems(inp ? inp.value : '');
}

function searchPickerItems(query) {
  clearTimeout(_pickerTimer);
  const q = query.trim();
  if (!q) { fetch('/api/items/search').then(r=>r.json()).then(d=>renderPickerResults(d.results||[])).catch(()=>{}); return; }
  _pickerTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/items/search?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      renderPickerResults(d.results || []);
    } catch(e) {}
  }, 200);
}

function renderPickerResults(items) {
  const results = document.getElementById('item-picker-results');
  if (_coreOnly) items = items.filter(it => isCoreItemSource(it.source || ''));
  if (window.SourceFilter) {
    const src = SourceFilter.slugs('sheet-items');
    if (src.length) items = items.filter(it => SourceFilter.matches(it.source || '', src));
  }
  if (!items.length) {
    results.innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">No items found</div>';
    return;
  }
  results.innerHTML = items.map(item =>
    `<div style="padding:0.25rem 0.6rem;cursor:pointer;border-bottom:1px solid var(--border);overflow-wrap:break-word;word-break:break-word">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem">
        <span style="flex:1;min-width:0;display:flex;align-items:center;gap:0.45rem;font-size:0.8rem;color:var(--text)" onclick="selectPickerItem('${item.name.replace(/'/g, "\\'")}')">${charPortraitTile(null, item.name, {size: 28, src: '/api/ref-image/item/' + encodeURIComponent(item.name) + '?size=56'})}<span style="min-width:0">${item.name}${item.source ? ` <span class="src-badge" onclick="event.stopPropagation();openSourceRef('${item.source.replace(/'/g, "\\'")}')" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;cursor:pointer" title="Click to open ${item.source}">📚 ${item.source}</span>` : ''}</span></span>
        <div style="display:flex;align-items:center;gap:0.3rem;flex-shrink:0">
          <span style="font-size:0.7rem;color:var(--text-muted);white-space:nowrap">${item.type}${item.rarity ? ' · '+item.rarity : ''}</span>
          ${item.concentration ? '<span style="font-size:0.68rem;font-weight:600;color:#f59e0b;background:rgba(245,158,11,0.12);padding:0.05rem 0.25rem;border-radius:3px">⟲</span>' : ''}
          <span style="font-size:0.75rem;color:var(--accent);cursor:pointer;padding:0.15rem 0.4rem;border-radius:3px;user-select:none" onclick="event.stopPropagation();event.preventDefault();togglePickerRowDesc(this)" onmousedown="event.stopPropagation()" onmouseover="this.style.background='var(--accent2)'" onmouseout="this.style.background='transparent'">▾ info</span>
        </div>
      </div>
      <div class="picker-row-desc" style="display:none;padding:0.25rem 0 0.2rem 0;font-size:0.7rem;color:var(--text-muted);line-height:1.4;border-top:1px solid var(--border);margin-top:0.2rem">${item.description || 'No description available.'}</div>
    </div>`
  ).join('');
}

function togglePickerRowDesc(el) {
  const descDiv = el.parentElement.parentElement.nextElementSibling;
  if (!descDiv || !descDiv.classList.contains('picker-row-desc')) return;
  const isHidden = descDiv.style.display === 'none' || !descDiv.style.display;
  descDiv.style.display = isHidden ? 'block' : 'none';
  el.textContent = isHidden ? '▴ info' : '▾ info';
}

function selectPickerItem(name) {
  selectItem(name);
  closeItemPicker();
}

function toggleItemDesc(el) {
  const descDiv = el.parentElement.nextElementSibling;
  if (!descDiv) return;
  const isHidden = descDiv.style.display === 'none' || !descDiv.style.display;
  descDiv.style.display = isHidden ? 'block' : 'none';
  el.textContent = isHidden ? '▴ info' : '▾ info';
}

let _selectingItem = false;
async function selectItem(name) {
  console.log('selectItem called:', name);
  // Guard against rapid double-clicks
  if (_selectingItem) { console.log('selectItem blocked by guard'); return; }
  _selectingItem = true;
  try {
    const list = document.getElementById('inv-list');
    if (!list) { console.log('selectItem: no inv-list'); return; }
    const emptyEl = document.getElementById('inv-empty');
    if (emptyEl) emptyEl.style.display = 'none';
    const safeName = CSS.escape(name);
    const existing = list.querySelector(`.item-card[data-item-name="${safeName}"]`);
    console.log('selectItem: existing card?', !!existing);
    if (existing) {
      console.log('selectItem: existing found, incrementing qty');
      const qtyBadge = existing.querySelector('.item-qty');
      console.log('selectItem: existing badge?', !!qtyBadge, 'textContent:', qtyBadge?.textContent);
      if (qtyBadge) {
        qtyBadge.textContent = parseInt(qtyBadge.textContent) + 1;
      } else {
        const span = document.createElement('span');
        span.className = 'item-qty';
        span.style.cursor = 'pointer';
        span.title = 'Click to expand';
        span.onclick = function(e) { e.stopPropagation(); toggleItemExpand(existing.querySelector('.item-expand-btn')); };
        span.textContent = '2';
        const btn = existing.querySelector('.item-card-btn');
        if (btn) existing.insertBefore(span, btn);
      }
    } else {
      console.log('selectItem: creating new card for', name);
      const card = document.createElement('div');
      card.className = 'item-card';
      card.draggable = true;
      card.setAttribute('data-item-name', name);
      card.setAttribute('ondragstart', `handleDragStart(event, '${name.replace(/'/g, "\\'")}', 'inventory', 1)`);
      card.setAttribute('ondragend', 'handleDragEnd(event)');
      const escapedName = name.replace(/"/g, '&quot;');
      const escapedQuote = name.replace(/'/g, "\\'");
      card.innerHTML = `<span class="drag-handle" title="Drag to move">⋮⋮</span>
        <input type="text" value="${escapedName}" class="item-card-name"
          onchange="autoSaveInventory();tagWeapons()"
          onfocus="this.closest('.item-card').classList.add('editing')"
          onblur="this.closest('.item-card').classList.remove('editing')">
        <button class="item-card-btn" onclick="event.stopPropagation();showItemInfo('${escapedQuote}')" title="Info">ℹ️</button>
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        ${CAMPAIGN_ID ? `<button class="item-card-btn" onclick="shareToTeam('${escapedQuote}', 1)" title="Share to team">↗</button>` : ''}
        <button class="item-card-btn" onclick="quickEquip(this.closest('.item-card').querySelector('.item-card-name').value, 1)" title="Equip">⬆️</button>
        <button class="item-card-btn item-card-del" onclick="deleteInventoryItem('${escapedQuote}')" title="Delete">✕</button>
        <div class="item-detail"></div>`;
      list.appendChild(card);
      console.log('selectItem: card appended');
    }
    toggleItemSearch();
    await saveInventory();
  } catch(e) {
    console.error('selectItem error:', e);
  } finally {
    _selectingItem = false;
  }
}

