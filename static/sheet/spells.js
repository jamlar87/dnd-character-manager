/* sheet/spells.js — split out of static/sheet.js (Stage 5, classic script, shares window scope). */
// ── Spell Slots ──
async function toggleSpellSlot(lvl, n, max) {
  const boxes = document.querySelectorAll(`#slots-lvl-${lvl} .slot-box`);
  const used = boxes[n-1].classList.contains('used') ? n-1 : n;
  boxes.forEach((b, i) => b.classList.toggle('used', i < used));
  await sheetApi('/update', { spell_slots_used: JSON.stringify({ [lvl]: used }) });
}

async function toggleSpellSlotById(spellId, n, max) {
  const boxes = document.querySelectorAll(`#sp-slots-${spellId} .slot-box`);
  const used = boxes[n-1].classList.contains('used') ? n-1 : n;
  boxes.forEach((b, i) => b.classList.toggle('used', i < used));
  await sheetApi('/update', { spells: [{ id: spellId, slots_used: used }] });
}

// ── Add Spell (searchable dropdown) ──
let _spellCache = [];
let _coreOnly = localStorage.getItem('coreOnly') === 'true';
const _CORE_BOOKS = ['PHB','PHB 2014','DMG','DMG 2014','MM','XGE','TCE','TCE 2020','VGM','MTF','SCAG','ERLW','FTD','MPMM','BPGG'];
const _CORE_NAMES = ["player's handbook","dungeon master's guide","monster manual","xanathar's guide","tasha's cauldron","volo's guide","mordenkainen's tome","sword coast adventurer's guide","eberron: rising","fizban's treasury","mordenkainen presents","bigby presents","glory of the giants","elemental evil player's companion"];
function isCoreSource(book) {
  if (!book) return true;
  const lower = book.toLowerCase();
  const abbr = lower.split(' p.')[0].trim();
  if (_CORE_BOOKS.some(b => abbr === b.toLowerCase())) return true;
  if (_CORE_NAMES.some(n => lower.includes(n))) return true;
  return false;
}
// Extended check for item source strings (full book names, no abbreviation pattern)
function isCoreItemSource(src) {
  if (!src) return true;
  const lower = src.toLowerCase();
  for (const b of _CORE_BOOKS) if (lower.includes(b.toLowerCase())) return true;
  for (const n of _CORE_NAMES) if (lower.includes(n)) return true;
  return false;
}

function toggleCoreOnly() {
  _coreOnly = !_coreOnly;
  localStorage.setItem('coreOnly', _coreOnly);
  document.getElementById('core-toggle-box').textContent = _coreOnly ? '☑' : '☐';
  document.getElementById('core-toggle-label').style.color = _coreOnly ? 'var(--accent)' : 'var(--text-muted)';
  filterSpellResults();
}

// Sync spell modal toggle when inventory toggle clicked
function syncCoreToSpellToggle() {
  const box = document.getElementById('core-toggle-box');
  const label = document.getElementById('core-toggle-label');
  if (box) box.textContent = _coreOnly ? '☑' : '☐';
  if (label) label.style.color = _coreOnly ? 'var(--accent)' : 'var(--text-muted)';
  filterSpellResults();
}

async function showAddSpell() {
  document.getElementById('add-spell-modal').style.display = 'flex';
  document.getElementById('spell-search').value = '';
  document.getElementById('spell-name').value = '';
  document.getElementById('spell-level').value = '0';
  document.getElementById('spell-detail').style.display = 'none';
  document.getElementById('spell-results').innerHTML = '<div style="padding:0.5rem;color:var(--text-muted);font-size:0.8rem">Loading...</div>';
  document.getElementById('spell-source-info').textContent = 'Loading available spells...';
  document.getElementById('spell-search').focus();

  try {
    const r = await fetch('/api/character/' + CHAR_ID + '/available-spells');
    _spellCache = await r.json();
    const counts = {};
    _spellCache.forEach(s => { counts[s.source] = (counts[s.source]||0) + 1; });
    const parts = Object.entries(counts).map(([k,v]) => `${v} ${k}`);
    document.getElementById('spell-source-info').textContent = parts.join(' · ') || 'No spells available';
    filterSpellResults();
  } catch(e) {
    document.getElementById('spell-results').innerHTML = '<div style="padding:0.5rem;color:var(--error)">Failed to load spells</div>';
  }
}

