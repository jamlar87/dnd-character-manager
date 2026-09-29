/* Click-to-roll dice — ONE asset for every surface that shows rules text.
 *
 * Any `2d6+3`-shaped expression inside rendered content becomes clickable and rolls itself,
 * which is the one feature from Atlas VTT that needs no new subsystem: the app already
 * renders dice in monster blocks, traps, spells and sheet attacks.
 *
 * Contract (tests/test_dice_roller.py runs parse()/roll() under node):
 *   DiceRoller.parse(expr) -> {count, sides, mod} | null
 *   DiceRoller.roll(expr)  -> {expr, rolls, mod, total, text}
 *   DiceRoller.enhance(root) -> wraps matches in span.dice-roll, idempotent
 *
 * Rules this file must keep:
 * - No code evaluation, and no HTML-string assignment of page text (build nodes with
 *   textContent instead).
 * - Never wrap text inside <script> <style> <textarea> <input> <select> <button> <a>:
 *   a button would also fire its own handler, a source badge <a> would break the link.
 * - enhance() marks the root it walked, so a second pass cannot double-wrap.
 * - Nothing touches document at load time (the asset is require()d under node).
 */
(function () {
  'use strict';

  var DICE_RE = /(?<![A-Za-z0-9_.])(\d{0,3})d(\d{1,3})(?:\s*([+-])\s*(\d{1,3}))?(?![A-Za-z0-9_])/gi;

  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, SELECT: 1, OPTION: 1,
                    BUTTON: 1, A: 1, CODE: 1, PRE: 1 };

  function parse(expr) {
    if (typeof expr !== 'string') return null;
    var m = DICE_RE.exec(expr.trim());
    DICE_RE.lastIndex = 0;
    if (!m) return null;
    // the whole trimmed string has to be the expression — "2d6+3 and then" is prose
    if (m.index !== 0 || m[0].length !== expr.trim().length) return null;
    var count = m[1] ? parseInt(m[1], 10) : 1;
    var sides = parseInt(m[2], 10);
    var mod = 0;
    if (m[3] && m[4]) mod = (m[3] === '-' ? -1 : 1) * parseInt(m[4], 10);
    if (!count || count > 100 || !sides || sides > 1000) return null;
    return { count: count, sides: sides, mod: mod };
  }

  function roll(expr) {
    var spec = parse(expr);
    if (!spec) return null;
    var rolls = [];
    var sum = 0;
    for (var i = 0; i < spec.count; i++) {
      var face = 1 + Math.floor(Math.random() * spec.sides);
      rolls.push(face);
      sum += face;
    }
    var total = sum + spec.mod;
    var parts = [String(rolls.join(' + '))];
    if (spec.mod) parts.push((spec.mod > 0 ? '+ ' : '- ') + Math.abs(spec.mod));
    var text = expr.trim() + ' = ' + parts.join(' ');
    return { expr: expr.trim(), rolls: rolls, mod: spec.mod, total: total, text: text };
  }

  function ensureStyle() {
    if (typeof document === 'undefined' || document.getElementById('dice-roll-style')) return;
    var st = document.createElement('style');
    st.id = 'dice-roll-style';
    st.textContent =
      '.dice-roll{cursor:pointer;border-bottom:1px dotted currentColor;font-weight:600;' +
      'border-radius:3px;padding:0 1px}' +
      '.dice-roll:hover{background:var(--accent,#c9a227);color:var(--bg,#111)}' +
      '#dice-toast{position:fixed;right:1rem;bottom:1rem;z-index:4000;background:var(--bg,#16161a);' +
      'color:var(--text,#eee);border:1px solid var(--accent,#c9a227);border-radius:8px;' +
      'padding:.5rem .75rem;font-size:.85rem;box-shadow:0 4px 18px rgba(0,0,0,.45);max-width:22rem}' +
      '#dice-toast .dice-total{font-weight:700;color:var(--accent,#c9a227)}';
    document.head.appendChild(st);
  }

  function toast(res) {
    if (typeof document === 'undefined') return;
    ensureStyle();
    var box = document.getElementById('dice-toast');
    if (!box) {
      box = document.createElement('div');
      box.id = 'dice-toast';
      document.body.appendChild(box);
    }
    box.textContent = '';
    var line = document.createElement('div');
    line.textContent = res.text;
    var total = document.createElement('span');
    total.className = 'dice-total';
    total.textContent = ' = ' + res.total;
    line.appendChild(total);
    box.appendChild(line);
    box.style.display = 'block';
    clearTimeout(box._diceTimer);
    box._diceTimer = setTimeout(function () {
      if (box.parentNode) box.parentNode.removeChild(box);
    }, 5000);
  }

  function skippable(el) {
    for (var node = el; node && node.nodeType === 1; node = node.parentNode) {
      if (SKIP_TAGS[node.tagName]) return true;
      if (node.classList && node.classList.contains('dice-roll')) return true;
      if (node.dataset && node.dataset.noDice !== undefined) return true;
    }
    return false;
  }

  function enhance(root) {
    if (typeof document === 'undefined') return 0;
    if (!root) root = document.body;
    if (!root || root.nodeType !== 1) return 0;
    if (root.dataset && root.dataset.diceDone !== undefined) return 0;
    ensureStyle();
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var targets = [];
    var node;
    while ((node = walker.nextNode())) {
      var value = node.nodeValue || '';
      if (value.indexOf('d') === -1 && value.indexOf('D') === -1) continue;
      if (skippable(node.parentNode)) continue;
      DICE_RE.lastIndex = 0;
      var probe = value.match(/[0-9]{0,3}d[0-9]{1,3}(?:\s*[+-]\s*[0-9]{1,3})?/gi);
      if (probe && probe.length) targets.push(node);
    }
    var made = 0;
    for (var i = 0; i < targets.length; i++) {
      var text = targets[i].nodeValue;
      var re = /(?<![A-Za-z0-9_.])(\d{0,3})d(\d{1,3})(?:\s*([+-])\s*(\d{1,3}))?(?![A-Za-z0-9_])/gi;
      var frag = document.createDocumentFragment();
      var last = 0;
      var m;
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var span = document.createElement('span');
        span.className = 'dice-roll';
        span.setAttribute('role', 'button');
        span.setAttribute('tabindex', '0');
        span.setAttribute('title', 'Click to roll');
        span.textContent = m[0];
        span.addEventListener('click', function (ev) {
          ev.stopPropagation();
          var res = roll(this.textContent);
          if (res) toast(res);
        });
        span.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            var res = roll(this.textContent);
            if (res) toast(res);
          }
        });
        frag.appendChild(span);
        made++;
        last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (made && targets[i].parentNode) targets[i].parentNode.replaceChild(frag, targets[i]);
    }
    if (root.dataset) root.dataset.diceDone = '1';
    return made;
  }

  window.DiceRoller = { parse: parse, roll: roll, enhance: enhance };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      // Opt-in per surface: enhance(document.body) is deliberately NOT called here, because
      // the sheet's JS re-renders its own panels and would fight the walker. Surfaces call
      // DiceRoller.enhance(el) after they render.
    });
  }
})();
