/* Dashboard page: character list filtering, import, share, delete, favourite.
 *
 * Moved out of dashboard.html so the template carries no inline script. Handlers
 * register through Actions.provide(), which events.js consults before window
 * globals; importCharacterFile is additionally mirrored onto window because
 * ui/shell.js — a classic script shared by every page — calls it by name.
 */

function filterChars() {
  const q = (document.getElementById('charSearch')?.value || '').toLowerCase().trim();
  const cls = (document.getElementById('classFilter')?.value || '').toLowerCase();
  const lvl = document.getElementById('levelFilter')?.value || '';
  let count = 0;
  document.querySelectorAll('.char-card-inner').forEach(card => {
    const name = (card.dataset.name || '').toLowerCase();
    const cClass = (card.dataset.class || '').toLowerCase();
    const cLevel = card.dataset.level || '';
    const match = (!q || name.includes(q) || cClass.includes(q))
      && (!cls || cClass === cls)
      && (!lvl || cLevel === lvl);
    card.style.display = match ? '' : 'none';
    if (match) count++;
  });
  const el = document.getElementById('charCount');
  if (el) el.textContent = count + ' shown';
  // the collapsed phone header carries the same number, so a closed filter bar still says
  // how many characters are listed
  const head = document.getElementById('filterCount');
  if (head) head.textContent = count + ' shown';
  const bar = document.querySelector('.filter-bar');
  if (bar) bar.setAttribute('aria-expanded', bar.classList.contains('collapsed') ? 'false' : 'true');
}

async function importCharacterFile(file) {
  if (!file) return;
  const text = await file.text();
  let data;
  try { data = JSON.parse(text); } catch (e) { alert('Not valid JSON'); return; }
  if (!data.character) { alert('Not a character export file'); return; }
  if (!confirm('Import "' + (data.character.name || 'character') + '" as a new character?')) return;
  try {
    const res = await api('/api/character/import', { method: 'POST', body: data });
    alert('Imported "' + res.name + '"');
    location.href = '/character/' + res.id;
  } catch (e) {
    alert('Import failed: ' + e.message);
  }
}

async function toggleShare(id, currentlyShared) {
  // data-call passes "true"/"false" as strings, and "false" is truthy — reading it
  // straight into !x made the Share button a no-op that kept re-sending false.
  const isShared = currentlyShared === true || currentlyShared === 'true';
  const newState = !isShared;
  try {
    await api('/api/character/' + id + '/toggle-share', { method: 'POST', body: { shared: newState } });
    location.reload();
  } catch (e) {
    alert('Failed to update sharing: ' + e.message);
  }
}

async function deleteChar(id) {
  if (!confirm('Permanently delete this character?')) return;
  try {
    await api('/api/character/' + id + '/delete', { method: 'POST' });
    const card = document.getElementById('char-card-' + id);
    if (card) card.remove();
    if (!document.querySelector('.char-card-inner')) location.reload();
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

async function toggleFav(id, el) {
  try {
    await api('/api/characters/' + id + '/favorite', { method: 'POST' });
    location.reload();
  } catch (e) {
    alert('Could not update favourite: ' + e.message);
  }
}

Actions.provide({ filterChars, importCharacterFile, toggleShare, deleteChar, toggleFav });
window.importCharacterFile = importCharacterFile;   // ui/shell.js calls it by name

document.addEventListener('DOMContentLoaded', filterChars);