function closeAddSpell() {
  document.getElementById('add-spell-modal').style.display = 'none';
  _spellCache = [];
}

function filterSpellResults() {
  const q = document.getElementById('spell-search').value.toLowerCase().trim();
  const div = document.getElementById('spell-results');
  let spells = _spellCache;
  if (_coreOnly) {
    spells = spells.filter(s => isCoreSource(s.book));
  }
  if (window.SourceFilter) {
    const src = SourceFilter.slugs('sheet-spells');
    if (src.length) spells = spells.filter(s => SourceFilter.matches(s.book || s.source || '', src));
  }
  if (q) {
    spells = spells.filter(s =>
      s.name.toLowerCase().includes(q) ||
      s.school.toLowerCase().includes(q) ||
      s.source.toLowerCase().includes(q)
    );
  }
  if (!spells.length) {
    div.innerHTML = '<div style="padding:0.5rem;color:var(--text-muted);font-size:0.8rem">No spells match</div>';
    return;
  }
  // Show all (scrollable)
  const shown = spells;
  div.innerHTML = shown.map((s, i) => {
    const lvlBadge = s.level === 0
      ? '<span style="background:var(--accent2);color:var(--accent);font-size:0.65rem;padding:0.1rem 0.35rem;border-radius:3px">Cantrip</span>'
      : `<span style="background:var(--accent);color:#fff;font-size:0.65rem;padding:0.1rem 0.35rem;border-radius:3px">Lv${s.level}</span>`;
    const schoolBadge = s.school ? `<span style="font-size:0.65rem;color:var(--text-muted)">${s.school}</span>` : '';
    const srcBadge = s.book ? `${_srcBadge(s.book)}` : '';
    const originTag = s.source ? `<span style="font-size:0.68rem;color:var(--accent);opacity:0.6">${s.source}</span>` : '';
    const concIcon = s.concentration ? '🔷' : '';
    const ritIcon = s.ritual ? '📖' : '';
    return `<div class="spell-option" data-idx="${i}"
      style="padding:0.4rem 0.6rem;cursor:pointer;border-bottom:1px solid var(--border);font-size:0.82rem;display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap"
      onmouseover="this.style.background='var(--accent2)'" onmouseout="this.style.background=''"
      onclick="selectSpell(${i})">
      <strong style="flex:1;min-width:0">${s.name}</strong>
      ${concIcon}${ritIcon} ${lvlBadge} ${schoolBadge} ${srcBadge} ${originTag}
    </div>`;
  }).join('');
  if (spells.length > 30) {
    div.innerHTML += `<div style="padding:0.3rem;font-size:0.7rem;color:var(--text-muted);text-align:center">Showing ${shown.length} of ${spells.length} \u2014 type to narrow</div>`;
  }
}

function handleSpellKey(e) {
  if (e.key === 'Escape') { closeAddSpell(); return; }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    const first = document.querySelector('.spell-option');
    if (first) { first.focus(); first.style.background = 'var(--accent2)'; }
  }
}

