/* sheet/story.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── History & Relationships ─────────────────────────────────────────────

let relationships = [];

async function loadRelationships() {
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/relationships`);
    relationships = await resp.json();
    renderRelationships();
  } catch(e) { console.error(e); }
}

const REL_TYPE_ICONS = {ally:'🤝',mentor:'👨‍🏫',family:'👪',rival:'⚔️',antagonist:'💀',other:'📋'};
const REL_TYPE_LABELS = {ally:'Ally',mentor:'Mentor',family:'Family',rival:'Rival',antagonist:'Antagonist',other:'Other'};

function renderRelationships() {
  const el = document.getElementById('relationships-list');
  if (!relationships.length) {
    el.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem;text-align:center;padding:1rem">No relationships yet. Add NPCs from your character\'s past.</div>';
    return;
  }
  el.innerHTML = relationships.map(r => {
    const npc = JSON.parse(r.npc_data || '{}');
    const extra = npc.race ? `${npc.race} ${npc.class || ''} L${npc.level || '?'}` : '';
    const st = npc.stats || {};
    const hasStats = st.ac || st.hp || st.attacks;
    return `<div style="background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:0.75rem">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <strong>${REL_TYPE_ICONS[r.relationship_type] || '📋'} ${r.name}</strong>
          <span style="color:var(--accent);font-size:0.8rem;margin-left:0.5rem">${REL_TYPE_LABELS[r.relationship_type] || r.relationship_type}</span>
          ${extra ? `<span style="font-size:0.75rem;color:var(--text-muted);margin-left:0.5rem">${extra}</span>` : ''}
        </div>
        <div style="display:flex;gap:0.35rem;align-items:flex-start;flex-shrink:0">
          <button onclick="editRelationship(${r.id})" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:0.8rem;padding:0 0.15rem" title="Edit">✏️</button>
          <button onclick="deleteRelationship(${r.id})" style="background:none;border:none;color:var(--danger);cursor:pointer;font-size:0.8rem;padding:0 0.15rem" title="Delete">✕</button>
        </div>
      </div>
      ${r.description ? `<p style="margin:0.5rem 0 0 0;font-size:0.85rem;color:var(--text);line-height:1.4">${r.description}</p>` : ''}
      ${hasStats ? `<div style="margin-top:0.5rem;padding:0.5rem;background:var(--bg);border-radius:6px;font-size:0.75rem;font-family:monospace;line-height:1.5">` +
        (st.ac ? `AC ${st.ac}  HP ${st.hp || '?'}  Spd ${st.speed || '30 ft.'}` : '') +
        (st.str ? `<br>STR ${st.str} DEX ${st.dex} CON ${st.con} INT ${st.int} WIS ${st.wis} CHA ${st.cha}` : '') +
        (st.skills?.length ? `<br>Skills: ${st.skills.join(', ')}` : '') +
        (st.saves?.length ? `<br>Saves: ${st.saves.join(', ')}` : '') +
        (st.attacks?.length ? `<br>Attacks: ${st.attacks.map(a => `${a.name} ${a.bonus} ${a.damage} ${a.type||''}`).join(' | ')}` : '') +
        (st.cr ? `<br>CR ${st.cr}` : '') +
      `</div>` : ''}
      ${r.ai_generated ? '<span style="font-size:0.7rem;color:var(--text-muted)">🤖 AI-generated</span>' : ''}
    </div>`;
  }).join('');
}

let relGeneratedData = null;  // cached AI-generated NPC data for Save
let editingRelId = null;      // non-null when editing an existing relationship

function openAddRelationship() {
  relGeneratedData = null;
  editingRelId = null;
  document.getElementById('rel-modal-title').textContent = 'Add Relationship';
  document.getElementById('rel-type').value = 'ally';
  document.getElementById('rel-name').value = '';
  document.getElementById('rel-desc').value = '';
  document.getElementById('rel-prompt').value = '';
  document.getElementById('rel-use-ai').checked = false;
  document.getElementById('rel-ai-fields').style.display = 'none';
  document.getElementById('rel-manual-fields').style.display = '';
  document.getElementById('rel-gen-status').textContent = '';
  document.getElementById('rel-modal').style.display = 'flex';
}

function closeRelModal() {
  document.getElementById('rel-modal').style.display = 'none';
}

function editRelationship(relId) {
  const r = relationships.find(x => x.id === relId);
  if (!r) return;
  const npc = JSON.parse(r.npc_data || '{}');
  editingRelId = relId;
  relGeneratedData = null;
  document.getElementById('rel-modal-title').textContent = 'Edit Relationship';
  document.getElementById('rel-type').value = r.relationship_type || 'ally';
  document.getElementById('rel-name').value = r.name || '';
  document.getElementById('rel-desc').value = r.description || '';
  document.getElementById('rel-prompt').value = r.prompt || '';
  document.getElementById('rel-use-ai').checked = false;
  document.getElementById('rel-ai-fields').style.display = 'none';
  document.getElementById('rel-manual-fields').style.display = '';
  document.getElementById('rel-gen-status').textContent = '';
  document.getElementById('rel-modal').style.display = 'flex';
}

function toggleRelAIMode() {
  const ai = document.getElementById('rel-use-ai').checked;
  document.getElementById('rel-ai-fields').style.display = ai ? '' : 'none';
  document.getElementById('rel-manual-fields').style.display = ai ? 'none' : '';
}

async function generateRelationship() {
  const prompt = document.getElementById('rel-prompt').value.trim();
  if (!prompt) { alert('Describe the NPC you want'); return; }
  const status = document.getElementById('rel-gen-status');
  status.textContent = 'Generating...';
  const btn = document.querySelector('#rel-ai-fields .btn');
  btn.disabled = true;
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/relationships/generate`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: prompt,
        relationship_type: document.getElementById('rel-type').value,
        level: parseInt(document.getElementById('rel-npc-level').value) || 1,
      }),
    });
    const result = await resp.json();
    if (!resp.ok) {
      // Reading only `result.error` meant any HTTP error whose body uses FastAPI's `detail`
      // shape (404 not-owner, 403, 500) fell through: the name field was set to the string
      // "undefined" and the status line said "✓ Generated!" — a failure presented as AI output.
      alert(result.error || result.detail || ('HTTP ' + resp.status));
      return;
    }
    if (result.error) { alert(result.error); return; }
    if (result.ai_generated === false) {
      // The AI produced nothing; the server says so now instead of hardcoding ai_generated:true.
      alert(result.error || 'The AI could not generate this NPC — the fields are a placeholder.');
    }
    relGeneratedData = result;  // cache for Save
    document.getElementById('rel-name').value = result.name;
    document.getElementById('rel-desc').value = result.description || '';
    document.getElementById('rel-use-ai').checked = false;
    document.getElementById('rel-ai-fields').style.display = 'none';
    document.getElementById('rel-manual-fields').style.display = '';
    status.textContent = '✓ Generated!';
  } catch(e) { status.textContent = 'Failed'; console.error(e); }
  btn.disabled = false;
}

async function saveRelationship() {
  const name = document.getElementById('rel-name').value.trim();
  const desc = document.getElementById('rel-desc').value.trim();
  const prompt = document.getElementById('rel-prompt').value.trim();
  if (!name && !desc) { alert('Enter a name or description'); return; }
  const body = {
    name: name || prompt.substring(0, 50),
    relationship_type: document.getElementById('rel-type').value,
    description: desc,
    prompt: prompt,
  };
  if (relGeneratedData) {
    body.npc_data = relGeneratedData.npc_data;
    body.ai_generated = true;
  }
  const isEdit = !!editingRelId;
  const url = isEdit
    ? `/api/character/${CHAR_ID}/relationships/${editingRelId}`
    : `/api/character/${CHAR_ID}/relationships`;
  try {
    const resp = await fetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    // fetch only rejects on a network failure, so a rejected save (400/404/500) resolved normally
    // and the modal closed as if it had saved — the relationship was silently lost.
    if (!resp.ok) {
      const d = await resp.json().catch(function () { return {}; });
      alert(d.error || d.detail || ('Could not save the relationship (HTTP ' + resp.status + ')'));
      return;
    }
    relGeneratedData = null;
    editingRelId = null;
    closeRelModal();
    loadRelationships();
  } catch(e) { alert('Could not save the relationship: ' + (e.message || e)); console.error(e); }
}

async function deleteRelationship(relId) {
  if (!confirm('Delete this relationship?')) return;
  try {
    await fetch(`/api/character/${CHAR_ID}/relationships/${relId}`, {method: 'DELETE'});
    loadRelationships();
  } catch(e) { console.error(e); }
}

// ── Lock/unlock history backstory textarea ──
function getHistoryLockKey() {
  const m = window.location.pathname.match(/\/character\/(\d+)/);
  return m ? `history-lock-${m[1]}` : null;
}
function applyHistoryLock(locked) {
  const ta = document.getElementById('history-backstory');
  const btn = document.getElementById('history-lock-btn');
  const tools = document.getElementById('history-tools');
  if (!ta || !btn) return;
  if (locked) {
    ta.readOnly = true;
    ta.style.background = 'var(--bg-muted)';
    ta.style.opacity = '0.7';
    btn.textContent = '🔒';
    btn.title = 'Click to unlock editing';
    if (tools) tools.style.display = 'none';
  } else {
    ta.readOnly = false;
    ta.style.background = 'var(--bg)';
    ta.style.opacity = '1';
    btn.textContent = '🔓';
    btn.title = 'Click to lock editing';
    if (tools) tools.style.display = '';
  }
}
function toggleHistoryLock() {
  const key = getHistoryLockKey();
  if (!key) return;
  const locked = localStorage.getItem(key) === 'true';
  const newVal = !locked;
  localStorage.setItem(key, newVal ? 'true' : 'false');
  applyHistoryLock(newVal);
}
// Restore lock state on page load
(function() {
  const key = getHistoryLockKey();
  if (key && localStorage.getItem(key) === 'true') {
    applyHistoryLock(true);
  }
})();

async function saveBackstory() {
  const text = document.getElementById('history-backstory').value;
  try {
    await fetch(`/api/character/${CHAR_ID}/update`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({backstory: text}),
    });
  } catch(e) { console.error(e); }
}

async function saveJournal() {
  const text = document.getElementById('journal-text').value;
  try {
    await fetch(`/api/character/${CHAR_ID}/update`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({journal: text}),
    });
  } catch(e) { console.error(e); }
}

async function savePersonalityField(field, value) {
  try {
    // Build the full personality_data object from all inputs
    const inputs = document.querySelectorAll('.personality-input');
    const data = {};
    inputs.forEach(el => data[el.dataset.field] = el.value);
    // Override with the changed field
    data[field] = value;
    await fetch(`/api/character/${CHAR_ID}/update`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({personality_data: JSON.stringify(data)}),
    });
  } catch(e) { console.error('savePersonalityField error:', e); }
}

async function generateHistory() {
  const btn = document.getElementById('history-gen-btn');
  const loading = document.getElementById('history-loading');
  const textarea = document.getElementById('history-backstory');
  const keywords = document.getElementById('history-keywords').value.trim();

  btn.disabled = true;
  loading.style.display = '';
  try {
    const resp = await fetch(`/api/character/${CHAR_ID}/generate-history`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({keywords}),
    });
    if (!resp.ok) {
      alert('Could not generate history: HTTP ' + resp.status);
      return;
    }
    const data = await resp.json();
    if (data.backstory) {
      textarea.value = data.backstory;
      await saveBackstory();
      if (data.ai_generated === false) {
        alert('The AI could not be reached — this is a generic placeholder, not a generated history.');
      }
    } else {
      alert('Failed to generate history: ' + (data.error || 'Unknown error'));
    }
  } catch(e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    loading.style.display = 'none';
  }
}

// ── Team Items (campaign pool) ──
async function loadTeamItems() {
  if (!CAMPAIGN_ID) return;
  const list = document.getElementById('team-items-list');
  if (!list) return;
  list.innerHTML = '<span style="color:var(--text-muted);font-size:0.8rem">Loading...</span>';
  try {
    const r = await fetch(`/api/campaign/${CAMPAIGN_ID}/team-items`);
    const d = await r.json();
    if (d.error) { list.innerHTML = `<span style="color:var(--text-muted);font-size:0.8rem">${d.error}</span>`; return; }
    const items = d.items || [];
    if (items.length === 0) {
      list.innerHTML = '<span style="color:var(--text-muted);font-size:0.8rem">No items in team pool</span>';
      return;
    }
    list.innerHTML = items.map(item => {
      const gpInfo = item.gp_value > 0 ? `<span style="font-size:0.68rem;color:#ffd700;margin-left:0.2rem">${item.gp_value}gp</span>` : '';
      const qtyBadge = item.qty > 1 ? `<span class="item-qty" style="cursor:default">${item.qty}</span>` : '';
      return `<div class="item-card">
        <button class="item-card-btn item-expand-btn" onclick="event.stopPropagation();toggleItemExpand(this)" title="Expand">▶</button>
        <span class="item-card-name" style="cursor:default">${item.name}</span>${qtyBadge}${gpInfo}
        <span class="wpn-badge item-tag" style="display:none">⚔️</span>
        <span class="arm-badge item-tag" style="display:none">🛡️</span>
        <button class="item-card-btn item-card-award" onclick="claimFromTeam(${item.id}, '${item.name.replace(/'/g, "\\'")}')" title="Claim item" style="color:var(--success)">📥</button>
        <div class="item-detail" data-item-name="${item.name}"></div>
      </div>`;
    }).join('');
    setTimeout(() => tagWeapons(), 50);
  } catch(e) { list.innerHTML = '<span style="color:var(--danger);font-size:0.8rem">Failed to load</span>'; }
}

async function claimFromTeam(itemId, itemName) {
  if (!confirm(`Claim "${itemName}" from the team pool?`)) return;
  try {
    const r = await fetch(`/api/campaign/${CAMPAIGN_ID}/team-items/${itemId}/claim`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({character_id: CHAR_ID}),
      credentials: 'same-origin'
    });
    const d = await r.json();
    if (d.ok) {
      // Instant GP update for currency claims
      if (d.currency && d.gp_added) { CHARACTER_GP += d.gp_added; renderGpDisplay(); }
      // Fallback: client-side parse for older items
      else if (!d.currency) {
        const gpMatch = itemName.match(/(\d[\d,]*)\s*gp/i);
        if (gpMatch) { CHARACTER_GP += parseInt(gpMatch[1].replace(/,/g,'')); renderGpDisplay(); }
      }
      // Refresh team items list
      try { loadTeamItems(); } catch(e) {}
      // Add item to inventory DOM (server already added it to DB)
      if (!d.currency && d.added) {
        addInventoryCard(d.added, d.qty || 1);
        await _flushInventory();
      }
      try { reloadGp(); } catch(e) {}
    } else {
      alert(d.error || 'Failed to claim');
    }
  } catch(e) { alert('Failed to claim item'); }
}

async function shareToTeam(name, qty) {
  if (!CAMPAIGN_ID) return;
  if (!confirm(`Share "${name}" (x${qty}) with the team?`)) return;
  try {
    const r = await fetch(`/api/character/${CHAR_ID}/share-to-team`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, qty})
    });
    const d = await r.json();
    if (d.ok) { renderInventory(); loadTeamItems(); }
    else alert(d.error || 'Failed to share');
  } catch(e) { alert('Failed to share item'); }
}

// ── GP Tracker ──
async function reloadGp() {
  try {
    const r = await fetch(`/api/character/${CHAR_ID}/gp`, {credentials: 'same-origin'});
    const d = await r.json();
    if (d.gp !== undefined) { CHARACTER_GP = d.gp; renderGpDisplay(); }
  } catch(e) { /* ignore — non-critical */ }
}

