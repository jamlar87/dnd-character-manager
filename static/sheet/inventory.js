/* sheet/inventory.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Inventory Management ──
const WEAPON_KEYWORDS = [
  'sword', 'axe', 'hammer', 'bow', 'dagger', 'mace', 'spear', 'flail',
  'rapier', 'scimitar', 'glaive', 'halberd', 'pike', 'lance', 'whip',
  'club', 'staff', 'quarterstaff', 'crossbow', 'sling', 'dart', 'javelin',
  'trident', 'war pick', 'morningstar', 'greatsword', 'longsword', 'shortsword',
  'battleaxe', 'greataxe', 'warhammer', 'maul', 'handaxe', 'light hammer',
  'sickle', 'blowgun', 'net', 'firearm', 'pistol', 'musket', 'rifle',
  'arrow', 'bolt', 'blade', 'knife', 'katana', 'nunchaku', 'shuriken',
];

function isWeapon(name) {
  const lower = name.toLowerCase().trim();
  if (NAMED_ITEM_TYPES[lower] === 'wpn') return true;
  return WEAPON_KEYWORDS.some(kw => lower.includes(kw));
}

// Weapon info lookup — returns {category: 'Simple'|'Martial'} or null
const _WEAPON_CATEGORIES = {
  // Simple Melee
  'club':'Simple','dagger':'Simple','greatclub':'Simple','handaxe':'Simple',
  'javelin':'Simple','light hammer':'Simple','mace':'Simple','quarterstaff':'Simple',
  'sickle':'Simple','spear':'Simple',
  // Simple Ranged
  'light crossbow':'Simple','dart':'Simple','shortbow':'Simple','sling':'Simple',
  // Martial Melee
  'battleaxe':'Martial','flail':'Martial','glaive':'Martial','greataxe':'Martial',
  'greatsword':'Martial','halberd':'Martial','lance':'Martial','longsword':'Martial',
  'maul':'Martial','morningstar':'Martial','pike':'Martial','rapier':'Martial',
  'scimitar':'Martial','shortsword':'Martial','trident':'Martial','war pick':'Martial',
  'warhammer':'Martial','whip':'Martial',
  // Martial Ranged
  'blowgun':'Martial','hand crossbow':'Martial','heavy crossbow':'Martial',
  'longbow':'Martial','net':'Martial',
};
function getWeaponInfo(name) {
  if (!name) return null;
  const lower = name.toLowerCase().trim();
  // Check server-side classification first
  if (NAMED_ITEM_TYPES[lower] === 'wpn') return {category: 'Simple'};
  // Direct match
  if (_WEAPON_CATEGORIES[lower]) return {category: _WEAPON_CATEGORIES[lower]};
  // Try singular form (strip trailing 's')
  if (lower.endsWith('s') && !lower.endsWith('ss')) {
    const singular = lower.slice(0, -1);
    if (_WEAPON_CATEGORIES[singular]) return {category: _WEAPON_CATEGORIES[singular]};
  }
  // Fallback: keyword match
  if (isWeapon(name)) {
    // Default to Simple for generic weapon-like items
    return {category: 'Simple'};
  }
  return null;
}

function tagWeapons() {
  // Read current proficiencies from display
  const weaponProfs = getCurrentProfs('weapon_proficiencies');
  const hasSimple = weaponProfs.some(p => p.toLowerCase() === 'simple weapons');
  const hasMartial = weaponProfs.some(p => p.toLowerCase() === 'martial weapons');
  
  document.querySelectorAll('#inv-list .item-card, #equipped-list .item-card, #team-items-list .item-card').forEach(card => {
    const input = card.querySelector('.item-card-name');
    const badge = card.querySelector('.wpn-badge');
    const npBadge = card.querySelector('.np-badge');
    if (!input || !badge) return;
    const name = (input.value || input.textContent || '').trim();
    const wpnInfo = getWeaponInfo(name);
    // Show enhancement badge for weapons (always visible for weapon/armor items)
    const enhBadge = card.querySelector('.enh-badge');
    if (enhBadge) {
      const enh = parseInt(card.dataset.enhancement || '0');
      if (wpnInfo) {
        enhBadge.textContent = '+' + enh;
        enhBadge.style.display = '';
      } else if (!isArmor(name)) {
        enhBadge.style.display = 'none';
      }
    }
    if (wpnInfo) {
      badge.style.display = 'inline-block';
      // Check proficiency
      let proficient = weaponProfs.some(p => p.toLowerCase() === name.toLowerCase());
      if (!proficient && wpnInfo.category === 'Simple' && hasSimple) proficient = true;
      if (!proficient && wpnInfo.category === 'Martial' && hasMartial) proficient = true;
      if (!proficient && npBadge) {
        npBadge.style.display = 'inline-block';
        npBadge.textContent = '⚠️ Non-Proficient';
      } else if (npBadge) {
        npBadge.style.display = 'none';
      }
    } else {
      badge.style.display = 'none';
      if (npBadge) npBadge.style.display = 'none';
    }
  });
  tagArmor();
}

// ── Armor Detection ──
const _ARMOR_KEYWORDS = [
  'padded', 'leather', 'studded', 'hide', 'chain shirt', 'scale mail',
  'breastplate', 'half plate', 'ring mail', 'chain mail', 'splint',
  'plate', 'shield', 'armor',
];
function isArmor(name) {
  const lower = name.toLowerCase().trim();
  // Check server-side classification first
  if (NAMED_ITEM_TYPES[lower] === 'arm') return true;
  if (ARMOR_NAMES.some(n => n === lower)) return true;
  if (lower.endsWith('s') && !lower.endsWith('ss')) {
    const singular = lower.slice(0, -1);
    if (ARMOR_NAMES.some(n => n === singular)) return true;
  }
  if (_ARMOR_KEYWORDS.some(kw => lower.includes(kw))) return true;
  return false;
}

function isBodyArmor(name) {
  return isArmor(name) && !name.toLowerCase().includes('shield');
}

// ── Enhancement bonus cycling (+0 ↔ +1 ↔ +2 ↔ +3) ──
function isWeaponOrArmor(name) {
  if (!name) return false;
  if (isArmor(name)) return true;
  return !!getWeaponInfo(name);
}

function cycleEnhancement(badge) {
  if (!document.body.classList.contains('edit-mode')) return;
  const card = badge.closest('.item-card');
  let enh = parseInt(card.dataset.enhancement || '0');
  enh = (enh + 1) % 4;  // 0 → 1 → 2 → 3 → 0
  card.dataset.enhancement = enh;
  badge.textContent = '+' + enh;
  badge.style.display = '';
  // Auto-save
  const inEquipped = card.closest('#equipped-list');
  if (inEquipped) {
    saveEquippedFromDOM();
  } else {
    autoSaveInventory();
  }
}

function isShieldItem(name) {
  return isArmor(name) && name.toLowerCase().includes('shield');
}

// Maps armor names to proficiency categories
function getArmorCategory(armorName) {
  const lower = armorName.toLowerCase().trim();
  const light = ['padded armor', 'padded', 'leather armor', 'leather', 'studded leather armor', 'studded leather'];
  const medium = ['hide armor', 'hide', 'chain shirt', 'scale mail', 'breastplate', 'half plate armor', 'half plate'];
  const heavy = ['ring mail', 'chain mail', 'splint armor', 'splint', 'plate armor', 'plate'];
  if (light.some(n => lower.includes(n))) return 'Light armor';
  if (medium.some(n => lower.includes(n))) return 'Medium armor';
  if (heavy.some(n => lower.includes(n))) return 'Heavy armor';
  return null;
}

function isArmorProficient(armorName) {
  const armors = getCurrentProfs('armor_proficiencies');
  const lower = armorName.toLowerCase().trim();
  // Direct match
  if (armors.some(a => a.toLowerCase() === lower)) return true;
  // Category match
  const cat = getArmorCategory(armorName);
  if (cat && armors.some(a => a.toLowerCase() === cat.toLowerCase())) return true;
  // Shield check
  if (lower.includes('shield') && armors.some(a => a.toLowerCase() === 'shields')) return true;
  return false;
}

function tagArmor() {
  const armors = getCurrentProfs('armor_proficiencies');
  document.querySelectorAll('#inv-list .item-card, #equipped-list .item-card, #team-items-list .item-card').forEach(card => {
    const input = card.querySelector('.item-card-name');
    const badge = card.querySelector('.arm-badge');
    const npBadge = card.querySelector('.np-badge');
    if (!input || !badge) return;
    const name = (input.value || input.textContent || '').trim();
    if (isArmor(name)) {
      badge.style.display = 'inline-block';
      // Show enhancement badge for armor (always visible)
      const enhBadge = card.querySelector('.enh-badge');
      if (enhBadge) {
        const enh = parseInt(card.dataset.enhancement || '0');
        enhBadge.textContent = '+' + enh;
        enhBadge.style.display = '';
      }
      if (!isArmorProficient(name) && npBadge) {
        npBadge.style.display = 'inline-block';
        npBadge.textContent = '⚠️ Non-Proficient';
      } else if (npBadge) {
        npBadge.style.display = 'none';
      }
    } else {
      badge.style.display = 'none';
    }
  });
  // Also update AC warning
  updateAcWarning();
}

function updateAcWarning() {
  const acEl = document.querySelector('.cq-value[data-field="ac"]');
  if (!acEl) return;
  const armorProfs = getCurrentProfs('armor_proficiencies');
  // Find equipped armor
  const equipped = (equippedItems || []).map(eq => typeof eq === 'object' ? eq.name : eq);
  const wornArmor = equipped.find(item => isBodyArmor(item));
  const wornShield = equipped.find(item => isShieldItem(item));
  
  let warning = '';
  if (wornArmor && !isArmorProficient(wornArmor)) {
    warning = ' ⚠️ (non-proficient: disadv on STR/DEX, no spells)';
  }
  // Only add warning, don't change AC value
  const existingWarning = acEl.querySelector('.ac-warn');
  if (warning && !existingWarning) {
    const span = document.createElement('span');
    span.className = 'ac-warn';
    span.style.cssText = 'font-size:0.72rem;color:var(--warn);font-weight:400;display:block;white-space:normal;max-width:200px';
    span.textContent = warning;
    acEl.appendChild(span);
  } else if (!warning && existingWarning) {
    existingWarning.remove();
  }
}