function selectSpell(idx) {
  const s = _spellCache[idx];
  if (!s) return;
  document.getElementById('spell-name').value = s.name;
  document.getElementById('spell-level').value = s.level;
  document.getElementById('spell-search').value = s.name;
  document.getElementById('spell-results').innerHTML = '';

  // Show/hide fields based on cantrip vs leveled spell
  const isCantrip = s.level === 0;
  document.getElementById('spell-prepared-label').textContent = isCantrip ? 'Known (cantrip)' : 'Prepared';
  document.getElementById('spell-prepared').checked = isCantrip ? true : true;
  document.getElementById('spell-slots-group').style.display = isCantrip ? 'none' : '';

  // Show spell detail
  const detail = document.getElementById('spell-detail');
  detail.style.display = 'block';
  const parts = [];
  if (s.casting_time) parts.push(`<strong>Cast:</strong> ${s.casting_time}`);
  if (s.range) parts.push(`<strong>Range:</strong> ${s.range}`);
  if (s.duration) parts.push(`<strong>Duration:</strong> ${s.duration}${s.concentration ? ' (concentration)' : ''}`);
  if (s.ritual) parts.push('<strong>Ritual</strong>');
  detail.innerHTML = parts.join(' · ') + '<br><br>' + (s.description || 'No description available');
}

async function addSpell() {
  const name = document.getElementById('spell-name').value.trim();
  if (!name) return;
  const data = {
    name, level: parseInt(document.getElementById('spell-level').value),
    prepared: document.getElementById('spell-prepared').checked ? 1 : 0,
    slots_max: parseInt(document.getElementById('spell-slots').value)
  };
  const r = await fetch('/api/character/' + CHAR_ID + '/add-spell', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
  });
  if (r.ok) location.reload();
}

async function aiSelectSpells() {
  const btn = document.querySelector('#tab-spells button[onclick*=\"aiSelectSpells\"]') || event.target;
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '🤖 Thinking...';
  try {
    const r = await fetch('/api/character/' + CHAR_ID + '/ai-spells', { method: 'POST' });
    if (!r.ok) {
      // Without this, a 404 (not the owner — e.g. a DM preview) has no `added`, so the else
      // branch renamed the button "Already full": the app told the user the opposite of what
      // happened. FastAPI errors carry `detail`, not `error`.
      const d = await r.json().catch(function () { return {}; });
      btn.textContent = '⚠️ ' + (d.error || d.detail || ('HTTP ' + r.status));
      setTimeout(function () { btn.textContent = orig; btn.disabled = false; }, 2500);
      return;
    }
    const data = await r.json();
    if (data.added > 0) {
      location.reload();
    } else {
      btn.textContent = data.message || 'Already full';
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
    }
  } catch(e) {
    btn.textContent = 'Error';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }
}

