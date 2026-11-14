/* sheet/equip.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Equip/Unequip System ──
let equippedItems = SHEETCFG.charEquipped;
if (!Array.isArray(equippedItems)) equippedItems = [];
// equippedItems is [{name, qty}] from server; normalize to ensure that format
equippedItems = equippedItems.map(eq => {
  if (typeof eq === 'string') return {name: eq, qty: 1};
  return {name: eq.name || '', qty: eq.qty || 1};
});
// Build qty lookup map
const _equippedQtys = {};
equippedItems.forEach(eq => { _equippedQtys[eq.name] = eq.qty; });

function equipItem(name, qty, enhancement) {
  name = name.trim();
  if (!name) return;
  const enh = enhancement || 0;

  // Don't duplicate
  if (equippedItems.some(eq => (typeof eq === 'object' ? eq.name : eq) === name)) return;

  // Armor exclusivity: if equipping body armor, unequip any existing body armor
  if (isBodyArmor(name)) {
    equippedItems = equippedItems.filter(eq => !isBodyArmor(typeof eq === 'object' ? eq.name : eq));
  }

  const item = {name, qty: qty || 1, enhancement: enh};
  equippedItems.push(item);
  _equippedQtys[name] = qty || 1;
  return saveEquipped();
}

// Quick-equip from inventory — clicks the ⬆️ button
async function quickEquip(name, qty) {
  console.log('quickEquip called:', name, qty);
  try {
    // 1. Read enhancement from inventory card
    const safeName = CSS.escape(name);
    const invCard = document.querySelector(`#inv-list .item-card[data-item-name="${safeName}"]`);
    if (!invCard) {
      console.warn('quickEquip: card not found in inv-list for:', name);
      return;
    }
    const enhancement = parseInt(invCard.dataset.enhancement || '0');
    invCard.remove();
    console.log('quickEquip: card removed from DOM');
    
    // 2. Save inventory
    await _flushInventory();
    console.log('quickEquip: inventory saved');
    
    // 3. Add to equipped array and save
    if (!equippedItems.some(eq => (typeof eq === 'object' ? eq.name : eq) === name)) {
      if (isBodyArmor(name)) {
        equippedItems = equippedItems.filter(eq => !isBodyArmor(typeof eq === 'object' ? eq.name : eq));
      }
      equippedItems.push({name: name, qty: qty || 1, enhancement: enhancement || 0});
    }
    _equippedQtys[name] = qty || 1;
    await saveEquipped();
    console.log('quickEquip: equipped saved, renderEquipped done');
  } catch(e) {
    console.error('quickEquip error:', e);
  }
}

// Quick-unequip to inventory — clicks the ⬇️ button
async function quickUnequip(name, qty) {
  console.log('quickUnequip called:', name, qty);
  try {
    // 1. Read enhancement before removing
    const eqItem = equippedItems.find(eq => (typeof eq === 'object' ? eq.name : eq) === name);
    const enhancement = (typeof eqItem === 'object' ? eqItem.enhancement : 0) || 0;
    // Remove from equipped array and save
    equippedItems = equippedItems.filter(eq => (typeof eq === 'object' ? eq.name : eq) !== name);
    delete _equippedQtys[name];
    await saveEquipped();
    console.log('quickUnequip: equipped saved');
    
    // 2. Add to inventory DOM and save
    addInventoryCard(name, qty || 1, enhancement);
    await _flushInventory();
    console.log('quickUnequip: inventory saved');
  } catch(e) {
    console.error('quickUnequip error:', e);
  }
}

function unequipItem(name) {
  equippedItems = equippedItems.filter(eq => (typeof eq === 'object' ? eq.name : eq) !== name);
  delete _equippedQtys[name];
  return saveEquipped();
}

async function saveEquippedFromDOM() {
  // Rebuild equippedItems from DOM cards (preserves enhancement data attributes)
  const items = [];
  document.querySelectorAll('#equipped-list .item-card').forEach(card => {
    const nameEl = card.querySelector('.item-card-name');
    const name = (nameEl?.textContent || '').trim();
    if (!name) return;
    const qtyBadge = card.querySelector('.item-qty');
    const qty = qtyBadge ? parseInt(qtyBadge.textContent) || 1 : 1;
    const enhancement = parseInt(card.dataset.enhancement || '0');
    items.push({ name, qty, enhancement });
  });
  equippedItems = items;
  // Update qty lookup
  Object.keys(_equippedQtys).forEach(k => delete _equippedQtys[k]);
  items.forEach(eq => { _equippedQtys[eq.name] = eq.qty; });
  await saveEquipped();
}

async function saveEquipped() {
  console.log('saveEquipped: equippedItems =', JSON.stringify(equippedItems));
  await sheetApi('/update', { equipped: JSON.stringify(equippedItems) });
  console.log('saveEquipped: api call done, calling renderEquipped');
  renderEquipped();
  refreshAttacksTab();
  // Refresh item badges
  tagArmor();
  tagWeapons();
}

// ── Refresh Actions tab weapon attacks ──
async function refreshAttacksTab() {
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/attacks`);
    const data = await resp.json();
    const card = document.getElementById('attacks-weapons-card');
    const list = document.getElementById('attacks-weapons-list');
    if (!card || !list) return;
    // Don't hide existing content on API error (auth, 404, etc.)
    if (data.error) {
      console.warn('refreshAttacksTab: API returned error:', data.error);
      return;
    }
    if (!data.attacks || data.attacks.length === 0) {
      card.style.display = 'none';
      list.innerHTML = '';
      return;
    }
    card.style.display = '';
    list.innerHTML = data.attacks.map(atk => {
      let propsHtml = '';
      if (atk.properties && atk.properties.length) {
        propsHtml = '<div class="atk-props">' + atk.properties.map(p => `<span class="atk-prop">${p}</span>`).join('') + '</div>';
      }
      const qtyBadge = (atk.qty && atk.qty > 1) ? ` <span class="item-qty" style="font-size:0.72rem;vertical-align:middle">${atk.qty}</span>` : '';
      const enhBadge = (atk.enhancement && atk.enhancement > 0) 
        ? ` <span class="enh-badge" style="font-size:0.9rem;padding:0 0.15rem;color:var(--accent);font-weight:700;border-radius:3px;background:var(--accent2)">+${atk.enhancement}</span>` 
        : '';
      const descHtml = atk.description ? `<div class="item-detail" data-loaded="true" style="margin-top:0.3rem;padding:0.5rem;background:var(--bg);border-radius:4px;font-size:0.75rem;color:var(--text-muted);line-height:1.4">${atk.description}</div>` : '';
      const srcHtml = atk.source ? `<div style="margin-top:0.2rem;text-align:right"><span class="src-badge" onclick="openSourceRef(this.dataset.src)" data-src="${atk.source.replace(/"/g, '&quot;')}" title="Click to open ${atk.source}" style="cursor:pointer;font-size:0.65rem;padding:0.1rem 0.4rem;border-radius:4px;background:var(--accent2);color:var(--accent)">📚 ${atk.source}</span></div>` : '';
      const hasMag = atk.max_charges && atk.max_charges > 0;
      const magHtml = hasMag ? `<div class="magazine-bar" style="display:flex;align-items:center;gap:0.5rem;margin-top:0.4rem">
        <span class="magazine-count" style="font-family:monospace;font-size:0.8rem;color:var(--text-muted)">${atk.current_charges}/${atk.max_charges}</span>
        <button class="mag-spend-btn" onclick="event.stopPropagation();spendCharge('${atk.name.replace(/'/g, "\\'")}')"
          style="padding:0.15rem 0.5rem;border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:0.7rem"
          ${atk.current_charges <= 0 ? 'disabled style="opacity:0.4"' : ''}>🔫 Fire</button>
        <button class="mag-reload-btn" onclick="event.stopPropagation();reloadCharge('${atk.name.replace(/'/g, "\\'")}')"
          style="padding:0.15rem 0.5rem;border-radius:4px;border:1px solid var(--accent);background:var(--accent2);color:var(--accent);cursor:pointer;font-size:0.7rem;font-weight:600">🔄 Reload</button>
      </div>` : '';
      const cardClasses = 'atk-card item-card' + (hasMag ? ' weapon-atk-card has-magazine' : '');
      const dataAttrs = `data-item-name="${atk.name}"` + (hasMag ? ` data-max-charges="${atk.max_charges}" data-charge-recharge="${atk.charge_recharge || ''}"` : '');
      return `<div class="${cardClasses}" ${dataAttrs}>
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <span style="display:flex;align-items:center;gap:0.3rem">
            <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
            <span class="atk-name">${atk.name}${qtyBadge}${enhBadge}</span>
          </span>
          <span style="font-family:monospace;font-size:0.85rem;color:var(--text-muted)">+${atk.attack_bonus} to hit</span>
        </div>
        <div class="atk-detail">
          ${atk.range ? `<span>Range: ${atk.range}</span>` : ''}
          ${atk.damage ? `<span> · ${atk.damage}</span>` : ''}
        </div>
        ${propsHtml}
        ${magHtml}
        ${descHtml}
        ${srcHtml}
      </div>`;
    }).join('');
    // Also update Unarmed Strike attack bonus if abilities changed
    const unarmedAtk = document.querySelector('#tab-actions .atk-card:nth-child(2) span[style*="monospace"]');
    // Unarmed is always the second atk-card now (first is weapons card or hidden)
  } catch(e) {
    console.error('refreshAttacksTab error:', e);
  }
}

// ── Feature Action (use limited-use feature from Actions in Combat) ──
async function useFeature(name, btn) {
  try {
    const data = await sheetApi('/use-feature', { name });
    if (!data.ok) return;
    // Update client-side featureData
    const feat = featureData.find(f => f.name === name);
    if (feat) feat.uses = data.uses;
    // Update the dots display on this button
    const dots = btn.querySelectorAll('.use-dot');
    for (let i = dots.length - 1; i >= 0; i--) {
      if (dots[i].classList.contains('active')) {
        dots[i].classList.remove('active');
        break;
      }
    }
    const countSpan = btn.querySelector('.feat-uses-dots span:last-child');
    if (countSpan) {
      countSpan.textContent = `${data.uses}/${dots.length}`;
    }
    if (data.uses === 0) {
      btn.classList.add('depleted');
      btn.style.opacity = '0.45';
      btn.style.cursor = 'default';
      btn.onclick = null;
    }
    // Sync features tab dots via render
    const idx = featureData.indexOf(feat);
    if (idx >= 0) renderFeatureDots(idx);
  } catch(e) { console.error(e); }
}

// ── Charged Items ── click card to spend charges ──
function chargeStep(btn, delta) {
  const stepper = btn.closest('.charge-stepper');
  const display = stepper.querySelector('.charge-amount-display');
  const card = btn.closest('.charge-card');
  const maxCharge = parseInt(card.dataset.maxCharges);
  const currentCharge = parseInt(card.dataset.currentCharges);
  const available = Math.min(maxCharge, currentCharge);
  let val = parseInt(display.textContent) + delta;
  if (val < 1) val = 1;
  if (val > available) val = available;
  display.textContent = val;
}

async function spendCharge(itemName, amount) {
  amount = amount || 1;
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/spend-charge`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: itemName, amount: amount})
    });
    const data = await resp.json();
    if (!data.charged_items) return;

    updateChargeCards(data.charged_items);
  } catch(e) { console.error('spendCharge error:', e); }
}

async function reloadCharge(itemName) {
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/reload-charge`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: itemName})
    });
    const data = await resp.json();
    if (!data.charged_items) return;

    updateChargeCards(data.charged_items);
  } catch(e) { console.error('reloadCharge error:', e); }
}

function updateChargeCards(chargedItems) {
  chargedItems.forEach(ci => {
    // Update Charged Items cards (wands, staves, etc.)
    document.querySelectorAll('.charge-card').forEach(card => {
      if (card.dataset.itemName === ci.name) {
        updateSingleChargeCard(card, ci);
      }
    });
    // Update Weapon Attack cards with magazines (firearms)
    document.querySelectorAll('.weapon-atk-card.has-magazine').forEach(card => {
      if (card.dataset.itemName === ci.name) {
        updateWeaponMagazineCard(card, ci);
      }
    });
  });
  window._chargedItems = chargedItems;
}

function updateSingleChargeCard(card, ci) {
  // Update charge count display
  const countEl = card.querySelector('.charge-count-display');
  if (countEl) {
    countEl.textContent = `${ci.current_charges}/${ci.max_charges} charges`;
  }
  // Update data attributes
  card.dataset.currentCharges = ci.current_charges;
  card.dataset.maxCharges = ci.max_charges;
  const recharge = card.dataset.recharge || '';
  const isReload = recharge.startsWith('reload');
  const multiCharge = ci.max_charges > 1;
  // Update stepper visibility and reset amount to 1
  let stepper = card.querySelector('.charge-stepper');
  if (multiCharge && ci.current_charges > 0) {
    if (!stepper) {
      // Rebuild stepper if missing (e.g. after depletion → recharge)
      stepper = document.createElement('div');
      stepper.className = 'charge-stepper';
      stepper.style.cssText = 'display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;justify-content:flex-end';
      stepper.onclick = (e) => e.stopPropagation();
      stepper.innerHTML = `<button class="charge-amount-btn" style="padding:0.1rem 0.4rem;border-radius:3px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:0.75rem;line-height:1">−</button><span class="charge-amount-display" style="font-family:monospace;font-size:0.8rem;min-width:1.2rem;text-align:center">1</span><button class="charge-amount-btn" style="padding:0.1rem 0.4rem;border-radius:3px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:0.75rem;line-height:1">+</button><button class="charge-spend-btn" style="padding:0.15rem 0.5rem;border-radius:4px;border:1px solid var(--accent);background:var(--accent2);color:var(--accent);cursor:pointer;font-size:0.7rem;font-weight:600">⚡ Spend</button>`;
      stepper.querySelector('.charge-amount-btn:first-child').onclick = (e) => { e.stopPropagation(); chargeStep(e.target, -1); };
      stepper.querySelector('.charge-amount-btn:last-child').onclick = (e) => { e.stopPropagation(); chargeStep(e.target, 1); };
      stepper.querySelector('.charge-spend-btn').onclick = (e) => {
        e.stopPropagation();
        spendCharge(card.dataset.itemName, parseInt(stepper.querySelector('.charge-amount-display').textContent));
      };
      card.appendChild(stepper);
    }
    stepper.style.display = '';
    const disp = stepper.querySelector('.charge-amount-display');
    if (disp) disp.textContent = '1';
  } else if (stepper) {
    stepper.style.display = 'none';
  }
  if (ci.current_charges <= 0) {
    card.classList.add('depleted');
    if (isReload) {
      card.style.opacity = '';
      card.style.cursor = 'pointer';
      card.onclick = function() { reloadCharge(this.dataset.itemName); };
      let reloadRow = card.querySelector('.reload-btn-row');
      if (!reloadRow) {
        reloadRow = document.createElement('div');
        reloadRow.className = 'reload-btn-row';
        reloadRow.style.cssText = 'margin-top:0.3rem;text-align:right';
        reloadRow.innerHTML = `<button class="reload-btn" style="padding:0.2rem 0.6rem;border-radius:4px;border:1px solid var(--accent);background:var(--accent2);color:var(--accent);cursor:pointer;font-size:0.7rem;font-weight:600">🔄 Reload</button>`;
        reloadRow.querySelector('.reload-btn').onclick = function(e) {
          e.stopPropagation();
          reloadCharge(card.dataset.itemName);
        };
        card.appendChild(reloadRow);
      }
      reloadRow.style.display = '';
    } else {
      card.style.opacity = '0.45';
      card.style.cursor = 'default';
      card.onclick = null;
      const reloadRow = card.querySelector('.reload-btn-row');
      if (reloadRow) reloadRow.style.display = 'none';
    }
  } else {
    card.classList.remove('depleted');
    card.style.opacity = '';
    card.style.cursor = 'pointer';
    card.onclick = function() { spendCharge(this.dataset.itemName); };
    const reloadRow = card.querySelector('.reload-btn-row');
    if (reloadRow) reloadRow.style.display = 'none';
  }
}

function updateWeaponMagazineCard(card, ci) {
  // Update magazine count
  const countEl = card.querySelector('.magazine-count');
  if (countEl) {
    countEl.textContent = `${ci.current_charges}/${ci.max_charges}`;
  }
  // Update Fire button
  const fireBtn = card.querySelector('.mag-spend-btn');
  if (fireBtn) {
    if (ci.current_charges <= 0) {
      fireBtn.disabled = true;
      fireBtn.style.opacity = '0.4';
    } else {
      fireBtn.disabled = false;
      fireBtn.style.opacity = '';
    }
  }
}

