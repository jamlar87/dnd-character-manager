/* One delegated listener per event type.
 *
 * Templates carry data-action="fnName" instead of inline on* attributes. The
 * function must exist on window (the page's classic scripts define them).
 *
 * Arguments come from one of two places:
 *   1. a named spec in REGISTRY (function (el, ev) -> array), for actions with
 *      real logic — see static/dm/actions.js;
 *   2. a declarative data-call descriptor in the markup, for the mechanical
 *      argument marshalling that used to live inline:
 *
 *        data-call='{"a":[{"s":"age"},{"p":"value"}]}'
 *
 *      Argument tokens:
 *        {"n": -1}            number literal
 *        {"s": "text"}        string literal (Jinja fills these in)
 *        {"e": true}          the element itself  (inline `this`)
 *        {"v": true}          the event            (inline `event`)
 *        {"p": "value"}       el.value / el.checked / ...
 *        {"d": "src"}         el.dataset.src      (data-src="...")
 *        {"g": "NAME"}        window.NAME, else the string NAME
 *
 *      Modifiers: "stop" (stopPropagation), "prevent" (preventDefault),
 *      "ifTarget" (run only when the event target is the element, the old
 *      `if (event.target === this)` guard).
 *
 * Why: inline handlers force 'unsafe-inline' in any future CSP, and they pin
 * markup to JS globals. Delegation keeps markup declarative and lets markup
 * rendered later (JS-built rows) work with no re-binding.
 *
 * Nesting: only the closest [data-action] ancestor runs. That reproduces the
 * old `event.stopPropagation()` between a card and a button inside it, without
 * the button having to know the card exists.
 */