function renderGpDisplay() {
  const el = document.getElementById('gp-display');
  if (el) el.textContent = CHARACTER_GP;
  const popup = document.getElementById('gp-popup-total');
  if (popup) popup.textContent = CHARACTER_GP;
}

async function adjustGp(delta) {
  CHARACTER_GP = Math.max(0, CHARACTER_GP + delta);
  renderGpDisplay();
  await saveGp();
}

function openGpPopup() {
  const popup = document.getElementById('gp-popup');
  if (!popup) return;
  document.getElementById('gp-popup-total').textContent = CHARACTER_GP;
  document.getElementById('gp-amount').value = '';
  popup.style.display = 'flex';
}

function closeGpPopup() {
  document.getElementById('gp-popup').style.display = 'none';
}

async function adjustGpPopup(mode) {
  const inp = document.getElementById('gp-amount');
  const amt = parseInt(inp.value) || 0;
  if (amt <= 0) return;
  if (mode === 'sub') {
    if (amt > CHARACTER_GP && !confirm(`You only have ${CHARACTER_GP} gp. Subtract all?`)) return;
    CHARACTER_GP = Math.max(0, CHARACTER_GP - amt);
  } else {
    CHARACTER_GP += amt;
  }
  renderGpDisplay();
  inp.value = '';
  await saveGp();
}