// ── Toggle Prepared Spell ──
async function togglePrepared(spellId, checkbox) {
  // checkbox can be an <input type=checkbox> or a <button> with data-checked
  const wasChecked = checkbox.tagName === 'BUTTON' ? checkbox.dataset.checked === 'true' : checkbox.checked;
  const prepared = !wasChecked;  // toggle
  const r = await fetch('/api/character/' + CHAR_ID + '/toggle-prepared', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ id: spellId, prepared: prepared })
  });
  if (!r.ok) return;
  
  // Update the button state if it's a button
  if (checkbox.tagName === 'BUTTON') {
    checkbox.dataset.checked = prepared ? 'true' : 'false';
    checkbox.textContent = prepared ? '✕' : '+';
    checkbox.title = prepared ? 'Unprepare' : 'Prepare';
    checkbox.style.color = prepared ? 'var(--danger)' : 'var(--success)';
  } else {
    checkbox.checked = prepared;
  }
  
  // Sync the Spellbook row button (if toggle came from chip)
  const row = document.getElementById('spell-row-' + spellId);
  if (row) {
    const spellbookBtn = row.querySelector('.prep-toggle-btn');
    if (spellbookBtn && spellbookBtn !== checkbox) {
      spellbookBtn.dataset.checked = prepared ? 'true' : 'false';
      spellbookBtn.textContent = prepared ? '✕' : '+';
      spellbookBtn.title = prepared ? 'Unprepare' : 'Prepare';
      spellbookBtn.style.color = prepared ? 'var(--danger)' : 'var(--success)';
    }
  }
  // Sync the chip button (if toggle came from Spellbook)
  if (checkbox.tagName === 'BUTTON' && checkbox.closest('.prepared-spell-chip')) {
    // Click came from chip — need to update Spellbook button too (handled above)
  } else {
    // Click came from Spellbook button — need to update chip button
    const chip = document.getElementById('prep-spell-' + spellId);
    if (chip) {
      const chipBtn = chip.querySelector('button');
      if (chipBtn) {
        chipBtn.dataset.checked = prepared ? 'true' : 'false';
        chipBtn.textContent = prepared ? '✕' : '+';
        chipBtn.title = prepared ? 'Unprepare' : 'Prepare';
        chipBtn.style.color = prepared ? 'var(--danger)' : 'var(--success)';
      }
    }
  }
  
  // Update prepared spell chip in the prepared spells card
  const prepList = document.getElementById('prepared-spells-list');
  if (prepList) {
    if (prepared) {
      // Use existing row var from sync section above, or find it
      const spellRow = document.getElementById('spell-row-' + spellId);
      const name = spellRow ? spellRow.querySelector('strong').textContent : 'Spell';
      const lvlBadge = spellRow ? spellRow.querySelector('.badge-muted') : null;
      const lvl = lvlBadge ? lvlBadge.textContent : '';
      const schoolBadge = spellRow ? spellRow.querySelector('[style*="var(--border)"]') : null;
      const concBadge = spellRow ? spellRow.querySelector('[style*="var(--warn)"]') : null;
      
      // Remove "no spells" message if present
      const emptyMsg = prepList.querySelector('p');
      if (emptyMsg) emptyMsg.remove();
      
      const chip = document.createElement('div');
      chip.className = 'prepared-spell-chip';
      chip.id = 'prep-spell-' + spellId;
      chip.style.cssText = 'display:flex;align-items:center;flex-wrap:wrap;gap:0.3rem;padding:0.25rem 0.5rem;background:var(--bg);border:1px solid var(--accent2);border-radius:4px;font-size:0.8rem;cursor:pointer';
      chip.onclick = function(e) {
        e.stopPropagation();
        const targetRow = document.getElementById('spell-row-' + spellId);
        if (targetRow) {
          targetRow.scrollIntoView({behavior:'smooth',block:'center'});
          targetRow.querySelector('.spell-header').classList.add('highlight-pulse');
        }
      };
      chip.title = 'Click to find in Spellbook';
      chip.innerHTML = '<strong>' + name + '</strong>' +
        (lvl ? ' <span class=\"badge badge-muted\" style=\"font-size:0.72rem\">' + lvl + '</span>' : '') +
        (schoolBadge ? ' ' + schoolBadge.outerHTML : '') +
        (concBadge ? ' ' + concBadge.outerHTML : '') +
        '<button class=\"btn btn-outline btn-sm\" style=\"font-size:0.72rem;padding:0.1rem 0.3rem;line-height:1;color:var(--danger)\" ' +
        'onclick=\"event.stopPropagation();togglePrepared(' + spellId + ', this)\" data-checked=\"true\" title=\"Unprepare\">✕</button>';
      prepList.appendChild(chip);
    } else {
      // Remove the chip
      const chip = document.getElementById('prep-spell-' + spellId);
      if (chip) chip.remove();
      // Add "no spells" message if list is empty
      if (prepList.children.length === 0) {
        prepList.innerHTML = '<p style=\"color:var(--text-muted);font-size:0.85rem;margin:0\">No spells prepared. Prepare spells from your Spellbook below.</p>';
      }
    }
  }
  
  // Update prepared count
  const countEl = document.getElementById('prepared-count');
  if (countEl) {
    if (prepared) {
      countEl.textContent = parseInt(countEl.textContent) + 1;
    } else {
      countEl.textContent = Math.max(0, parseInt(countEl.textContent) - 1);
    }
    const current = parseInt(countEl.textContent);
    countEl.style.color = current > PREPARED_MAX ? 'var(--danger)' : '';
  }
}

