/* The table's shared roll feed: one subscriber, one poster, one renderer.
 *
 * Every roll site in the app already funnels through static/dice.js, so this asset does not
 * touch them: it installs a sink (DiceRoller.setSink) and publishes whatever it sees. The
 * surfaces only mount it:
 *
 *   RollFeed.init({ streamUrl, rollsUrl, rollUrl, listEl, statusEl, canRoll, characterId,
 *                   hidden, label })
 *
 * Rules this file must keep:
 * - The server is the authority on what a roll is worth and who may see it: a row carrying a
 *   total is rendered as given, never recomputed here, and nothing is filtered client-side
 *   (a hidden roll simply never arrives on a player stream).
 * - Build rows with textContent. A label is free text a user typed; assigning it through
 *   innerHTML would be an injection into every other player's screen.
 * - Nothing touches document at load time (the asset is require()d under node).
 * - The feed must never break the dice: a failed publish is a missing line, not a lost roll.
 */
(function () {
  'use strict';

  var MAX_ROWS = 200;

  var state = {
    cfg: null,
    rows: [],
    source: null,
    lastId: 0,
    connected: false,
    filter: null,
    hooks: {}
  };

  function defaults() {
    return {
      streamUrl: '', rollsUrl: '', rollUrl: '',
      listEl: null, statusEl: null,
      canRoll: false, characterId: null, hidden: false, label: '',
      limit: 50
    };
  }

  function csrfToken() {
    if (typeof document === 'undefined' || !document.cookie) return '';
    var match = document.cookie.split('; ').filter(function (row) {
      return row.indexOf('csrf_token=') === 0;
    })[0];
    return match ? match.slice('csrf_token='.length) : '';
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function rowLabel(roll) {
    if (roll.character_name) return roll.character_name;
    return roll.who === 'dm' ? 'DM' : 'Player';
  }

  function renderRow(roll) {
    var row = el('div', 'rf-row');
    if (roll.hidden) row.classList.add('rf-hidden');

    row.appendChild(el('span', 'rf-who', rowLabel(roll)));
    if (roll.label) row.appendChild(el('span', 'rf-label', roll.label));
    if (roll.hidden) row.appendChild(el('span', 'rf-lock', 'hidden'));

    var right = el('span', 'rf-right');
    right.appendChild(el('span', 'rf-expr', roll.expression || ''));
    var faces = Array.isArray(roll.results) ? roll.results.join(' + ') : '';
    if (faces && roll.results.length > 1) right.appendChild(el('span', 'rf-faces', faces));
    right.appendChild(el('span', 'rf-total', roll.total));
    row.appendChild(right);
    return row;
  }

  function render() {
    var list = state.cfg && state.cfg.listEl;
    if (!list) return;
    list.textContent = '';
    var rows = state.filter ? state.rows.filter(state.filter) : state.rows;
    if (!rows.length) {
      list.appendChild(el('div', 'rf-empty', 'No rolls yet — the table is quiet.'));
      return;
    }
    for (var i = 0; i < rows.length; i++) list.appendChild(renderRow(rows[i]));
  }

  /** A view preference for the DM's own screen. It hides nothing from anyone else: the
   *  server decides who may see a hidden roll, this only decides what this screen shows. */
  function setFilter(fn) {
    state.filter = (typeof fn === 'function') ? fn : null;
    render();
    return !!state.filter;
  }

  /** Change one option after init (the DM switching whether their own rolls are hidden). */
  function setOptions(opts) {
    if (!state.cfg || !opts) return state.cfg;
    for (var k in opts) if (Object.prototype.hasOwnProperty.call(opts, k)) state.cfg[k] = opts[k];
    return state.cfg;
  }

  function setStatus(text) {
    var node = state.cfg && state.cfg.statusEl;
    if (node) node.textContent = text || '';
  }

  /** A roll from any source: newest first, bounded, and never stored twice. */
  function ingest(roll) {
    if (!roll || (roll.kind && roll.kind !== 'roll')) return false;
    if (typeof roll.id === 'number') {
      for (var i = 0; i < state.rows.length; i++) {
        if (state.rows[i].id === roll.id) return false;   // a replay overlap, not a new roll
      }
      if (roll.id > state.lastId) state.lastId = roll.id;
    }
    state.rows.unshift(roll);
    if (state.rows.length > MAX_ROWS) state.rows.length = MAX_ROWS;
    render();
    return true;
  }

  /** The SSE message handler, split out so it can be driven without a real EventSource. */
  function onEvent(message) {
    if (!message || !message.data) return false;
    var payload;
    try {
      payload = JSON.parse(message.data);
    } catch (e) {
      return false;
    }
    var kind = payload.kind || 'roll';
    if (kind !== 'roll') {
      // Not a feed row: a surface may still be waiting for it (the sheet's HP, for one).
      fireHooks(kind, payload);
      return false;
    }
    return ingest(payload);
  }

  /** Subscribe to a non-roll event kind, or to 'open' / 'error' for the connection itself.
   *  Register AFTER init — init starts from a clean slate of hooks. */
  function on(kind, fn) {
    if (typeof fn !== 'function' || !kind) return false;
    state.hooks[kind] = state.hooks[kind] || [];
    state.hooks[kind].push(fn);
    return true;
  }

  function off(kind) {
    delete state.hooks[kind];
  }

  function fireHooks(kind, payload) {
    var list = state.hooks[kind];
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      // A listener that throws is its own problem: the stream keeps running for everything else.
      try { list[i](payload); } catch (e) { /* ignore */ }
    }
  }

  function isConnected() {
    return !!state.connected;
  }

  function loadHistory() {
    var cfg = state.cfg;
    if (!cfg || !cfg.rollsUrl || typeof fetch !== 'function') return Promise.resolve([]);
    return fetch(cfg.rollsUrl, { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { rolls: [] }; })
      .then(function (data) {
        var rolls = (data && data.rolls) || [];
        // Oldest first into the list so the newest ends up on top.
        for (var i = rolls.length - 1; i >= 0; i--) ingest(rolls[i]);
        return rolls;
      })
      .catch(function () { return []; });
  }

  function connect() {
    var cfg = state.cfg;
    if (!cfg || !cfg.streamUrl || typeof EventSource === 'undefined') return null;
    var url = cfg.streamUrl;
    if (state.lastId) url += (url.indexOf('?') === -1 ? '?' : '&') + 'last_id=' + state.lastId;
    state.source = new EventSource(url);
    state.source.addEventListener('open', function () {
      state.connected = true;
      setStatus('');
      fireHooks('open', {});
    });
    state.source.addEventListener('message', onEvent);
    state.source.addEventListener('error', function () {
      var was = state.connected;
      state.connected = false;
      setStatus('Reconnecting…');
      if (was) fireHooks('error', {});
    });
    return state.source;
  }

  /** Publish a roll the app already made. Never throws: the dice are not the feed's hostage. */
  function post(roll, opts) {
    var cfg = state.cfg;
    opts = opts || {};
    if (!cfg || !cfg.rollUrl || typeof fetch !== 'function') return Promise.resolve(null);
    var body = {
      expression: roll.expr,
      results: roll.rolls,
      label: opts.label !== undefined ? opts.label : (cfg.label || ''),
      hidden: opts.hidden !== undefined ? !!opts.hidden : !!cfg.hidden,
      source: cfg.source || 'table'
    };
    if (cfg.characterId) body.character_id = cfg.characterId;

    var headers = { 'Content-Type': 'application/json' };
    var token = csrfToken();
    if (token) headers['X-CSRF-Token'] = token;      // ignored by the key-authenticated path

    return fetch(cfg.rollUrl, {
      method: 'POST', headers: headers, body: JSON.stringify(body)
    }).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  /** Wire every roll on the page into the feed (the DM tools and sheet path). */
  function installSink() {
    if (!window.DiceRoller || !window.DiceRoller.setSink) return false;
    window.DiceRoller.setSink(function (result) {
      post(result, {});
    });
    return true;
  }

  function init(cfg) {
    var merged = defaults();
    for (var k in cfg) if (Object.prototype.hasOwnProperty.call(cfg, k)) merged[k] = cfg[k];
    state.cfg = merged;
    state.rows = [];
    state.lastId = 0;
    state.filter = null;
    state.hooks = {};

    var history = loadHistory();
    history.then(function () { connect(); });
    if (merged.publish !== false && merged.rollUrl) installSink();
    render();
    return state.cfg;
  }

  function feedRows() {
    return state.rows.slice();
  }

  function disconnect() {
    if (state.source) {
      try { state.source.close(); } catch (e) { /* already closed */ }
      state.source = null;
    }
    state.connected = false;
  }

  window.RollFeed = {
    init: init,
    onEvent: onEvent,
    ingest: ingest,
    post: post,
    render: render,
    feedRows: feedRows,
    disconnect: disconnect,
    installSink: installSink,
    setFilter: setFilter,
    setOptions: setOptions,
    on: on,
    off: off,
    isConnected: isConnected,
    MAX_ROWS: MAX_ROWS
  };
})();
