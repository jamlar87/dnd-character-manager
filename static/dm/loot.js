/* dm/loot.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Roll Loot ──
let _currentHoard = null; // {coins: [], gems: [], magic_items: [], total_gp_value: N}

async function rollLoot() {
  if (!_itemsCampId) return alert('Select a campaign first');
  const bracket = document.getElementById('lootCrBracket').value;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/roll-loot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cr_bracket: bracket })
  });
  const data = await resp.json();
  if (data.ok) {
    _currentHoard = data.hoard;
    renderLootStaging(data.hoard, bracket);
    document.getElementById('btnRollLoot').style.display = 'none';
    document.getElementById('btnRerollLoot').style.display = '';
  } else {
    alert(data.error || 'Failed to roll loot');
  }
}

async function rerollLoot() {
  if (!_itemsCampId) return alert('Select a campaign first');
  const bracket = document.getElementById('lootCrBracket').value;
  const resp = await fetch(`/api/campaign/${_itemsCampId}/roll-loot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cr_bracket: bracket })
  });
  const data = await resp.json();
  if (data.ok) {
    _currentHoard = data.hoard;
    renderLootStaging(data.hoard, bracket);
  } else {
    alert(data.error || 'Failed to reroll loot');
  }
}

function renderLootStaging(hoard, bracket) {
  const staging = document.getElementById('lootStaging');
  const items = document.getElementById('lootStagingItems');
  const label = document.getElementById('lootStagingLabel');
  staging.style.display = '';
  label.textContent = `🎲 Loot Staging — CR ${bracket}`;

  let html = '';

  // Coins
  if (hoard.coins && hoard.coins.length) {
    hoard.coins.forEach((c, i) => {
      html += `<div class="item-card" style="border-color:#ffd700">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name">${c.label}</span>
        <span style="font-size:0.68rem;color:#ffd700;margin-left:0.2rem">≈${c.gp_value.toLocaleString()} gp</span>
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('coin', ${i})" title="Keep">📥</button>
        <div class="item-detail"><span class="detail-label">Value</span> ≈${c.gp_value.toLocaleString()} gp (${c.amount} ${c.type})</div>
      </div>`;
    });
  }

  // Gems
  if (hoard.gems && hoard.gems.length) {
    hoard.gems.forEach((g, i) => {
      html += `<div class="item-card" style="border-color:#00bcd4">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name">${g.label}</span>
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('gem', ${i})" title="Keep">📥</button>
        <div class="item-detail"><span class="detail-label">Value</span> ${g.total_value.toLocaleString()} gp (${g.count} × ${g.value_per} gp each)</div>
      </div>`;
    });
  }

  // Magic items
  if (hoard.magic_items && hoard.magic_items.length) {
    hoard.magic_items.forEach((m, i) => {
      const attune = /requires attunement/i.test(m.description || '') ? ' ◆ Attunement required' : '';
      const desc = (m.description || 'No description available.').replace(/'/g, "\\'");
      html += `<div class="item-card" style="border-color:#ce93d8">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        ${charPortraitTile(null, m.name, {size: 24, src: '/api/ref-image/item/' + encodeURIComponent(m.name) + '?size=48'})}
        <span class="item-card-name">${m.name}</span>
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        <span style="font-size:0.72rem;color:var(--text-muted);margin-left:0.2rem">${m.rarity}</span>
        ${m.source ? `<span class="src-badge" data-src="${dmEsc(m.source)}" style="font-size:0.68rem;color:var(--text-muted);opacity:0.6;margin-left:0.2rem;cursor:pointer" title="Click to open ${m.source}">📚</span>` : ''}
        <button class="item-card-btn" style="color:var(--success)" onclick="keepLootItem('magic', ${i})" title="Keep">📥</button>
        <div class="item-detail">
          <span class="detail-label">Rarity</span> ${m.rarity}${attune ? '<br><span class="detail-label">Attunement</span>' + attune : ''}
          <br><span style="font-size:0.65rem;line-height:1.4;color:var(--text)">${m.description || 'No description available.'}</span>
        </div>
      </div>`;
    });
  }

  if (!html) {
    html = '<span style="color:var(--text-muted);font-size:0.85rem">No treasure found — the hoard was empty!</span>';
  }

  items.innerHTML = html;
  setTimeout(dmTagItemBadges, 50);
}

// ── Expand/collapse staging item cards ──
function toggleItemExpand(btn) {
  const card = btn.closest('.item-card');
  if (!card) return;
  card.classList.toggle('expanded');
}

// ── Expand/collapse team pool item cards (with fetch + qty) ──
const _dmItemDetailCache = {};

async function dmToggleItemExpand(btn) {
  const card = btn.closest('.item-card');
  if (!card) return;
  const isExpanded = card.classList.toggle('expanded');
  if (!isExpanded) return;

  const nameEl = card.querySelector('.item-card-name');
  const name = (nameEl?.value || nameEl?.textContent || '').trim();
  if (!name) return;

  const detail = card.querySelector('.item-detail');
  if (!detail) return;

  // Show qty row immediately from existing badge
  let currentQty = 1;
  const qtyBadge = card.querySelector('.item-qty');
  if (qtyBadge) currentQty = parseInt(qtyBadge.textContent) || 1;

  if (_dmItemDetailCache[name]) {
    detail.innerHTML = _dmItemDetailCache[name] + dmBuildQtyRow(currentQty);
    return;
  }

  // Fetch from API
  try {
    const resp = await fetch(`/api/items/describe?name=${encodeURIComponent(name)}`);
    const data = await resp.json();
    let descHtml = '';
    if (data.description) {
      descHtml = `<p style="margin:0;font-size:0.7rem;line-height:1.4;white-space:normal;overflow:visible">${data.description}</p>`;
      if (data.curse) descHtml += _dmCurseSectionHtml(data.curse);
      if (data.rarity) descHtml += `<br><span class="detail-label">Rarity</span> ${data.rarity}`;
      if (data.source) descHtml += ` <span class="src-badge" data-src="${dmEsc(data.source)}" style="font-size:0.72rem;cursor:pointer;opacity:0.7" title="Click to open ${data.source}">📚 ${data.source}</span>`;
    } else {
      descHtml = `<span class="detail-label">No description available</span>`;
    }
    _dmItemDetailCache[name] = descHtml;
    detail.innerHTML = descHtml + dmBuildQtyRow(currentQty);
  } catch {
    detail.innerHTML = `<span class="detail-label">Description unavailable</span>` + dmBuildQtyRow(currentQty);
  }
}

function dmBuildQtyRow(qty) {
  return `<div style="margin-top:0.4rem;display:flex;align-items:center;gap:0.3rem">
    <span class="detail-label">Qty</span>
    <button class="qty-stepper" onclick="event.stopPropagation();dmStepQty(this, -1)">−</button>
    <input type="number" class="qty-detail-input" value="${qty}" min="1" max="999"
      style="width:4ch;padding:0.1rem 0.2rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.65rem;-moz-appearance:textfield;appearance:textfield;text-align:center"
      onchange="dmUpdateItemQty(this)" onclick="event.stopPropagation()">
    <button class="qty-stepper" onclick="event.stopPropagation();dmStepQty(this, 1)">+</button>
  </div>`;
}

// ── Curse spoiler for DM item cards ──
function _dmCurseSectionHtml(curseText) {
  if (!curseText) return '';
  const safe = curseText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<details style="margin-top:0.4rem;border:1px solid var(--danger, #e74c3c);border-radius:6px;padding:0.25rem 0.4rem;background:rgba(231,76,60,0.08);cursor:pointer">
    <summary style="font-size:0.65rem;font-weight:600;color:var(--danger, #e74c3c);user-select:none">
      ⚠️ Curse (DM permission to reveal)
    </summary>
    <div style="margin-top:0.25rem;font-size:0.65rem;line-height:1.4;color:var(--text-danger, #c0392b)">${safe}</div>
  </details>`;
}

function dmStepQty(btn, delta) {
  const input = btn.parentElement.querySelector('.qty-detail-input');
  if (!input) return;
  let val = parseInt(input.value) || 1;
  val = Math.max(1, Math.min(999, val + delta));
  input.value = val;
  dmUpdateItemQty(input);
}

async function dmUpdateItemQty(input) {
  const card = input.closest('.item-card');
  if (!card) return;
  const qty = Math.max(1, parseInt(input.value) || 1);
  input.value = qty;

  // Update badge
  let badge = card.querySelector('.item-qty');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'item-qty';
    badge.style.cursor = 'pointer';
    badge.title = 'Click to expand';
    badge.onclick = function(e) { e.stopPropagation(); dmToggleItemExpand(card.querySelector('.item-expand-btn')); };
    const btn = card.querySelector('.item-card-btn');
    card.insertBefore(badge, btn);
  }
  badge.textContent = qty;

  // Save to server
  const itemId = card.getAttribute('data-item-id');
  if (!itemId || !_itemsCampId) return;
  try {
    await fetch(`/api/campaign/${_itemsCampId}/team-items/${itemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ qty })
    });
  } catch (e) { /* silently fail */ }
}