async function giftGpToTeam() {
  const inp = document.getElementById('gp-amount');
  const amt = parseInt(inp.value) || 0;
  if (amt <= 0) { alert('Enter an amount to gift'); return; }
  if (amt > CHARACTER_GP) { alert(`You only have ${CHARACTER_GP} gp`); return; }
  if (!confirm(`Gift ${amt} gp to the party pool?`)) return;
  try {
    const r = await fetch(`/api/campaign/${CAMPAIGN_ID}/team-items/add`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name: `${amt} gp`, qty: 1, gp_value: amt})
    });
    const d = await r.json();
    if (d.ok) {
      CHARACTER_GP -= amt;
      renderGpDisplay();
      await saveGp();
      loadTeamItems();
      inp.value = '';
      closeGpPopup();
    } else {
      alert(d.error || 'Failed to gift');
    }
  } catch(e) { alert('Failed to gift gp'); }
}

async function saveGp() {
  try {
    await fetch(`/api/character/${CHAR_ID}/update`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({gp: CHARACTER_GP})
    });
  } catch(e) { /* ignore */ }
}

// Override switchTab to load relationships when history tab opens
const _origSwitchTab = switchTab;
switchTab = function(name) {
  _origSwitchTab(name);
  if (name === 'history') loadRelationships();
  if (name === 'inventory' && CAMPAIGN_ID) setTimeout(loadTeamItems, 100);
};

