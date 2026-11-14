/* dm/traps.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Traps ──
function toggleTrapDetail(btn) {
  const card = btn.closest('.trap-card');
  const detail = card.querySelector('.trap-detail');
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? '▶ Expand' : '▼ Collapse';
}

function filterTraps() {
  const query = (document.getElementById('trapSearch')?.value || '').toLowerCase().trim();
  const typeFilter = document.getElementById('trapTypeFilter')?.value || '';
  const dangerFilter = document.getElementById('trapDangerFilter')?.value || '';
  let count = 0;
  document.querySelectorAll('.trap-card').forEach(card => {
    const name = card.getAttribute('data-name') || '';
    const type = card.getAttribute('data-type') || '';
    const danger = card.getAttribute('data-danger') || '';
    const match = (!query || name.includes(query))
      && (!typeFilter || type === typeFilter)
      && (!dangerFilter || danger === dangerFilter)
      && (!_coreOnlyDM || isCoreSourceDM(card.getAttribute('data-source') || ''))
      && _srcOk(card.getAttribute('data-source') || '', 'dm-traps');
    card.style.display = match ? '' : 'none';
    if (match) count++;
  });
  const countEl = document.getElementById('trapCount');
  if (countEl) countEl.textContent = count + ' trap' + (count !== 1 ? 's' : '');
}

// ── Custom trap create/edit ──
const TRAP_DANGER_PRESETS = {
  setback:  { dc: 10, damage: '1d10' },
  dangerous: { dc: 15, damage: '2d10' },
  deadly:   { dc: 20, damage: '4d10' }
};

function showCreateTrap() {
  document.getElementById('trapModalTitle').textContent = '⚙️ Create Custom Trap';
  document.getElementById('trapEditId').value = '';
  document.getElementById('trapForm').reset();
  document.getElementById('trapDanger').value = 'dangerous';
  onTrapDangerChange();
  openModal('trapModal');
}

function onTrapDangerChange() {
  const danger = document.getElementById('trapDanger').value;
  const preset = TRAP_DANGER_PRESETS[danger];
  if (preset) {
    document.getElementById('trapSaveDc').value = preset.dc;
    if (!document.getElementById('trapDamage').value) {
      document.getElementById('trapDamage').value = preset.damage;
    }
  }
}

async function saveTrap(event) {
  event.preventDefault();
  const id = document.getElementById('trapEditId').value;
  const data = {
    name: document.getElementById('trapName').value.trim(),
    type: document.getElementById('trapType').value,
    danger: document.getElementById('trapDanger').value,
    save_dc: parseInt(document.getElementById('trapSaveDc').value) || null,
    save_ability: document.getElementById('trapSaveAbility').value,
    damage: document.getElementById('trapDamage').value.trim(),
    damage_type: document.getElementById('trapDamageType').value,
    area: document.getElementById('trapArea').value.trim(),
    trigger: document.getElementById('trapTrigger').value.trim(),
    detection_dc: parseInt(document.getElementById('trapDetectDc').value) || null,
    detection_skill: document.getElementById('trapDetectSkill').value,
    detection_detail: document.getElementById('trapDetectDetail').value.trim(),
    disarm_dc: parseInt(document.getElementById('trapDisarmDc').value) || null,
    disarm_method: document.getElementById('trapDisarmMethod').value.trim(),
    disarm_detail: document.getElementById('trapDisarmDetail').value.trim(),
    effect: document.getElementById('trapEffect').value.trim(),
    description: document.getElementById('trapDesc').value.trim()
  };
  if (!data.name) return alert('Name is required.');

  const url = id ? `/api/dm/traps/${id}/update` : '/api/dm/traps/create';
  try {
    const r = await fetch(url, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (!d.ok && d.error) return alert(d.error);
    closeModal('trapModal');
    location.reload();
  } catch(e) { alert('Failed to save trap.'); }
}

async function editTrap(id) {
  try {
    const r = await fetch('/api/dm/traps');
    const d = await r.json();
    const t = (d.traps || []).find(t => t.id === id);
    if (!t) return alert('Trap not found');

    document.getElementById('trapModalTitle').textContent = '✏️ Edit Trap';
    document.getElementById('trapEditId').value = t.id;
    document.getElementById('trapName').value = t.name || '';
    document.getElementById('trapType').value = t.type || 'mechanical';
    document.getElementById('trapDanger').value = t.danger || 'dangerous';
    document.getElementById('trapSaveDc').value = t.save_dc || '';
    document.getElementById('trapSaveAbility').value = t.save_ability || 'Dexterity';
    document.getElementById('trapDamage').value = t.damage || '';
    document.getElementById('trapDamageType').value = t.damage_type || '';
    document.getElementById('trapArea').value = t.area || '';
    document.getElementById('trapTrigger').value = t.trigger || '';
    document.getElementById('trapDetectDc').value = t.detection_dc || '';
    document.getElementById('trapDetectSkill').value = t.detection_skill || 'Perception';
    document.getElementById('trapDetectDetail').value = t.detection_detail || '';
    document.getElementById('trapDisarmDc').value = t.disarm_dc || '';
    document.getElementById('trapDisarmMethod').value = t.disarm_method || '';
    document.getElementById('trapDisarmDetail').value = t.disarm_detail || '';
    document.getElementById('trapEffect').value = t.effect || '';
    document.getElementById('trapDesc').value = t.description || '';
    openModal('trapModal');
  } catch(e) { alert('Failed to load trap.'); }
}

async function deleteTrap(id) {
  if (!confirm('Delete this custom trap?')) return;
  try {
    const r = await fetch(`/api/dm/traps/${id}/delete`, { method: 'POST' });
    const d = await r.json();
    if (!d.ok) return alert(d.error || 'Failed');
    location.reload();
  } catch(e) { alert('Failed to delete trap.'); }
}

// ── AI Trap Generation ──
async function generateAiTrap() {
  const btn = document.getElementById('btnAiTrap');
  const status = document.getElementById('aiTrapStatus');
  btn.disabled = true;
  status.style.display = 'block';
  status.textContent = '🤖 Generating trap...';

  try {
    const data = {
      danger: document.getElementById('trapDanger').value,
      type: document.getElementById('trapType').value,
      theme: (document.getElementById('trapName').value || document.getElementById('trapDesc').value || '').trim(),
      location: document.getElementById('trapArea').value.trim(),
    };
    // Use party level info if available from combat campaign
    if (typeof _campaignPlayers !== 'undefined' && _campaignPlayers.length > 0) {
      const avgLevel = Math.round(_campaignPlayers.reduce((s, p) => s + (p.level || 1), 0) / _campaignPlayers.length);
      data.party_level = String(avgLevel);
    }

    const r = await fetch('/api/dm/ai/build-trap', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const t = await r.json();

    // Populate form fields with AI-generated content
    if (t.name) document.getElementById('trapName').value = t.name;
    if (t.trigger) document.getElementById('trapTrigger').value = t.trigger;
    if (t.detection_dc) document.getElementById('trapDetectDc').value = t.detection_dc;
    if (t.detection_skill) document.getElementById('trapDetectSkill').value = t.detection_skill;
    if (t.detection_detail) document.getElementById('trapDetectDetail').value = t.detection_detail;
    if (t.disarm_dc) document.getElementById('trapDisarmDc').value = t.disarm_dc;
    if (t.disarm_method) document.getElementById('trapDisarmMethod').value = t.disarm_method;
    if (t.disarm_detail) document.getElementById('trapDisarmDetail').value = t.disarm_detail;
    if (t.effect) document.getElementById('trapEffect').value = t.effect;
    if (t.save_dc) document.getElementById('trapSaveDc').value = t.save_dc;
    if (t.save_ability) document.getElementById('trapSaveAbility').value = t.save_ability;
    if (t.damage) document.getElementById('trapDamage').value = t.damage;
    if (t.damage_type) document.getElementById('trapDamageType').value = t.damage_type;
    if (t.area) document.getElementById('trapArea').value = t.area;
    if (t.description) document.getElementById('trapDesc').value = t.description;

    status.textContent = '✅ Trap generated! Review and Save.';
    status.style.color = 'var(--success)';
  } catch(e) {
    status.textContent = '❌ Generation failed. Try again or fill manually.';
    status.style.color = 'var(--danger)';
  }
  btn.disabled = false;
  setTimeout(() => { status.style.display = 'none'; status.style.color = 'var(--accent)'; }, 4000);
}

