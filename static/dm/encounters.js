/* dm/encounters.js — split out of static/dm_tools.js (Stage 5, classic script, shares window scope). */
// ── Encounters ──
function showCreateEncounter() {
  const name = prompt('Encounter name:');
  if (!name) return;
  api('/api/dm/encounter/create', { body: {name, description: '', location: '', environment: '', difficulty: 'medium'} })
    .then(d => { if (d.ok) location.reload(); })
    .catch(e => alert('Could not create the encounter: ' + e.message));
}

// ── AI Encounter Builder ──
async function showAiEncounterBuilder() {
  openModal('aiEncounterModal');
  document.getElementById('aiEncounterResult').style.display = 'none';
  // Populate campaign dropdown
  try {
    const r = await fetch('/api/dm/campaigns');
    const d = await r.json();
    const sel = document.getElementById('aiCampaignSelect');
    sel.innerHTML = '<option value="">— Manual party level/size —</option>';
    (d.campaigns || []).forEach(c => {
      sel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
    });
    document.getElementById('aiPartyPreview').style.display = 'none';
    const lvl = document.getElementById('aiPartyLevel');
    const sz = document.getElementById('aiPartySize');
    lvl.disabled = false;
    sz.disabled = false;
    lvl.value = 5;
    sz.value = 4;
  } catch(e) {}
}

async function onAiCampaignChange() {
  const sel = document.getElementById('aiCampaignSelect');
  const cid = sel.value;
  const preview = document.getElementById('aiPartyPreview');
  const lvl = document.getElementById('aiPartyLevel');
  const sz = document.getElementById('aiPartySize');
  if (!cid) {
    preview.style.display = 'none';
    return;
  }
  // Fetch party profile from campaign
  try {
    const r = await fetch(`/api/dm/ai/party-profile?campaign_id=${cid}`);
    const d = await r.json();
    if (d.profile) {
      const s = d.profile.summary;
      lvl.value = Math.max(1, Math.round(s.avg_level));
      sz.value = s.size;
      preview.style.display = 'block';
      preview.style.color = 'var(--accent)';
      preview.textContent = `${plural(s.size, 'character')}, avg L${s.avg_level} — encounter tailored to their stats`;
    } else if (d.campaign) {
      // Fallback to campaign's stored values when no characters linked
      lvl.value = d.campaign.party_level || 1;
      sz.value = d.campaign.party_size || 4;
      preview.style.display = 'block';
      preview.style.color = 'var(--accent)';
      preview.textContent = `L${lvl.value} · ${plural(sz.value, 'player')} (campaign default)`;
    } else {
      preview.style.display = 'block';
      preview.style.color = 'var(--text-muted)';
      preview.textContent = 'No characters in this campaign. Enter values manually.';
    }
  } catch(e) {
    preview.style.display = 'block';
    preview.style.color = 'var(--danger)';
    preview.textContent = 'Failed to load party. Enter values manually.';
  }
}

