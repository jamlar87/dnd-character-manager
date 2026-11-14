/* sheet/popups.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Detail Popup ──
const DESCRIPTIONS = {
  race: SHEETREF.races,
  class: SHEETREF.classes,
  bg: SHEETREF.bgInfo
};
const CLASS_LEVELS = SHEETCFG.classLevels;

const ARMOR_NAMES = SHEETCFG.armorNames;  // from Campaign Expert equipment_data
const NAMED_ITEM_TYPES = SHEETREF.namedItemTypes;  // server-side weapon/armor classification

// ── Action Detail Popup ──
function showDetail() {
  if (document.body.classList.contains('edit-mode')) return;
  const popup = document.getElementById('detail-popup');
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');

  const race = SHEETCFG.charRaceJson;
  const subrace = SHEETCFG.charSubraceJson;
  const cls = SHEETCFG.charClassNameJson;
  const subclass = SHEETCFG.charSubclassJson;
  const bg = SHEETCFG.charBackgroundJson;
  const align = SHEETCFG.charAlignmentJson;
  const bgData = SHEETCFG.charBackgroundData;

  title.textContent = SHEETCFG.charNameJson;

  let html = '';
  // Case-insensitive race lookup: try exact first, then lowercase
  const raceData = DESCRIPTIONS.race[race] || DESCRIPTIONS.race[race.toLowerCase()];
  const classData = DESCRIPTIONS.class[cls];
  const bgDesc = DESCRIPTIONS.bg[bg];

  // Race
  const effectiveSubrace = subrace || raceData?.subrace || '';
  const raceSrc = raceData?.source || '';
  html += `<div style="margin-bottom:1rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong style="color:var(--accent)">${race}</strong>${raceSrc ? ` ${_srcBadge(raceSrc)}` : ''}</div>`;
  if (raceData?.desc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${raceData.desc}</p>`;
  if (effectiveSubrace) {
    const srSrc = (raceData?._subrace_sources || {})[effectiveSubrace] || '';
    html += `<div style="margin-top:0.5rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong>${effectiveSubrace}</strong>${srSrc ? ` ${_srcBadge(srSrc)}` : ''}</div>`;
    const srDesc = (raceData?.subrace_descs || {})[effectiveSubrace];
    if (srDesc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${srDesc}</p>`;
    html += '</div>';
  }
  html += '</div>';

  // Find which class owns the subclass (may differ from primary for multiclass)
  let subclassParent = null;
  if (subclass) {
    for (const [cName] of Object.entries(CLASS_LEVELS)) {
      const cd = DESCRIPTIONS.class[cName];
      if (cd && (cd.subclasses || []).includes(subclass)) {
        subclassParent = cName;
        break;
      }
    }
  }

  // Primary class
  const classSrc = classData?.source || '';
  html += `<div style="margin-bottom:1rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong style="color:var(--accent)">${cls} ${CLASS_LEVELS[cls] || ''}</strong>${classSrc ? ` ${_srcBadge(classSrc)}` : ''}</div>`;
  if (classData?.desc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${classData.desc}</p>`;
  // Subclass — only if it belongs to primary
  if (subclass && cls === subclassParent) {
    const scSrc = (classData?._subclass_sources || {})[subclass] || '';
    html += `<div style="margin-top:0.5rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong>${subclass}</strong>${scSrc ? ` ${_srcBadge(scSrc)}` : ''}</div>`;
    const scDesc = (classData?.subclass_descs || {})[subclass];
    if (scDesc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${scDesc}</p>`;
    html += '</div>';
  }
  html += '</div>';

  // Additional multiclass classes
  for (const [mcName, mcLevel] of Object.entries(CLASS_LEVELS)) {
    if (mcName === cls) continue;  // skip primary
    const mcData = DESCRIPTIONS.class[mcName];
    if (!mcData) continue;
    const mcSrc = mcData.source || '';
    html += `<div style="margin-bottom:1rem;border-top:1px solid var(--border);padding-top:0.75rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong style="color:var(--accent)">${mcName} ${mcLevel}</strong>${mcSrc ? ` ${_srcBadge(mcSrc)}` : ''}</div>`;
    if (mcData.desc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${mcData.desc}</p>`;
    // Subclass — only if it belongs to this multiclass
    if (subclass && mcName === subclassParent) {
      const scSrc = (mcData?._subclass_sources || {})[subclass] || '';
      html += `<div style="margin-top:0.5rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong>${subclass}</strong>${scSrc ? ` ${_srcBadge(scSrc)}` : ''}</div>`;
      const scDesc = (mcData?.subclass_descs || {})[subclass];
      if (scDesc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${scDesc}</p>`;
      html += '</div>';
    }
    html += '</div>';
  }

  // Background
  if (bg) {
    const bgSrc = (bgData && bgData.source) ? bgData.source : '';
    html += `<div style="margin-bottom:1rem;border-top:1px solid var(--border);padding-top:0.75rem"><div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem"><strong style="color:var(--accent)">Background: ${bg}</strong>${bgSrc ? ` ${_srcBadge(bgSrc)}` : ''}</div>`;
    const desc = (bgData && bgData.description) ? bgData.description : bgDesc;
    if (desc) html += `<p style="margin:0.25rem 0 0 0;font-size:0.85rem;color:var(--text-muted)">${desc}</p>`;
    if (bgData && bgData.items && bgData.items.length) html += `<p style="margin:0.25rem 0 0 0;font-size:0.8rem;color:var(--text-muted)">Items: ${bgData.items.join(', ')} · ${bgData.gp || 15} gp</p>`;
    html += '</div>';
  }

  // Alignment
  if (align) {
    html += `<div style="font-size:0.85rem;color:var(--text-muted);border-top:1px solid var(--border);padding-top:0.75rem"><strong>Alignment:</strong> ${align}</div>`;
  }

  body.innerHTML = html;
  popup.style.display = 'flex';
}

// ── Action Detail Popup ──
const ACTION_DESCRIPTIONS = {
  "Attack": "Make one melee or ranged attack. Certain features, such as Extra Attack, allow multiple attacks as part of this action. You can also substitute a grapple or shove for one of your attacks if you have the Extra Attack feature. (PHB p.192, 195)",
  "Dash": "Gain extra movement equal to your speed (after applying any modifiers) for the current turn. If you have a speed of 30 feet, you can move up to 60 feet on your turn when you Dash. (PHB p.192)",
  "Disengage": "Your movement does not provoke opportunity attacks for the rest of the turn. Use this to safely retreat from melee or reposition without drawing attacks. (PHB p.192)",
  "Dodge": "Until the start of your next turn, any attack roll made against you has disadvantage if you can see the attacker, and you make Dexterity saving throws with advantage. You lose this benefit if you are incapacitated or your speed drops to 0. (PHB p.192)",
  "Grapple": "Attempt to grab a creature. Make a Strength (Athletics) check contested by the target's Strength (Athletics) or Dexterity (Acrobatics). You must have at least one free hand, and the target must be no more than one size larger than you. On success, the target is grappled (speed becomes 0). You can release the grapple at any time (no action). (PHB p.195)",
  "Help": "Aid another creature with a task or in attacking an enemy. The target gains advantage on the next ability check it makes to perform the task you are helping with. Alternatively, you can aid a friendly creature in attacking a creature within 5 feet of you — the ally's first attack roll against that creature before your next turn has advantage. (PHB p.192)",
  "Hide": "Make a Dexterity (Stealth) check to become hidden. You can't hide from a creature that can see you clearly, and you give away your position if you make noise or approach. While hidden, you have advantage on attack rolls against creatures that can't see you, and they have disadvantage on attacks against you. (PHB p.192, 194-195)",
  "Improvise": "Attempt any action not covered by the other standard options. Describe what you want to do, and the DM decides whether it's possible and what kind of ability check (if any) is required. Examples: intimidate an enemy, block a doorway, swing from a chandelier. (PHB p.193)",
  "Ready": "Choose a trigger you can perceive and a response action you will take when the trigger occurs. When the trigger happens, you can use your reaction to perform the readied action right after the trigger finishes, or you can ignore the trigger. If you Ready a spell, you must cast it as normal (expending the slot) and hold its energy using concentration — if your concentration is broken, the spell dissipates without effect. (PHB p.193)",
  "Search": "Devote your attention to finding something. The DM might call for a Wisdom (Perception) check to spot a hidden creature or object, or an Intelligence (Investigation) check to deduce clues from examining an area. (PHB p.193)",
  "Shove": "Attempt to push a creature 5 feet away from you or knock it prone. Make a Strength (Athletics) check contested by the target's Strength (Athletics) or Dexterity (Acrobatics). The target must be no more than one size larger than you. You have disadvantage if the target is two or more sizes larger. (PHB p.195)",
  "Study": "Deduce information or recall lore about a creature, object, or phenomenon. Make an Intelligence check appropriate to the knowledge area: Arcana (magic, planes), History (past events, wars), Nature (terrain, beasts), or Religion (deities, holy rites). The DM sets the DC based on how obscure the knowledge is. (2024 PHB — Action)",
  "Utilize": "Use a nonmagical object or interact with the environment in a way that requires your action. Examples: drink a potion, pull a lever, open a stuck door, light a torch, or activate a magic item that requires an action. Simple object interactions (drawing a weapon, opening an unlocked door) can typically be done for free once per turn as part of your move or action. (2024 PHB — Utilize Action)"
};

function showActionDetail(action) {
  const popup = document.getElementById('detail-popup');
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');
  const desc = ACTION_DESCRIPTIONS[action] || 'No description available.';
  title.textContent = action;
  body.innerHTML = `<p style="margin:0;font-size:0.9rem;line-height:1.5;color:var(--text)">${desc}</p>`;
  popup.style.display = 'flex';
}

function showReactionDetail(featureName, description) {
  const popup = document.getElementById('detail-popup');
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');
  title.textContent = featureName;
  body.innerHTML = `<p style="margin:0;font-size:0.9rem;line-height:1.5;color:var(--text)">${description}</p>`;
  popup.style.display = 'flex';
}

// ── Ability Score Descriptions ──
const ABILITY_DESCRIPTIONS = {
  strength: "Measures bodily power, athletic training, and the extent to which you can exert raw physical force. Strength checks include forcing open a stuck door, pushing through a tight tunnel, hanging onto a wagon while being dragged, tipping over a statue, or keeping a boulder from rolling. The Athletics skill reflects aptitude in certain kinds of Strength checks. Your Strength score determines your carrying capacity (Strength × 15 lbs) and how much you can push, drag, or lift. Most melee weapons use Strength for attack and damage rolls. (PHB p.175–176)",
  dexterity: "Measures agility, reflexes, and balance. Dexterity checks include staying on your feet on a rocking ship, picking a lock, disabling a trap, securely tying up a prisoner, playing a stringed instrument, or crafting a small or detailed object. The Acrobatics, Sleight of Hand, and Stealth skills reflect aptitude in certain kinds of Dexterity checks. Your Dexterity modifier contributes to your Armor Class (up to your armor's limit), initiative rolls, and attack and damage rolls with ranged and finesse weapons. (PHB p.176–177)",
  constitution: "Measures health, stamina, and vital force. Constitution checks include holding your breath, marching or laboring for hours without rest, going without sleep, surviving without food or water, or drinking an entire stein of ale in one go. No skills apply directly to Constitution checks, but Constitution is critical for maintaining concentration on spells (Constitution saving throw when you take damage). Your Constitution modifier is added to your hit point maximum each time you gain a level. (PHB p.177)",
  intelligence: "Measures mental acuity, accuracy of recall, and the ability to reason. Intelligence checks include piecing together clues, recalling lore about a craft or trade, winning a game of skill, estimating the value of an item, forging a document, or communicating with a creature without using words. The Arcana, History, Investigation, Nature, and Religion skills reflect aptitude in certain kinds of Intelligence checks. Wizards use Intelligence for spellcasting. (PHB p.177–178)",
  wisdom: "Measures how attuned you are to the world around you and represents perceptiveness and intuition. Wisdom checks include reading body language, understanding someone's feelings, noticing things in the environment, caring for an injured person, discerning the weather, or following tracks. The Animal Handling, Insight, Medicine, Perception, and Survival skills reflect aptitude in certain kinds of Wisdom checks. Clerics, Druids, and Rangers use Wisdom for spellcasting. (PHB p.178)",
  charisma: "Measures your ability to interact effectively with others — confidence, eloquence, and leadership. Charisma checks include persuading someone to do you a favor, winning a duel of wits, finding the best person to talk to for information, blending into a crowd, or delivering a speech to an audience. The Deception, Intimidation, Performance, and Persuasion skills reflect aptitude in certain kinds of Charisma checks. Bards, Paladins, Sorcerers, and Warlocks use Charisma for spellcasting. (PHB p.178–179)"
};

function showAbilityPopup(stat, label) {
  if (document.body.classList.contains('edit-mode')) return;
  const popup = document.getElementById('detail-popup');
  if (!popup) return;
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');
  if (!title || !body) return;
  const desc = ABILITY_DESCRIPTIONS[stat] || 'No description available.';
  title.textContent = label + ' — ' + stat.charAt(0).toUpperCase() + stat.slice(1);
  body.innerHTML = `<p style="margin:0;font-size:0.9rem;line-height:1.5;color:var(--text)">${desc}</p>`;
  popup.style.display = 'flex';
}

// ── Combat Stat Descriptions ──
const COMBAT_DESCRIPTIONS = {
  ac: "Represents how hard you are to hit in combat. Your base AC depends on armor: unarmored is 10 + Dexterity modifier, light armor adds your full Dex modifier, medium armor caps at +2 from Dex, and heavy armor ignores Dex entirely. Shields add +2. A monster must roll equal to or higher than your AC on an attack roll to hit you. Some class features (Unarmored Defense) and spells (Mage Armor, Shield of Faith) provide alternative AC calculations. (PHB p.14, 144–145)",
  initiative: "Determines the order of turns during combat. At the start of a fight, every creature makes a Dexterity check (d20 + Dex modifier). The DM ranks results highest to lowest — that's the turn order. Ties are broken by Dexterity score, then by a coin flip. Some features modify initiative: the Alert feat adds +5, the Bard's Jack of All Trades adds half proficiency, and certain magic items grant advantage. Initiative stays fixed for the entire combat unless a readied action or special effect changes it. (PHB p.177, 189)",
  speed: "How far you can move on your turn. Most races have a walking speed of 30 feet. You can break up your movement before and after your action, and use all or part of it. Difficult terrain (rubble, undergrowth, steep stairs) costs 2 feet for every 1 foot moved. You can climb, swim, or crawl (each costs extra unless you have a special speed) and you can jump as part of your move. If you have multiple speeds (e.g. walking 30 ft, flying 60 ft), you can switch between them during your move, subtracting the distance already covered. (PHB p.181–182, 190)",
  prof: "Added to any d20 roll you make with something you're proficient in — attack rolls with trained weapons, ability checks with proficient skills/tools, and proficient saving throws. It also sets the DC for your spells and class features (8 + proficiency + ability modifier). Starts at +2 at level 1 and increases at levels 5, 9, 13, and 17, capping at +6. Independent of ability scores — it represents training and experience. (PHB p.12–13, 15)",
  inspiration: "A special resource the DM awards for clever thinking, heroic acts, or exceptional roleplaying. You either have inspiration or you don't — it doesn't stack. When you have inspiration, you can spend it to gain advantage on one attack roll, saving throw, or ability check. You can also give your inspiration to another player if they do something impressive. Inspiration encourages dramatic choices and character-driven play. Click the ⭐ to toggle. (PHB p.125)"
};

function showCombatPopup(key) {
  if (document.body.classList.contains('edit-mode')) return;
  const names = { ac: 'AC — Armor Class', initiative: 'Initiative', speed: 'Speed', prof: 'Proficiency Bonus', inspiration: 'Inspiration' };
  const popup = document.getElementById('detail-popup');
  if (!popup) return;
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');
  if (!title || !body) return;
  const desc = COMBAT_DESCRIPTIONS[key] || 'No description available.';
  title.textContent = names[key] || key;
  body.innerHTML = `<p style="margin:0;font-size:0.9rem;line-height:1.5;color:var(--text)">${desc}</p>`;
  popup.style.display = 'flex';
}

// ── Curse spoiler section for item descriptions ──
function _curseSectionHtml(curseText) {
  if (!curseText) return '';
  const safe = curseText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<details style="margin-top:0.5rem;border:1px solid var(--danger, #e74c3c);border-radius:6px;padding:0.3rem 0.5rem;background:rgba(231,76,60,0.08);cursor:pointer">
    <summary style="font-size:0.7rem;font-weight:600;color:var(--danger, #e74c3c);user-select:none">
      ⚠️ Curse (DM permission to reveal)
    </summary>
    <div style="margin-top:0.3rem;font-size:0.7rem;line-height:1.4;color:var(--text-danger, #c0392b)">${safe}</div>
  </details>`;
}

// ── Item Info Popup ──
async function showItemInfo(name) {
  if (!name || !name.trim()) return;
  const popup = document.getElementById('detail-popup');
  const title = document.getElementById('popup-title');
  const body = document.getElementById('popup-body');
  
  title.textContent = name;
  body.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Loading...</p>';
  popup.style.display = 'flex';
  
  try {
    const resp = await fetch(`/api/items/describe?name=${encodeURIComponent(name.trim())}`);
    const data = await resp.json();
    title.textContent = data.name || name;
    const typeTag = data.type ? `<span style="font-size:0.7rem;color:var(--text-muted);margin-left:0.5rem">${data.type}</span>` : '';
    // Reference art, floated right — the same detail-view pattern as the DM
    // tools' item modal. refArtImg hides itself when no art exists yet, so an
    // un-backfilled or character-specific item name degrades to the old text popup.
    // The API's canonical name is preferred over the typed one: the art library is
    // keyed by canonical name, and inventory names are free text.
    body.innerHTML = `${refArtImg('item', data.name || name, 240)}<p style="margin:0;font-size:0.9rem;line-height:1.5;color:var(--text)">${data.description || 'No description available.'}</p>
      ${data.curse ? _curseSectionHtml(data.curse) : ''}
      <div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-muted);display:flex;justify-content:space-between;align-items:center">${typeTag}${data.source ? `${_srcBadge(data.source)}` : ''}</div>`;
  } catch(e) {
    body.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Unable to load description.</p>';
  }
}

// ── Expand/collapse item cards (fetches SRD info on first expand) ──
const _itemDetailCache = {};
async function toggleItemExpand(btn) {
  const card = btn.closest('.item-card');
  if (!card) return;
  const isExpanding = !card.classList.contains('expanded');
  card.classList.toggle('expanded');

  if (isExpanding) {
    const detail = card.querySelector('.item-detail');
    if (!detail || detail.dataset.loaded) return;
    const nameEl = card.querySelector('.item-card-name');
    const name = (nameEl?.value || nameEl?.textContent || '').trim();
    if (!name) return;

    // Use cache if available
    if (_itemDetailCache[name]) {
      const qtyBadge2 = card.querySelector('.item-qty');
      const currentQty2 = qtyBadge2 ? parseInt(qtyBadge2.textContent) || 1 : 1;
      detail.innerHTML = _itemDetailCache[name] + buildQtyRow(currentQty2);
      detail.dataset.loaded = '1';
      return;
    }

    detail.innerHTML = '<span style="color:var(--text-muted);font-size:0.65rem">Loading...</span>';
    try {
      const resp = await fetch(`/api/items/describe?name=${encodeURIComponent(name)}`);
      const data = await resp.json();
      const typeTag = data.type ? ` · ${data.type}` : '';
      const srcTag = data.source ? `${_srcBadge(data.source)}` : '';
      const qtyBadge = card.querySelector('.item-qty');
      const currentQty = qtyBadge ? parseInt(qtyBadge.textContent) || 1 : 1;
      const descHtml = `<span class="detail-label">${data.name || name}</span>${typeTag}${srcTag}
        <br><span style="color:var(--text);font-size:0.65rem;line-height:1.4">${data.description || 'No description available.'}</span>
        ${data.curse ? _curseSectionHtml(data.curse) : ''}`;
      _itemDetailCache[name] = descHtml;
      detail.innerHTML = descHtml + buildQtyRow(currentQty);
    } catch(e) {
      detail.innerHTML = '<span style="color:var(--text-muted);font-size:0.65rem">Unable to load details.</span>';
    }
    detail.dataset.loaded = '1';
  }
}

// ── Quantity input inside expanded item detail ──
function buildQtyRow(qty) {
  return `<div style="margin-top:0.4rem;display:flex;align-items:center;gap:0.3rem">
    <span class="detail-label">Qty</span>
    <button class="qty-stepper" onclick="event.stopPropagation();stepQty(this, -1)">−</button>
    <input type="number" class="qty-detail-input" value="${qty}" min="1" max="999"
      style="width:4ch;padding:0.1rem 0.2rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:0.65rem;-moz-appearance:textfield;appearance:textfield;text-align:center"
      onchange="updateItemQty(this)" onclick="event.stopPropagation()">
    <button class="qty-stepper" onclick="event.stopPropagation();stepQty(this, 1)">+</button>
  </div>`;
}

function stepQty(btn, delta) {
  const input = btn.parentElement.querySelector('.qty-detail-input');
  if (!input) return;
  let val = parseInt(input.value) || 1;
  val = Math.max(1, Math.min(999, val + delta));
  input.value = val;
  updateItemQty(input);
}

async function updateItemQty(input) {
  console.log('updateItemQty called');
  const card = input.closest('.item-card');
  if (!card) { console.log('updateItemQty: no card found'); return; }
  const qty = Math.max(1, parseInt(input.value) || 1);
  input.value = qty;
  console.log('updateItemQty: qty =', qty, 'card name =', (card.querySelector('.item-card-name')?.value || ''));

  // Update or remove qty badge on the card
  let badge = card.querySelector('.item-qty');
  if (qty > 1) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'item-qty';
      badge.style.cursor = 'pointer';
      badge.title = 'Click to expand';
      badge.onclick = function(e) { e.stopPropagation(); toggleItemExpand(card.querySelector('.item-expand-btn')); };
      const btn = card.querySelector('.item-card-btn');
      card.insertBefore(badge, btn);
    }
    badge.textContent = qty;
  } else if (badge) {
    badge.remove();
  }

  // Update drag data with new qty
  const nameEl = card.querySelector('.item-card-name');
  const name = (nameEl?.value || nameEl?.textContent || '').trim();
  card.setAttribute('ondragstart', `handleDragStart(event, '${name.replace(/'/g, "\\'")}', 'inventory', ${qty})`);

  await saveInventory();
}

