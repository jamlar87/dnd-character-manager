/* Argument specs for the DM-tools page's data-action handlers.
 *
 * Actions with no arguments need no entry here — Actions.dispatch() calls them
 * with none. Only the ones that read markup carry a spec.
 */
(function () {
  'use strict';

  function num(value) {
    var n = Number(value);
    return isNaN(n) ? value : n;
  }

  window.Actions.register({
    /* encounters */
    toggleShareEncounter: function (el) {
      return [el.dataset.id, el.dataset.shared === 'true'];
    },
    copyEncounter: function (el) { return [el.dataset.id]; },
    openEncounter: function (el) { return [el.dataset.id]; },
    deleteEncounter: function (el) { return [el.dataset.id]; },

    /* npcs */
    editNpc: function (el) { return [num(el.dataset.id)]; },
    deleteNpc: function (el) { return [num(el.dataset.id)]; },
    buildNpcToCharacter: function (el) {
      return [num(el.dataset.id), el.dataset.name];
    },

    /* campaigns */
    previewCharSheet: function (el) {
      return [num(el.dataset.id), el.dataset.name];
    },
    saveCampNotes: function (el) {
      var id = el.dataset.id;
      return [num(id), el.dataset.notes || ('campNotes-' + id)];
    },
    saveNpcNotes: function (el) {
      return [num(el.dataset.id), num(el.dataset.npcId), el.dataset.notes];
    },
    addCharToCampaign: function (el) { return [num(el.dataset.id)]; },
    addLocation: function (el) { return [num(el.dataset.id)]; },
    addQuest: function (el) { return [num(el.dataset.id)]; },
    deleteFromPage: function (el) { return [num(el.dataset.id)]; },
    editCampaignMeta: function (el) { return [num(el.dataset.id)]; },
    showNpcPicker: function (el) { return [num(el.dataset.id)]; },
    removeCharFromCampaign: function (el) {
      return [num(el.dataset.id), num(el.dataset.charId)];
    },
    removeNpcFromCampaign: function (el) {
      return [num(el.dataset.id), num(el.dataset.npcId)];
    },
    removeLocation: function (el) {
      return [num(el.dataset.id), num(el.dataset.index)];
    },
    removeQuest: function (el) {
      return [num(el.dataset.id), num(el.dataset.index)];
    },
    deleteCampaign: function (el) { return [num(el.dataset.id)]; },
    importCampaignPack: function (el) { return [el]; },
    triggerFileInput: function (el) {
      var target = document.getElementById(el.dataset.target);
      if (target) target.click();
      return null;
    },

    /* modals */
    closeModal: function (el) { return [el.dataset.modal]; },
    closeModalIfSelf: function (el, ev) {
      return ev.target === el ? [el.dataset.modal] : null;
    },
    closeItemPickerIfSelf: function (el, ev) {
      if (ev.target !== el) return null;
      closeItemPicker();
      return null;
    },

    /* forms */
    generateAiEncounter: function (el, ev) { return [ev]; },
    saveTrap: function (el, ev) { return [ev]; },

    /* traps */
    toggleTrapDetail: function (el) { return [el]; },
    editTrap: function (el) { return [num(el.dataset.id)]; },
    deleteTrap: function (el) { return [num(el.dataset.id)]; },
    openSourceRef: function (el) { return [el.dataset.src]; },

    /* item picker: the search box is the source of truth for every trigger */
    searchItemPicker: function (el) {
      var box = document.getElementById('itemPickerSearch');
      return [box ? box.value : el.value];
    },

    /* combat drag and drop */
    allowDrop: function (el, ev) {
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
      return null;
    },
    dropOnBenched: function (el, ev) {
      ev.preventDefault();
      return [ev];
    },
    dropOnPlayersPanel: function (el, ev) {
      ev.preventDefault();
      return [ev];
    },
    loadCombatCampaignThenSave: function () {
      loadCombatCampaign().then(function () { saveCombatState(); });
      return null;
    },
    toggleCombatFiltersThenFilter: function () {
      toggleCombatFilters();
      filterCombatCreatures();
      return null;
    }
  });
})();