async function generateAiEncounter(event) {
  event.preventDefault();
  const form = document.getElementById('aiEncounterForm');
  const data = Object.fromEntries(new FormData(form));
  data.party_level = parseInt(data.party_level) || 5;
  data.party_size = parseInt(data.party_size) || 4;

  const resultDiv = document.getElementById('aiEncounterResult');
  const submit = document.getElementById('aiEncounterSubmit');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<div style="text-align:center;padding:2rem">🧙 Crafting encounter...</div>';
  // The form (and this button) stayed live while the request was in flight, so a second click
  // fired a second POST — and the server makes a real AI call per request.
  if (submit) { submit.disabled = true; submit.textContent = '⏳ Crafting...'; }

  try {
    const r = await fetch('/api/dm/ai/build-encounter', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    if (!r.ok) {
      const d = await r.json().catch(function () { return {}; });
      resultDiv.innerHTML = '<p style="color:var(--danger)">Failed to generate encounter: '
        + dmEsc(d.error || d.detail || ('HTTP ' + r.status)) + '</p>';
      return;
    }
    const enc = await r.json();

    const difficultyColors = {Easy: 'var(--success)', Medium: 'var(--warn)', Hard: 'var(--danger)', Deadly: 'var(--danger)'};
    const diffColor = difficultyColors[enc.difficulty] || 'var(--text-muted)';

    let html = `<div style="border-top:1px solid var(--border);padding-top:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
        <h3 style="margin:0">${dmEsc(enc.name)}</h3>
        <div style="display:flex;gap:0.3rem;align-items:center">
          <span class="badge badge-accent">${enc.difficulty}</span>
          <span style="font-size:0.8rem;color:var(--text-muted)">L${enc.party.level} · ${plural(enc.party.size, 'player')}</span>
        </div>
      </div>`;

    // AI text goes through dmEsc: it is model output, and innerHTML ran it (a reply containing
    // markup executed in the DM's session; a quote or newline in an onclick string broke Save).
    if (enc.description) html += `<p style="color:var(--text-muted);font-size:0.85rem;margin:0.5rem 0">${dmEsc(enc.description)}</p>`;

    // XP breakdown
    html += `<div style="display:flex;gap:1rem;margin:0.5rem 0;font-size:0.85rem;flex-wrap:wrap">
      <span><strong>Raw XP:</strong> ${enc.xp.raw_total}</span>
      <span><strong>Adjusted XP:</strong> ${enc.xp.adjusted}</span>
      <span><strong>Budget:</strong> ${enc.xp.budget}</span>
      <span><strong>Budget used:</strong> <span style="color:${diffColor}">${enc.xp.budget_pct}%</span></span>
    </div>`;

    // Composition
    html += `<div style="margin:0.5rem 0"><strong>Composition:</strong></div>`;
    enc.composition.forEach(m => {
      const srcBadge = m.source
        ? ` <span class="src-badge" data-src="${dmEsc(m.source)}" title="${m.source}" style="cursor:pointer;background:var(--accent2);border:1px solid var(--border);font-size:0.7rem;padding:0.1rem 0.4rem;border-radius:4px;white-space:nowrap;flex-shrink:0;color:var(--text);display:inline-flex;align-items:center;gap:0.15rem">📚</span>`
        : '';
      html += `<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:0.4rem 0.5rem;background:var(--bg);border-radius:4px;margin-bottom:0.3rem;flex-wrap:wrap;gap:0.3rem">
        <span style="flex:1 1 auto;min-width:0;overflow-wrap:break-word;word-break:break-word;display:flex;align-items:center;gap:0.25rem;flex-wrap:wrap"><strong>${m.count}× ${m.name}</strong>${srcBadge}<span style="color:var(--text-muted);font-size:0.8rem;white-space:nowrap">${m.size} ${m.type} · CR ${m.cr}</span></span>
        <span style="font-size:0.8rem;color:var(--text-muted);flex-shrink:0">AC ${m.ac} HP ${m.hp} · ${m.xp * m.count} XP</span>
      </div>`;
    });

    if (enc.tactics) html += `<p style="font-size:0.85rem;color:var(--text-muted);margin-top:0.5rem"><strong>Tactics:</strong> ${enc.tactics}</p>`;
    if (enc.dynamic) html += `<p style="font-size:0.85rem;color:var(--accent);margin-top:0.25rem;border-left:3px solid var(--accent);padding-left:0.5rem">⚡ <strong>Dynamic:</strong> ${enc.dynamic}</p>`;

    // Store composition + tactics + dynamic for saveAiEncounter, and the identifying fields too:
    // the Save button used to carry them as string-interpolated onclick arguments, so a name with
    // a double quote, a backslash or a newline broke the button and the encounter could not be
    // saved. A fixed identifier cannot be broken by model output.
    window._aiComposition = enc.composition;
    window._aiTactics = enc.tactics || '';
    window._aiDynamic = enc.dynamic || '';
    window._aiEncounterSave = {
      name: enc.name || 'Encounter',
      description: enc.description || '',
      environment: data.environment || '',
      difficulty: (enc.difficulty || 'medium').toLowerCase()
    };

    // Save button
    html += `<div style="margin-top:1rem;display:flex;gap:0.5rem">
      <button class="btn btn-primary" onclick="saveAiEncounterFromResult()">💾 Save as Encounter</button>
      <button class="btn btn-outline" onclick="closeModal('aiEncounterModal')">Close</button>
    </div></div>`;

    resultDiv.innerHTML = html;
  } catch(e) {
    resultDiv.innerHTML = '<p style="color:var(--danger)">Failed to generate encounter. Try again.</p>';
  } finally {
    if (submit) { submit.disabled = false; submit.textContent = '🤖 Generate Encounter'; }
  }
  return false;
}

function saveAiEncounterFromResult() {
  const e = window._aiEncounterSave || {};
  return saveAiEncounter(e.name, e.description, e.environment, e.difficulty);
}

async function saveAiEncounter(name, description, environment, difficulty) {
  const tactics = window._aiTactics || '';
  const dynamic = window._aiDynamic || '';
  // Append dynamic element to notes
  const notes = dynamic ? (tactics + '\n⚡ Dynamic: ' + dynamic) : tactics;
  let d;
  try {
    d = await api('/api/dm/encounter/create', {
      body: {name, description, environment, difficulty, location: '', notes}
    });
  } catch (e) {
    // Silent no-op before this: the DM clicked Save, the create failed, and nothing happened
    // with no message at all.
    alert(e.message);
    return;
  }
  if (!d.ok) { alert(d.error || 'Could not create the encounter'); return; }
  const encId = d.id;
  // Add creatures from AI composition to the encounter
  let added = 0, failed = 0, total = 0;
  if (window._aiComposition && window._aiComposition.length) {
    for (const m of window._aiComposition) {
      total += m.count || 1;
      for (let i = 0; i < (m.count || 1); i++) {
        try {
          const ar = await api(`/api/dm/encounter/${encId}/add-creature`, {
            body: {
              name: m.name, race: m.type || '', class_name: '', level: 1,
              hp: m.hp || 10, hp_max: m.hp || 10, ac: m.ac || 10,
              is_enemy: 1, role: `${m.size || ''} ${m.type || ''}`.trim(),
              xp_reward: m.xp || 0, _monster_index: m.index || '',
            }
          });
          const ad = ar;
          if (ad.ok) added++;
          else failed++;
        } catch(e) { failed++; }
      }
    }
  }
  const note = failed > 0 ? ` (${failed} of ${total} creatures failed to save)` : '';
  window._aiComposition = null;
  window._aiTactics = null;
  window._aiDynamic = null;
  closeModal('aiEncounterModal');
  if (failed > 0) { alert(`Saved ${added} creatures to encounter${note}.`); }
  location.reload();
}