// Also trigger on initial load if history was saved tab
if (savedTab === 'history') setTimeout(loadRelationships, 300);
if (savedTab === 'inventory' && CAMPAIGN_ID) setTimeout(loadTeamItems, 300);

// ── De-Level Wizard ───────────────────────────────────────────────────
let dlData = null;
let dlChoices = {target_level: null, abilities: null, hp_max: null};
let dlStep = 0;
let dlSteps = [];
let dlClassToLevel = null;

async function openDeLevel() {
  const levelEl = document.querySelector('.badge-accent .editable[data-field="level"]');
  const currentLevel = levelEl ? parseInt(levelEl.textContent) : 1;

  dlStep = 0;
  dlChoices = {target_level: null, abilities: null, hp_max: null};
  dlClassToLevel = null;

  // Detect multiclass from CLASS_LEVELS
  const clKeys = Object.keys(CLASS_LEVELS || {});
  const isMulti = clKeys.length > 1;

  dlData = {current_level: currentLevel, is_multiclass: isMulti};

  dlSteps = [];
  if (isMulti) {
    dlSteps.push({label: 'Class', render: renderDlClassPick});
  }
  dlSteps.push({label: 'Target', render: renderDlTarget});

  document.getElementById('dl-title').textContent = '🔻 Level Down';

  const dots = document.getElementById('dl-steps');
  dots.innerHTML = dlSteps.map((s, i) =>
    `<span class="dl-step-dot" id="dl-dot-${i}" style="display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;border-radius:50%;font-size:0.7rem;font-weight:600;background:var(--bg);border:2px solid var(--border);color:var(--text-muted)">${i+1}</span>`
  ).join(' ');

  document.getElementById('delevel-modal').style.display = 'flex';
  renderDeLevelStep();
}