// ── Unlearn Cantrip ──
async function unlearnSpell(spellId, spellName) {
  if (!confirm(`Unlearn "${spellName}"? This cannot be undone.`)) return;
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '⌛';
  const r = await fetch('/api/character/' + CHAR_ID + '/unlearn-spell', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ id: spellId })
  });
  if (!r.ok) { btn.disabled = false; btn.textContent = '🗑'; return; }
  // Remove the spell row
  const row = document.getElementById('spell-row-' + spellId);
  if (row) row.remove();
  // Update cantrip count
  const countEl = document.getElementById('cantrips-known-count');
  if (countEl) countEl.textContent = Math.max(0, parseInt(countEl.textContent) - 1);
}

// ── Notes ──
let notesTimer;
function saveNotes() {
  clearTimeout(notesTimer);
  notesTimer = setTimeout(async () => {
    await sheetApi('/update', { notes: document.getElementById('notes').value });
  }, 500);
}

// ── Portrait Modal ──
function openPortraitModal() {
  document.getElementById('portrait-modal').style.display = 'flex';
  // Reset upload section
  document.getElementById('upload-section').style.display = 'none';
  document.getElementById('upload-preview').style.display = 'none';
  document.getElementById('upload-file').value = '';
  document.getElementById('upload-status').textContent = '';
}

function toggleUpload() {
  const sec = document.getElementById('upload-section');
  sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
}

function previewUpload() {
  const file = document.getElementById('upload-file').files[0];
  const preview = document.getElementById('upload-preview');
  if (!file) { preview.style.display = 'none'; return; }
  // Same downscale as the save path, so the preview matches what will be stored.
  downscaleImageFile(file, 1024).then(url => {
    if (!url) return;
    preview.src = url;
    preview.style.display = 'block';
  }).catch(() => { preview.style.display = 'none'; });
}

async function saveUploadedPortrait() {
  const file = document.getElementById('upload-file').files[0];
  const status = document.getElementById('upload-status');
  if (!file) { status.textContent = 'Select a file first'; return; }

  status.textContent = 'Uploading...';
  // Downscale before upload (shared with the NPC editor): a phone photo would
  // otherwise be stored at full size — the reason the old portraits are 3 MB.
  try {
    const dataUrl = await downscaleImageFile(file, 1024);
    const r = await fetch('/api/character/portrait', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ character_id: SHEETCFG.charId, image_data: dataUrl })
    });
    if (r.ok) {
      status.textContent = 'Saved!';
      // Route URL (fresh upload, so bust the tile cache) — never the data URL,
      // which is what made the sheet embed megabytes.
      const fresh = '/api/character/' + SHEETCFG.charId + '/portrait-image?size=320&t=' + Date.now();
      document.getElementById('modal-portrait-img').src = fresh;
      document.getElementById('modal-portrait-img').style.display = 'block';
      const ph = document.getElementById('modal-portrait-placeholder');
      if (ph) ph.style.display = 'none';
      const sb = document.getElementById('sidebar-portrait');
      if (sb) sb.src = fresh;
    } else {
      status.textContent = 'Error: ' + r.status;
    }
  } catch(err) {
    status.textContent = 'Failed: ' + err.message;
  }
}

async function fetchPortraitPrompt() {
  const custom = document.getElementById('modal-portrait-custom').value.trim();
  const display = document.getElementById('prompt-display');
  const textarea = document.getElementById('prompt-text');
  display.style.display = 'block';
  textarea.value = 'Generating prompt...';

  try {
    const r = await fetch('/api/ai/portrait', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        // character_id first: the DM rebuilds the prompt from its own sheet row,
        // and that is the only way this preview shows the words it will paint.
        character_id: SHEETCFG.charId,
        race: SHEETCFG.charRace, subrace: SHEETCFG.charSubrace,
        class_name: SHEETCFG.charClassName, subclass: SHEETCFG.charSubclass,
        background: SHEETCFG.charBackground, alignment: SHEETCFG.charAlignment,
        custom_prompt: custom,
        // Prompt only: this button previews the prompt, and it used to run a full image
        // generation (and stand the reference-art backfill down) to show text the server
        // already had — one of the ~50/day keyless quota spent on a preview.
        prompt_only: true
      })
    });
    if (!r.ok) { textarea.value = '⚠️ HTTP ' + r.status; return; }
    const data = await r.json();
    textarea.value = data.prompt || '(no prompt returned)';
  } catch(e) {
    textarea.value = 'Error: ' + (e.message || e);
  }
}