async function keepLootItem(type, index) {
  if (!_currentHoard) return;

  let name, gpValue = 0;

  if (type === 'coin') {
    const c = _currentHoard.coins[index];
    // Standardize currency to GP
    name = `${c.gp_value.toLocaleString()} gp`;
    gpValue = c.gp_value;
    // Remove from hoard
    _currentHoard.coins.splice(index, 1);
  } else if (type === 'gem') {
    const g = _currentHoard.gems[index];
    name = g.label;
    _currentHoard.gems.splice(index, 1);
  } else if (type === 'magic') {
    const m = _currentHoard.magic_items[index];
    name = m.name;
    _currentHoard.magic_items.splice(index, 1);
  }

  // Add to team pool via API
  const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, qty: 1, gp_value: gpValue })
  });
  const data = await resp.json();
  if (data.ok) {
    // Re-render staging and pool
    renderLootStaging(_currentHoard, document.getElementById('lootCrBracket').value);
    loadItemsPanel();
  } else {
    alert(data.error || 'Failed to keep item');
  }
}

async function keepAllLoot() {
  if (!_currentHoard) return;
  if (!confirm('Add ALL staged items to the team pool?')) return;

  const items = [];
  (_currentHoard.coins || []).forEach(c => items.push({ name: `${c.gp_value.toLocaleString()} gp`, gp_value: c.gp_value }));
  (_currentHoard.gems || []).forEach(g => items.push({ name: g.label, gp_value: 0 }));
  (_currentHoard.magic_items || []).forEach(m => items.push({ name: m.name, gp_value: 0 }));

  if (items.length === 0) return;

  let failed = 0;
  for (const item of items) {
    const resp = await fetch(`/api/campaign/${_itemsCampId}/team-items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: item.name, qty: 1, gp_value: item.gp_value })
    });
    if (!(await resp.json()).ok) failed++;
  }

  // Clear hoard and re-render
  _currentHoard = { coins: [], gems: [], magic_items: [], total_gp_value: 0 };
  renderLootStaging(_currentHoard, document.getElementById('lootCrBracket').value);
  loadItemsPanel();

  if (failed) alert(`${failed} item(s) failed to add.`);
}

// ═══════════════════════════════════════════════════════════
// ⚔️ COMBAT TRACKER  [v2 — characters inline]
// ═══════════════════════════════════════════════════════════