function closeDeLevel() {
  document.getElementById('delevel-modal').style.display = 'none';
  dlData = null;
}

function deLevelStep(delta) {
  if (delta > 0 && dlStep >= dlSteps.length - 1) return;
  if (delta < 0 && dlStep <= 0) return;
  dlStep += delta;
  renderDeLevelStep();
}

function renderDeLevelStep() {
  const content = document.getElementById('dl-content');
  content.innerHTML = '';

  document.querySelectorAll('.dl-step-dot').forEach((d, i) => {
    d.style.background = i === dlStep ? 'var(--accent)' : (i < dlStep ? 'var(--success)' : 'var(--bg)');
    d.style.borderColor = i === dlStep ? 'var(--accent)' : (i < dlStep ? 'var(--success)' : 'var(--border)');
    d.style.color = i <= dlStep ? '#fff' : 'var(--text-muted)';
  });

  document.getElementById('dl-back').style.display = dlStep > 0 ? '' : 'none';
  document.getElementById('dl-next').style.display = dlStep < dlSteps.length - 1 ? '' : 'none';
  document.getElementById('dl-confirm').style.display = dlStep === dlSteps.length - 1 ? '' : 'none';

  if (dlSteps[dlStep] && dlSteps[dlStep].render) {
    dlSteps[dlStep].render(content);
  }
}

