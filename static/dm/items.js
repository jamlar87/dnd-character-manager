/* dm/items.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Items Panel ──
let _itemsDragItem = null;
let _itemsCampId = null;
let _itemsPartyChars = [];

async function loadItemsPanel() {
  const sel = document.getElementById('itemsCampaignSelect');
  _itemsCampId = sel.value ? parseInt(sel.value) : null;

  // Always populate campaign dropdown if empty
  if (sel.options.length <= 1) {
    try {
      const cr = await fetch('/api/dm/campaigns');
      const cd = await cr.json();
      (cd.campaigns || []).forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id; opt.textContent = c.name;
        sel.appendChild(opt);
      });
    } catch(e) {}
  }

  if (!_itemsCampId) {
    document.getElementById('itemsPanelContent').innerHTML =
      '<div style="color:var(--text-muted);padding:2rem;text-align:center;width:100%">Select a campaign to manage items</div>';
    return;
  }
  const content = document.getElementById('itemsPanelContent');
  content.innerHTML = '<div style="text-align:center;padding:2rem;width:100%">Loading...</div>';

  try {
    const cr = await fetch('/api/dm/campaigns');
    const cd = await cr.json();
    const camps = cd.campaigns || [];

    // Find selected campaign
    const camp = camps.find(c => c.id == _itemsCampId);
    if (!camp) { content.innerHTML = '<p>Campaign not found</p>'; return; }
    const chars = typeof camp.characters === 'string' ? JSON.parse(camp.characters) : (camp.characters || []);
    _itemsPartyChars = chars;

    // Fetch team items
    const tr = await fetch(`/api/campaign/${_itemsCampId}/team-items`);
    const td = await tr.json();
    const items = td.items || [];

    renderItemsPanel(items, chars);
  } catch (e) {
    content.innerHTML = '<p style="color:var(--danger)">Failed to load. Are you the DM?</p>';
  }
}

function renderItemsPanel(items, chars) {
  const content = document.getElementById('itemsPanelContent');

  // Team items pool (left)
  let poolHtml = '<div class="item-pool"><div class="card"><h3 style="margin:0 0 0.5rem 0">📦 Team Items Pool</h3>';
  if (items.length === 0) {
    poolHtml += '<div style="color:var(--text-muted);font-size:0.85rem;padding:0.5rem 0">No items in pool. Click +Add or drag items from characters below.</div>';
  } else {
    poolHtml += '<div style="display:flex;flex-wrap:wrap;gap:0.35rem;min-height:2rem">';
    items.forEach(item => {
      const qtyBadge = `<span class="item-qty" onclick="event.stopPropagation();dmToggleItemExpand(this.closest('.item-card').querySelector('.item-expand-btn'))" title="Click to expand" style="cursor:pointer">${item.qty}</span>`;
      const gpInfo = item.gp_value > 0 ? `<span style="font-size:0.68rem;color:#ffd700;margin-left:0.2rem">${item.gp_value}gp</span>` : '';
      const srcBadge = item.source ? ` <span class="src-badge" data-src="${dmEsc(item.source)}" style="font-size:0.68rem;cursor:pointer;opacity:0.6" title="Click to open source">📚</span>` : '';
      poolHtml += `<div class="item-card" draggable="true"
        data-item-name="${dmEsc(item.name)}" data-item-id="${item.id}" data-item-source="pool"
        ondragend="itemsDragEnd(event)">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();dmToggleItemExpand(this)" title="Expand">▶</button>
        <span class="drag-handle" title="Drag to award">⋮⋮</span>
        ${charPortraitTile(null, item.name, {size: 24, src: '/api/ref-image/item/' + encodeURIComponent(item.name) + '?size=48'})}
        <span class="item-card-name" style="cursor:default">${dmEsc(item.name)}</span>${srcBadge}${qtyBadge}${gpInfo}
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        <button class="item-card-btn item-card-award" data-item-id="${item.id}" data-item-name="${dmEsc(item.name)}" data-item-qty="${item.qty}" title="Award to character">🎁</button>
        <button class="item-card-btn item-card-del" onclick="deleteTeamItem(${item.id})" title="Remove">✕</button>
        <div class="item-detail" data-item-name="${dmEsc(item.name)}"></div>
      </div>`;
    });
    poolHtml += '</div>';
  }
  poolHtml += '</div></div>';

  // Party members (right)
  let partyHtml = '<div class="party-drops"><div class="card"><h3 style="margin:0 0 0.5rem 0">👥 Party</h3>';
  if (chars.length === 0) {
    partyHtml += '<div style="color:var(--text-muted);font-size:0.85rem">No characters in party. Add them in the Campaign tab.</div>';
  } else {
    chars.forEach(ch => {
      partyHtml += `<div style="margin-bottom:0.5rem">
        <div class="party-member-label">
          <strong>${dmEsc(ch.name)}</strong>
          <span style="color:var(--text-muted);font-size:0.75rem">L${ch.level} ${dmEsc(ch.class_name)}</span>
        </div>
        <div class="party-drop-zone"
          ondragover="itemsDragOver(event)"
          ondragleave="itemsDragLeave(event)"
          ondrop="itemsDrop(event, ${ch.id}, this.dataset.charName)"
          data-char-name="${dmEsc(ch.name)}"
          id="party-zone-${ch.id}">
          <span style="color:var(--text-muted);font-size:0.7rem">Drop items here to award</span>
        </div>
      </div>`;
    });
  }
  partyHtml += '</div></div>';

  content.innerHTML = `<div style="display:flex;gap:1rem;flex-wrap:wrap">${poolHtml}${partyHtml}</div>`;
  setTimeout(dmTagItemBadges, 50);

  // Initialize campaigns dropdown on first load
  if (!document.getElementById('itemsCampaignSelect').dataset.loaded) {
    document.getElementById('itemsCampaignSelect').dataset.loaded = '1';
    fetch('/api/dm/campaigns').then(r => r.json()).then(d => {
      const sel = document.getElementById('itemsCampaignSelect');
      (d.campaigns || []).forEach(c => {
        if (!sel.querySelector(`option[value="${c.id}"]`)) {
          const opt = document.createElement('option');
          opt.value = c.id; opt.textContent = c.name;
          sel.appendChild(opt);
        }
      });
    });
  }
}

// ── Tag team item cards with weapon/armor badges ──
function dmTagItemBadges() {
  const cards = document.querySelectorAll('#lootStagingItems .item-card, #itemsPanelContent .item-card');
  if (!cards.length) return;
  const wpnKw = ['sword','axe','hammer','bow','dagger','mace','spear','flail','rapier','scimitar','glaive','halberd','pike','lance','whip','club','staff','quarterstaff','crossbow','sling','dart','javelin','trident','war pick','morningstar','greatsword','longsword','shortsword','battleaxe','greataxe','warhammer','maul','handaxe','sickle','blowgun','net','pistol','musket','rifle','blade'];
  const armKw = ['padded','leather','studded','hide','chain shirt','scale mail','breastplate','half plate','ring mail','chain mail','splint','plate','shield','armor'];
  cards.forEach(card => {
    const name = (card.querySelector('.item-card-name')?.textContent || '').toLowerCase().trim();
    if (!name) return;
    const wpnBadge = card.querySelector('.wpn-badge');
    const armBadge = card.querySelector('.arm-badge');
    // Check server-side classification first
    const classified = NAMED_ITEM_TYPES[name];
    if (classified === 'wpn') {
      if (wpnBadge) wpnBadge.style.display = 'inline-block';
      if (armBadge) armBadge.style.display = 'none';
      return;
    }
    if (classified === 'arm') {
      if (wpnBadge) wpnBadge.style.display = 'none';
      if (armBadge) armBadge.style.display = 'inline-block';
      return;
    }
    // Fall back to keyword matching
    if (wpnBadge) wpnBadge.style.display = wpnKw.some(kw => name.includes(kw)) ? 'inline-block' : 'none';
    if (armBadge) armBadge.style.display = armKw.some(kw => name.includes(kw)) ? 'inline-block' : 'none';
  });
}

// ── Items drag/drop ──
function itemsDragStart(e, name, source, itemId) {
  _itemsDragItem = { name, source, itemId };
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', name);
  e.target.closest('.item-card')?.classList.add('dragging');
}

function itemsDragEnd(e) {
  e.target.closest('.item-card')?.classList.remove('dragging');
  document.querySelectorAll('.drop-zone-active').forEach(el => el.classList.remove('drop-zone-active'));
  _itemsDragItem = null;
}

function itemsDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const zone = e.currentTarget;
  if (!zone.classList.contains('drop-zone-active')) zone.classList.add('drop-zone-active');
}

function itemsDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) {
    e.currentTarget.classList.remove('drop-zone-active');
  }
}

async function itemsDrop(e, charId, charName) {
  e.preventDefault();
  e.currentTarget.classList.remove('drop-zone-active');
  if (!_itemsDragItem || !_itemsCampId) return;

  const { name, source, itemId } = _itemsDragItem;
  if (source !== 'pool') return;

  // Confirm award
  if (!confirm(`Award "${name}" to ${charName}?`)) return;
  
  // Claim on behalf of character
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_id: charId })
  });
  const data = await resp.json();
  if (data.ok) {
    loadItemsPanel(); // Refresh
  } else {
    alert(data.error || 'Failed to award item');
  }
  _itemsDragItem = null;
}

// ── Add / delete team items ──
function addTeamItem() {
  if (!_itemsCampId) return alert('Select a campaign first');
  openItemPicker();
}

async function addDiamond() {
  if (!_itemsCampId) return alert('Select a campaign first');
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'Diamond (500gp)', qty: 1, gp_value: 500 })
  });
  const data = await resp.json();
  if (data.ok) {
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to add diamond');
  }
}

async function deleteTeamItem(itemId) {
  if (!confirm('Remove this item from the pool?')) return;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}`, { method: 'DELETE' });
  const data = await resp.json();
  if (data.ok) loadItemsPanel();
  else alert(data.error || 'Failed to remove item');
}

// ── Item Picker Popup ──
let _pickerTimer = null;

function openItemPicker() {
  const popup = document.getElementById('itemPickerPopup');
  if (!popup) return;
  document.getElementById('itemPickerSearch').value = '';
  popup.style.display = 'flex';
  loadAllItems();
}

function closeItemPicker() {
  document.getElementById('itemPickerPopup').style.display = 'none';
}

async function loadAllItems() {
  const results = document.getElementById('itemPickerResults');
  results.innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">Loading...</div>';
  try {
    const type = document.getElementById('itemPickerType') ? document.getElementById('itemPickerType').value : '';
    const rarity = document.getElementById('itemPickerRarity') ? document.getElementById('itemPickerRarity').value : '';
    const params = new URLSearchParams();
    if (type) params.set('type', type);
    if (rarity) params.set('rarity', rarity);
    const qs = params.toString();
    const r = await fetch(`/api/items/search${qs ? '?' + qs : ''}`);
    const d = await r.json();
    renderPickerResults(d.results || []);
  } catch(e) {
    results.innerHTML = '<div style="padding:1rem;color:var(--danger);text-align:center;font-size:0.85rem">Failed to load items</div>';
  }
}

function searchItemPicker(query) {
  clearTimeout(_pickerTimer);
  const q = query.trim();
  _pickerTimer = setTimeout(async () => {
    const results = document.getElementById('itemPickerResults');
    const type = document.getElementById('itemPickerType') ? document.getElementById('itemPickerType').value : '';
    const rarity = document.getElementById('itemPickerRarity') ? document.getElementById('itemPickerRarity').value : '';
    try {
      const params = new URLSearchParams();
      if (q) params.set('q', q);
      if (type) params.set('type', type);
      if (rarity) params.set('rarity', rarity);
      if (window.SourceFilter) {
        const src = SourceFilter.slugs('dm-items');
        if (src.length) params.set('source', SourceFilter.expand(src).join(','));
      }
      const r = await fetch(`/api/items/search?${params.toString()}`);
      const d = await r.json();
      renderPickerResults(d.results || []);
    } catch(e) {}
  }, q ? 250 : 0);
}

function renderPickerResults(items) {
  const results = document.getElementById('itemPickerResults');
  // Apply core filter
  if (_coreOnlyDM) items = items.filter(it => isCoreSourceDM(it.source || ''));
  if (!items.length) {
    results.innerHTML = '<div style="padding:1rem;color:var(--text-muted);text-align:center;font-size:0.85rem">No items found</div>';
    return;
  }
  results.innerHTML = items.map(item =>
    `<div class="picker-result-row" data-item-name="${dmEsc(item.name)}" style="padding:0.25rem 0.6rem;cursor:pointer;border-bottom:1px solid var(--border);overflow-wrap:break-word;word-break:break-word">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem">
        ${charPortraitTile(null, item.name, {size: 28, src: '/api/ref-image/item/' + encodeURIComponent(item.name) + '?size=56'})}
        <span style="flex:1;min-width:0;font-size:0.8rem;color:var(--text)">${dmEsc(item.name)}${item.source ? ` <span class="src-badge" data-src="${dmEsc(item.source)}" style="font-size:0.72rem;color:var(--text-muted);opacity:0.7;cursor:pointer">📚 ${dmEsc(item.source)}</span>` : ''}</span>
        <div style="display:flex;align-items:center;gap:0.3rem;flex-shrink:0">
          <span style="font-size:0.7rem;color:var(--text-muted);white-space:nowrap">${dmEsc(item.type)}${item.rarity ? ' · '+dmEsc(item.rarity) : ''}</span>
          ${item.dice ? `<span style="font-size:0.65rem;font-weight:600;color:var(--accent)">${dmEsc(item.dice)}</span>` : ''}
          <span style="font-size:0.75rem;color:var(--accent);cursor:pointer;padding:0.15rem 0.4rem;border-radius:3px;user-select:none" onmouseover="this.style.background='var(--accent2)'" onmouseout="this.style.background='transparent'">📖</span>
        </div>
      </div>
    </div>`
  ).join('');
}

// ── Item detail modal (full lookup + add to team pool) ──
let _itemDetailName = null;

async function showItemDetail(name) {
  openModal('itemModal');
  document.getElementById('itemDetail').innerHTML = '<div style="text-align:center;padding:2rem">Loading...</div>';
  try {
    const r = await fetch('/api/items/describe?name=' + encodeURIComponent(name));
    const it = await r.json();
    _itemDetailName = it.name || name;

    const rarityColor = {'common':'var(--text-muted)','uncommon':'#2fbf71','rare':'#4da3ff','very rare':'#b57bff','legendary':'#ffb74d','artifact':'#ff8a65'}[String(it.rarity||'').toLowerCase()] || 'var(--text-muted)';

    let html = refArtImg('item', it.name || name, 240) + `<h2 style="margin:0">${it.name}</h2>
      <p style="color:var(--text-muted);margin:0.3rem 0">${it.type || 'Item'}${it.rarity ? ` · <span style="color:${rarityColor};font-weight:600">${it.rarity}</span>` : ''}${it.requires_attunement ? ' · <span style="color:var(--warn)">Requires Attunement</span>' : ''}</p>
      ${it.source ? `<p style="font-size:0.75rem;color:var(--text-muted);margin:0 0 0.3rem 0;cursor:pointer" class="src-badge" data-src="${dmEsc(it.source)}">📚 ${it.source}</p>` : ''}
      <div style="display:flex;gap:1rem;margin:0.5rem 0;font-size:0.9rem;flex-wrap:wrap">
        ${it.cost ? `<span><strong>Cost</strong> ${it.cost}</span>` : ''}
        ${it.weight ? `<span><strong>Weight</strong> ${it.weight} lb.</span>` : ''}
        ${it.dice ? `<span><strong>Damage</strong> ${it.dice}</span>` : ''}
        ${it.base_weapon ? `<span><strong>Base</strong> ${it.base_weapon}</span>` : ''}
      </div>`;

    const descText = it.description || '';
    html += `<div style="margin-top:0.8rem;font-size:0.9rem;line-height:1.55;white-space:pre-wrap">${descText}</div>`;

    if (it.curse) {
      html += `<div class="collapse-header" onclick="toggleCollapse(this)"><span class="collapse-arrow open">▶</span><h3 style="margin:0;border:none;padding:0">🕳️ Cursed</h3></div>`;
      html += `<div class="collapse-body" style="color:var(--danger);font-size:0.85rem">${it.curse}</div>`;
    }

    // Add-to-pool section (requires campaign selected)
    html += `<div style="margin-top:1rem;padding-top:0.8rem;border-top:1px solid var(--border)">
      <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
        <strong style="font-size:0.9rem">📦 Add to Team Items Pool</strong>
        <input type="number" id="itemModalQty" value="1" min="1" style="width:60px;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);text-align:center;font-size:0.85rem" title="Quantity">
        <input type="number" id="itemModalGp" value="0" min="0" placeholder="GP" style="width:70px;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);text-align:center;font-size:0.85rem" title="GP value">
        <button class="btn btn-primary btn-sm" onclick="addItemFromDetail()">➕ Add to Pool</button>
      </div>
      <div style="font-size:0.7rem;color:var(--text-muted);margin-top:0.35rem">${_itemsCampId ? `Campaign #${_itemsCampId} selected` : '⚠ Select a campaign in the Items tab first'}</div>
    </div>`;

    document.getElementById('itemDetail').innerHTML = html;
  } catch(e) {
    document.getElementById('itemDetail').innerHTML = '<div style="text-align:center;padding:2rem;color:var(--danger)">Item not found.</div>';
  }
}

async function addItemFromDetail() {
  if (!_itemDetailName) return;
  if (!_itemsCampId) {
    alert('Select a campaign in the Items tab first');
    return;
  }
  const qty = parseInt(document.getElementById('itemModalQty').value) || 1;
  const gp = parseInt(document.getElementById('itemModalGp').value) || 0;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: _itemDetailName, qty, gp_value: gp })
  });
  const data = await resp.json();
  if (data.ok) {
    closeModal('itemModal');
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to add item');
  }
}

// ── Award item to character (popover picker) ──
let _awardItemId = null, _awardItemName = null, _awardItemQty = null, _awardSelectedChar = null;

function openAwardPopover(itemId, itemName, itemQty) {
  if (!_itemsCampId) return;
  const chars = _itemsPartyChars;
  if (chars.length === 0) { alert('No characters in this campaign'); return; }
  if (chars.length === 1) {
    claimTeamItem(itemId, chars[0].id, itemName, itemQty);
    return;
  }
  _awardItemId = itemId;
  _awardItemName = itemName;
  _awardItemQty = itemQty;
  _awardSelectedChar = null;

  document.getElementById('awardPopoverTitle').textContent = `Award "${itemName}" to:`;
  const container = document.getElementById('awardPopoverChars');
  container.innerHTML = chars.map(c => `
    <div class="award-char-card" data-char-id="${c.id}" onclick="selectAwardChar(this, ${c.id})">
      <span style="font-size:1.1rem">👤</span>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:0.85rem;overflow-wrap:break-word;word-break:break-word">${dmEsc(c.name)}</div>
        <div style="font-size:0.7rem;color:var(--text-muted)">L${c.level} ${dmEsc(c.class_name)}${c.race ? ' · ' + dmEsc(c.race) : ''}</div>
      </div>
    </div>
  `).join('');
  document.getElementById('awardConfirmBtn').disabled = true;
  document.getElementById('awardPopover').style.display = 'flex';
}

function selectAwardChar(el, charId) {
  document.querySelectorAll('#awardPopoverChars .award-char-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  _awardSelectedChar = charId;
  document.getElementById('awardConfirmBtn').disabled = false;
}

function closeAwardPopover() {
  document.getElementById('awardPopover').style.display = 'none';
  _awardSelectedChar = null;
}

async function confirmAwardItem() {
  if (!_awardSelectedChar || !_awardItemId) return;
  await claimTeamItem(_awardItemId, _awardSelectedChar, _awardItemName, _awardItemQty);
  closeAwardPopover();
}

// Close popover on backdrop click
document.getElementById('awardPopover').addEventListener('click', function(e) {
  if (e.target === this) closeAwardPopover();
});

async function claimTeamItem(itemId, charId, itemName, itemQty) {
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_id: charId })
  });
  const data = await resp.json();
  if (data.ok) {
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to award item');
  }
}

