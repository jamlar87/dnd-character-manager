/* Campaign detail page — ES module.
 *
 * Was a 105-line inline <script> in campaign_detail.html. Inline script is the
 * one thing a strict CSP cannot allow, so this page is the pilot for the
 * type="module" structure: it imports nothing at runtime except the shared
 * helpers already on window (layout.js loads them before this module runs),
 * and it publishes its data-action handlers through Actions.provide() instead
 * of assigning globals.
 *
 * Config arrives as JSON in #campaign-detail-config — a module cannot read
 * Jinja, and a data-attribute blob keeps it parseable.
 */
import { readConfig, fail } from './page-utils.js';

const cfg = readConfig('campaign-detail-config');
const CAMP_ID = cfg.id;
const CAMP_NAME = cfg.name || '';

function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('open');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('open');
}

function previewCharSheet(id, name) {
  document.getElementById('charSheetTitle').textContent = name;
  document.getElementById('charSheetFrame').src = '/character/' + id;
  openModal('charSheetModal');
}

function reload() {
  window.location.reload();
}

async function post(url, payload) {
  return window.api(url, {method: 'POST', body: payload});
}

async function editCampaignMeta(id) {
  const name = prompt('Campaign name:', CAMP_NAME);
  if (!name) return;
  const pl = prompt('Party level:', String(cfg.party_level || 1)) || '1';
  const ps = prompt('Party size:', String(cfg.party_size || 4)) || '4';
  try {
    await post('/api/dm/campaign/' + id + '/update', {
      name,
      party_level: parseInt(pl, 10),
      party_size: parseInt(ps, 10)
    });
    reload();
  } catch (err) { fail(err); }
}

async function deleteFromPage(id) {
  if (!confirm('Delete this campaign?')) return;
  try {
    await post('/api/dm/campaign/' + id + '/delete');
    window.location.href = '/dm-tools';
  } catch (err) { fail(err); }
}

async function addCharToCampaign(campId) {
  const sel = document.getElementById('charPicker');
  const charId = parseInt(sel.value, 10);
  if (!charId) return;
  try {
    await post('/api/dm/campaign/' + campId + '/add-character', {character_id: charId});
    reload();
  } catch (err) { fail(err); }
}

async function removeCharFromCampaign(campId, charId) {
  if (!confirm('Remove this character from the campaign?')) return;
  try {
    await post('/api/dm/campaign/' + campId + '/remove-character', {character_id: charId});
    reload();
  } catch (err) { fail(err); }
}

function showNpcPicker() { /* stub — redirect to DM Tools NPC tab */ window.location.href = '/dm-tools'; }

async function removeNpcFromCampaign(campId, npcId) {
  if (!confirm('Remove this NPC from the campaign?')) return;
  try {
    await post('/api/dm/campaign/' + campId + '/remove-npc', {npc_id: npcId});
    reload();
  } catch (err) { fail(err); }
}

async function saveNpcNotes(campId, npcId, elementId) {
  const field = document.getElementById(elementId);
  if (!field) return;
  try {
    await post('/api/dm/campaign/' + campId + '/update-npc-notes', {
      npc_id: npcId, notes: field.value
    });
  } catch (err) { fail(err); }
}

async function addQuest(id) {
  const qName = prompt('Quest name:');
  if (!qName) return;
  const status = prompt('Status (active/completed):', 'active') || 'active';
  try {
    await post('/api/dm/campaign/' + id + '/update', {
      addQuest: JSON.stringify({name: qName, status})
    });
    reload();
  } catch (err) { fail(err); }
}

async function removeQuest(campId, idx) {
  if (!confirm('Remove quest?')) return;
  try {
    await post('/api/dm/campaign/' + campId + '/update', {removeQuest: idx});
    reload();
  } catch (err) { fail(err); }
}

async function addLocation(id) {
  const lName = prompt('Location name:');
  if (!lName) return;
  const type = prompt('Type (city/dungeon/forest/etc):', '') || '';
  try {
    await post('/api/dm/campaign/' + id + '/update', {
      addLocation: JSON.stringify({name: lName, type})
    });
    reload();
  } catch (err) { fail(err); }
}

async function removeLocation(campId, idx) {
  if (!confirm('Remove location?')) return;
  try {
    await post('/api/dm/campaign/' + campId + '/update', {removeLocation: idx});
    reload();
  } catch (err) { fail(err); }
}

async function saveCampNotes(id) {
  const field = document.getElementById('campNotes');
  if (!field) return;
  try {
    await post('/api/dm/campaign/' + id + '/update', {notes: field.value});
    alert('Saved!');
  } catch (err) { fail(err); }
}

window.Actions.provide({
  closeModal,
  previewCharSheet,
  editCampaignMeta,
  deleteFromPage,
  addCharToCampaign,
  removeCharFromCampaign,
  showNpcPicker,
  removeNpcFromCampaign,
  saveNpcNotes,
  addQuest,
  removeQuest,
  addLocation,
  removeLocation,
  saveCampNotes
});

export {
  CAMP_ID, closeModal, previewCharSheet, editCampaignMeta, deleteFromPage,
  addCharToCampaign, removeCharFromCampaign, showNpcPicker,
  removeNpcFromCampaign, saveNpcNotes, addQuest, removeQuest, addLocation,
  removeLocation, saveCampNotes
};