function renderDlClassPick(el) {
  const cl = CLASS_LEVELS || {};
  const lvlDisplay = Object.entries(cl).map(([c, l]) => `${c} ${l}`).join(' / ');

  let html = `<div style="font-size:0.9rem;line-height:1.6">
    <h3 style="margin-bottom:0.5rem">Choose Class to De-Level</h3>
    <p style="color:var(--text-muted);margin-bottom:1rem">Current: <strong>${lvlDisplay}</strong></p>
    <div style="display:flex;flex-direction:column;gap:0.4rem;margin-bottom:0.75rem">`;
  for (const [clsName, clsLvl] of Object.entries(cl)) {
    const checked = dlClassToLevel === clsName;
    html += `<label style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;cursor:pointer">
      <input type="radio" name="dl-class" value="${clsName}" ${checked ? 'checked' : ''} onchange="dlClassToLevel=this.value">
      <strong>${clsName}</strong>
      <span style="font-size:0.75rem;color:var(--text-muted)">Lv ${clsLvl}</span>
    </label>`;
  }
  html += `</div>
    <p style="color:var(--text-muted);font-size:0.8rem">Select which class to remove levels from. The target level you choose next is the character's new total level.</p>
  </div>`;
  el.innerHTML = html;
}

function renderDlTarget(el) {
  const currentLevel = dlData.current_level;
  let html = `<div>
    <p style="margin-bottom:1rem">Select a level to de-level down to:</p>
    <select id="dl-target-select" style="width:100%;padding:0.4rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);margin-bottom:1rem" onchange="dlChoices.target_level=this.value">
      <option value="">— Select target level —</option>`;
  for (let l = 1; l < currentLevel; l++) {
    const sel = dlChoices.target_level == l ? ' selected' : '';
    html += `<option value="${l}"${sel}>Level ${l}</option>`;
  }
  html += `</select>
    <button class="btn btn-primary" onclick="loadDlDataAndAdvance()" style="width:100%">🔍 Load Preview</button>
  </div>`;
  el.innerHTML = html;
}