(function () {
  'use strict';

  var REGISTRY = Object.create(null);
  // Handler implementations registered by ES modules. Classic scripts still put
  // their handlers on window and stay resolvable; this map is the path that
  // lets a `type="module"` page publish data-action handlers without leaking
  // them onto the global object, and without depending on script order.
  var HANDLERS = Object.create(null);
  // focusin/focusout (not focus/blur) because those do not bubble.
  var TYPES = ['click', 'change', 'input', 'submit', 'dragover', 'drop',
               'dragstart', 'dragend', 'dragleave', 'keydown',
               'focusin', 'focusout'];

  function register(map) {
    Object.keys(map).forEach(function (name) {
      REGISTRY[name] = map[name];
    });
  }

  function resolveToken(token, el, ev) {
    if (token === null || typeof token !== 'object') return token;
    if ('n' in token) return token.n;
    if ('s' in token) return token.s;
    if ('e' in token) return el;
    if ('v' in token) return ev;
    if ('g' in token) {
      var gval = window[token.g];
      return typeof gval === 'undefined' ? token.g : gval;
    }

    // Node source: this / this.parentElement / closest(sel) / #id, then an
    // optional child query. Then either a property or a dataset key.
    var node = token.parent ? el.parentElement : el;
    if ('id' in token) {
      node = document.getElementById(token.id);
    } else if ('closest' in token) {
      node = node && node.closest(token.closest);
    }
    if (node && 'q' in token) node = node.querySelector(token.q);
    if (!node) return undefined;

    var value = node;
    if ('d' in token) {
      var key = token.d.replace(/-([a-z])/g, function (m, c) { return c.toUpperCase(); });
      value = node.dataset ? node.dataset[key] : undefined;
    } else if ('p' in token) {
      value = node[token.p];
    }
    if (token.trim && typeof value === 'string') value = value.trim();
    if (token.int) value = parseInt(value, 10);
    return value;
  }

  function runOps(ops, el, ev) {
    if (ops.stop) ev.stopPropagation();
    if (ops.prevent) ev.preventDefault();
    if (ops.key && !(ev.key === ops.key || ev.code === ops.key)) return false;
    (ops.hide ? [ops.hide] : []).forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.style.display = 'none';
    });
    if (ops.hideSelf) el.style.display = 'none';
    if (ops.toggleClass) {
      var t = ops.toggleClass;
      var target = t.on === 'parent' ? el.parentElement
                 : t.on === 'next' ? el.nextElementSibling
                 : el;
      if (target) target.classList.toggle(t.cls);
      if (t.alsoNext && el.nextElementSibling) el.nextElementSibling.classList.toggle(t.cls);
    }
    if (ops.click) {
      var btn = document.getElementById(ops.click);
      if (btn) btn.click();
    }
    return true;
  }

  // Module-registered handlers win over window globals of the same name: a
  // type="module" page owns its own version of a shared name (closeModal, say)
  // without stomping the classic-script one the rest of the app relies on.
  function handler(name) {
    if (typeof name !== 'string') return null;
    var fn = HANDLERS[name];
    return typeof fn === 'function' ? fn : window[name];
  }

  function callOne(name, args, el, ev) {
    var fn = handler(name);
    if (typeof fn !== 'function') {
      console.warn('[actions] no handler on window for "' + name + '"');
      return;
    }
    // Legacy handlers written for inline dispatch read the global `event`
    // when their call site passes nothing; expose it for the duration of the call.
    var prev = window.event;
    window.event = ev;
    try {
      fn.apply(el, args.map(function (token) { return resolveToken(token, el, ev); }));
    } catch (err) {
      console.error('[actions] handler "' + name + '" threw', err);
    } finally {
      window.event = prev;
    }
  }

  // One element can react to several event types; data-action-map lets the
  // markup say which handler runs for which event without duplicate
  // data-action attributes (the parser keeps only the first one).
  function pickAction(el, ev) {
    var map = el.getAttribute('data-action-map');
    if (map !== null) {
      try {
        var parsed = JSON.parse(map);
        return parsed[ev.type] || null;
      } catch (err) {
        console.warn('[actions] bad data-action-map JSON', err);
        return null;
      }
    }
    // A plain data-action answers activation events only. Focusing a control
    // fires focusin/in before its click, so answering focus too ran every
    // handler twice — a wizard Next click advanced two steps at once.
    if (ev.type === 'focusin' || ev.type === 'focusout') return null;
    // A button or link already turns Enter/Space into a click. Answering the
    // keydown as well ran the handler twice from one keypress — pressing Enter
    // on "Create Character" inserted the sheet twice — and a held key fired
    // again on every repeat. Native activation owns those keys on elements that
    // have it; keydown is for elements that do not (inputs, divs, modals).
    if (ev.type === 'keydown') {
      var key = ev.key;
      if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        var tag = el.tagName;
        var nativeActivation = tag === 'BUTTON' ||
          (tag === 'A' && el.hasAttribute('href')) ||
          (tag === 'INPUT' && (el.type === 'submit' || el.type === 'button'));
        if (nativeActivation) return null;
      }
    }
    return el.getAttribute('data-action');
  }

  function pickCall(el, ev) {
    var map = el.getAttribute('data-call-map');
    if (map !== null) {
      try {
        var parsed = JSON.parse(map);
        if (parsed[ev.type] !== undefined) return parsed[ev.type];
      } catch (err) {
        console.warn('[actions] bad data-call-map JSON', err);
      }
    }
    var raw = el.getAttribute('data-call');
    if (raw === null) return null;
    try {
      return JSON.parse(raw);
    } catch (err) {
      console.warn('[actions] bad data-call JSON on "' + el.getAttribute('data-action') + '"', err);
      return undefined;
    }
  }

  function dispatch(ev) {
    var target = ev.target;
    if (!target) return;

    // closest() resolves the normal case in one hop; the parent walk covers
    // targets that are not Elements (text nodes, synthetic events in tests).
    var el = null;
    var action = null;
    if (typeof target.closest === 'function') {
      el = target.closest('[data-action], [data-action-map]');
      if (el) {
        action = pickAction(el, ev);
        if (!action) el = null;
      }
    }
    var node = el ? null : (target.parentElement || null);
    while (!el && node && node !== document) {
      if (node.hasAttribute && (node.hasAttribute('data-action') ||
                                node.hasAttribute('data-action-map'))) {
        action = pickAction(node, ev);
        if (action) { el = node; break; }
      }
      node = node.parentElement;
    }
    if (!el) return;

    // A form with data-action used to end in `return false`; keep the page from
    // navigating while the handler does its fetch.
    if (ev.type === 'submit' && el.tagName === 'FORM') ev.preventDefault();

    var spec = pickCall(el, ev);
    if (spec === undefined) return;
    if (spec !== null) {
      if (spec.ifTarget && ev.target !== el) return;
      if (!runOps(spec, el, ev)) return;
      if (spec.calls) {
        spec.calls.forEach(function (c) { callOne(c.n, c.a || [], el, ev); });
      } else {
        callOne(action, spec.a || [], el, ev);
      }
      return;
    }

    // A registry spec may own the whole behaviour (it returns null after doing
    // the work), so it outranks the window lookup instead of following it.
    var named = REGISTRY[action];
    var fn = handler(action);
    if (!fn && !named) {
      console.warn('[actions] no handler for data-action="' + action + '"');
      return;
    }
    // No declarative spec and no registry entry: the old inline call was either
    // `fn()` or `fn(this)`. Handlers that declare parameters get the element and
    // the event, which is what `this`/`event` meant inline; zero-arg ones get none.
    var args = named ? named(el, ev) : (fn.length > 0 ? [el, ev] : []);
    if (args === null) return;
    if (!Array.isArray(args)) args = [args];
    if (typeof fn !== 'function') return;

    var prevEvent = window.event;
    window.event = ev;
    try {
      fn.apply(el, args);
    } catch (err) {
      console.error('[actions] handler "' + action + '" threw', err);
    } finally {
      window.event = prevEvent;
    }
  }

  function init() {
    TYPES.forEach(function (type) {
      document.addEventListener(type, dispatch, false);
    });
  }

  // ES modules publish their data-action handlers here instead of assigning to
  // window: `provide({saveCampNotes})` from a type="module" entry point.
  function provide(map) {
    Object.keys(map || {}).forEach(function (name) {
      var fn = map[name];
      if (typeof fn === 'function') HANDLERS[name] = fn;
      else console.warn('[actions] provide("' + name + '") is not a function');
    });
  }

  window.Actions = {
    register: register,
    provide: provide,
    handler: handler,
    dispatch: dispatch
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