function copyPrompt() {
  const ta = document.getElementById('prompt-text');
  ta.select();
  document.execCommand('copy');
  const span = document.getElementById('prompt-copied');
  span.style.display = 'inline';
  setTimeout(() => { span.style.display = 'none'; }, 2000);
}

/* One generation, polled.

   The DM paints in the background and answers "still painting" while a cold
   render finishes (measured ~45 s on this box). That answer used to drop the
   player on a Retry button for a face that was seconds away — and because the
   sheet paints the modal from the response, the portrait looked generated but
   was never written to the DB, so the sheet (and the DM's party card) showed
   the old face on the next load. Ask again instead: the render is cached by
   then and the second call stores it. */
async function paintSheetPortrait(attempts, gapMs) {
  const loading = document.getElementById('modal-portrait-loading');
  let data = {};
  for (let i = 0; i < attempts; i++) {
    if (i > 0 && loading) {
      loading.style.display = 'block';
      loading.innerHTML = '<div style="font-size:0.75rem;color:var(--text-muted);text-align:center">'
        + 'the DM is still painting… attempt ' + (i + 1) + '/' + attempts + '</div>';
    }
    const r = await fetch('/api/ai/portrait', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        race: SHEETCFG.charRace, subrace: SHEETCFG.charSubrace,
        class_name: SHEETCFG.charClassName, subclass: SHEETCFG.charSubclass,
        background: SHEETCFG.charBackground, alignment: SHEETCFG.charAlignment,
        custom_prompt: document.getElementById('modal-portrait-custom').value.trim(),
        character_id: SHEETCFG.charId
      })
    });
    data = await r.json();
    if (data.image_url) return data;
    if (!/still painting/i.test(String(data.error || ''))) return data;
    if (i < attempts - 1) await new Promise(res => setTimeout(res, gapMs));
  }
  return data;
}

async function regenerateSheetPortrait() {
  const img = document.getElementById('modal-portrait-img');
  const placeholder = document.getElementById('modal-portrait-placeholder');
  const loading = document.getElementById('modal-portrait-loading');

  if (placeholder) placeholder.style.display = 'none';
  img.style.display = 'none';
  loading.style.display = 'block';

  try {
    const data = await paintSheetPortrait(4, 10000);
    const dataUrl = data.image_url || '';

    if (dataUrl) {
      img.src = dataUrl;
      loading.style.display = 'none';
      img.style.display = 'block';
      // Update sidebar portrait
      const sb = document.getElementById('sidebar-portrait');
      if (sb) sb.src = dataUrl;
    } else {
      // The server returns the real reason in `error`, and this branch used to invent a quota
      // story instead (the exact kind of copy that hid a spent provider cap) while telling the
      // user to wait for an image that was never coming. Retry pointed at generatePortrait(),
      // which exists only in create.js — a dead button on this page.
      const reason = String(data.error || 'No image was returned.').replace(/</g, '&lt;');
      const who = String(data.provider || 'unknown').replace(/</g, '&lt;');
      loading.innerHTML = '<div style="font-size:0.75rem;color:var(--text-muted);text-align:center;line-height:1.4">'
        + '<span>⚠️ ' + reason + '</span><br>'
        + '<span style="font-size:0.65rem">Provider: ' + who + '</span>'
        + '<br><button onclick="regenerateSheetPortrait()" style="margin-top:6px;padding:2px 10px;font-size:0.7rem;cursor:pointer;background:var(--accent);color:#fff;border:none;border-radius:4px">🔄 Retry</button>'
        + '</div>';
    }
  } catch(e) {
    loading.innerHTML = '⚠️ Failed: ' + (e.message || e);
    console.error(e);
  }
}