async function loadDlDataAndAdvance() {
  const target = parseInt(dlChoices.target_level);
  if (!target) { alert('Please select a target level'); return; }

  // If multiclass, require class selection
  if (dlData.is_multiclass && !dlClassToLevel) {
    alert('Please select a class to de-level');
    return;
  }

  const btn = document.querySelector('#dl-content .btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

  try {
    let url = `/api/character/${CHAR_ID}/de-level-info?target_level=${target}`;
    if (dlClassToLevel) url += `&class_name=${encodeURIComponent(dlClassToLevel)}`;
    const resp = await fetch(url);
    dlData = await resp.json();
    if (dlData.error) { alert(dlData.error); if (btn) { btn.disabled = false; btn.textContent = '🔍 Load Preview'; } return; }
  } catch(e) {
    console.error(e);
    alert('Failed to load de-level info');
    if (btn) { btn.disabled = false; btn.textContent = '🔍 Load Preview'; }
    return;
  }

  dlChoices.hp_max = dlData.hp_estimate;
  dlChoices.abilities = dlData.suggested_abilities ? {...dlData.suggested_abilities} : (dlData.current_abilities ? {...dlData.current_abilities} : null);

  dlSteps = [
    {label: 'Target', render: renderDlTarget},
    {label: 'Preview', render: renderDlPreview}
  ];
  if (dlData.lost_asi_levels && dlData.lost_asi_levels.length > 0) {
    dlSteps.push({label: 'Abilities', render: renderDlAbilities});
  }
  dlSteps.push({label: 'Confirm', render: renderDlConfirm});

  document.getElementById('dl-title').textContent = `🔻 Level Down — L${dlData.current_level} → L${dlData.target_level}`;

  const dots = document.getElementById('dl-steps');
  dots.innerHTML = dlSteps.map((s, i) =>
    `<span class="dl-step-dot" id="dl-dot-${i}" style="display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;border-radius:50%;font-size:0.7rem;font-weight:600;background:var(--bg);border:2px solid var(--border);color:var(--text-muted)">${i+1}</span>`
  ).join(' ');

  dlStep = 1;
  renderDeLevelStep();
}

function renderDlPreview(el) {
  const d = dlData;
  let html = `<div style="font-size:0.9rem;line-height:1.6">
    <p><strong>Class:</strong> ${d.class_name} (losing ${d.levels_lost} level${d.levels_lost > 1 ? 's' : ''})</p>
    <p><strong>New Level:</strong> <span style="color:var(--danger)">${d.target_level}</span></p>
    <p><strong>HP Estimate:</strong> ${d.hp_estimate} (${d.hp_per_level_avg} avg/level × ${d.levels_lost} levels lost)</p>`;

  if (d.features_lost && d.features_lost.length) {
    html += `<p><strong>Features Lost:</strong></p><ul>`;
    d.features_lost.forEach(f => html += `<li style="color:var(--danger)">${f}</li>`);
    html += `</ul>`;
  }

  if (d.proficiency_bonus && d.proficiency_bonus.changed) {
    html += `<p><strong>Proficiency Bonus:</strong> +${d.proficiency_bonus.old} → <span style="color:var(--danger)">+${d.proficiency_bonus.new}</span></p>`;
  }

  if (d.subclass_note) {
    html += `<p style="color:var(--warn)"><strong>⚠ Subclass:</strong> ${d.subclass_note}</p>`;
  }

  if (d.lost_asi_levels && d.lost_asi_levels.length) {
    html += `<p style="color:var(--warn)"><strong>⚠ ASI Levels Lost:</strong> ${d.lost_asi_levels.map(l => 'L' + l).join(', ')} — You'll need to revert ability score changes on the next step.</p>`;
  }

  html += `</div>`;
  el.innerHTML = html;
}

function renderDlAbilities(el) {
  const abilities = dlData.current_abilities;
  if (!abilities) { el.innerHTML = '<p>No ability data available.</p>'; return; }

  if (!dlChoices.abilities) dlChoices.abilities = {...abilities};

  let html = `<div>
    <p style="font-weight:600;margin-bottom:0.75rem">Revert Ability Scores</p>
    <p style="font-size:0.85rem;color:var(--text-muted);margin-bottom:1rem">These levels included ASI improvements. Adjust ability scores down to what they were before those levels.</p>
    <button class="btn btn-outline btn-sm" onclick="resetDlAbilities()" style="margin-bottom:1rem">🔄 Reset to Current</button>`;

  for (const [name, value] of Object.entries(abilities)) {
    const currentVal = dlChoices.abilities[name] !== undefined ? dlChoices.abilities[name] : value;
    html += `<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
      <label style="width:100px;font-weight:600">${name}</label>
      <input type="number" min="1" max="30" value="${currentVal}"
        onchange="dlChoices.abilities['${name}']=parseInt(this.value)||0"
        style="width:80px;padding:0.35rem;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);text-align:center">
    </div>`;
  }

  html += `</div>`;
  el.innerHTML = html;
}

function resetDlAbilities() {
  if (dlData && dlData.current_abilities) {
    dlChoices.abilities = {...dlData.current_abilities};
    renderDeLevelStep();
  }
}

function renderDlConfirm(el) {
  const d = dlData;
  let html = `<div style="font-size:0.9rem;line-height:1.6">
    <h3 style="margin-bottom:0.75rem">Confirm De-Level</h3>
    <p><strong>Class:</strong> ${d.class_name}</p>
    <p><strong>Level:</strong> ${d.current_level} → <span style="color:var(--danger)">${d.target_level}</span> (lose ${d.levels_lost} level${d.levels_lost > 1 ? 's' : ''})</p>
    <p><strong>HP:</strong> → ${dlChoices.hp_max}</p>`;

  if (d.features_lost && d.features_lost.length) {
    html += `<p><strong>Features Lost:</strong> ${d.features_lost.join(', ')}</p>`;
  }

  if (d.proficiency_bonus && d.proficiency_bonus.changed) {
    html += `<p><strong>Proficiency:</strong> +${d.proficiency_bonus.old} → +${d.proficiency_bonus.new}</p>`;
  }

  if (dlChoices.abilities && d.lost_asi_levels && d.lost_asi_levels.length) {
    html += `<p><strong>Adjusted Abilities:</strong></p>`;
    for (const [name, value] of Object.entries(dlChoices.abilities)) {
      const orig = d.current_abilities[name];
      if (value !== orig) {
        html += `<p style="margin-left:1rem">${name}: ${orig} → <span style="color:var(--danger)">${value}</span></p>`;
      }
    }
  }

  html += `<p style="color:var(--warn);font-size:0.8rem;margin-top:1rem">⚠ This cannot be undone. HP will be fully restored. All features and spells from lost levels will be removed.</p>`;
  html += `</div>`;
  el.innerHTML = html;
}

async function applyDeLevel() {
  const btn = document.getElementById('dl-confirm');
  btn.disabled = true;
  btn.textContent = 'Applying...';

  try {
    const payload = {...dlChoices};
    if (dlClassToLevel) payload.class_to_level = dlClassToLevel;
    const resp = await fetch(`/api/character/${CHAR_ID}/de-level`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const result = await resp.json();
    if (result.ok) {
      alert(`De-Level complete! ${result.changes.join(', ')}`);
      location.reload();
    } else {
      alert('De-level failed: ' + (result.error || 'Unknown error'));
      btn.disabled = false;
      btn.textContent = '✅ Confirm De-Level';
    }
  } catch(e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = '✅ Confirm De-Level';
  }
}

// ══════ SUMMONS ══════
