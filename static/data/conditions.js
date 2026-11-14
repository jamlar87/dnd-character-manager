/* The condition table — names, colours, rules text, exhaustion levels.

   Two pages kept their own copy of this data (dm_tools.js and sheet.js) and
   the descriptions had drifted: dm said blinded attacks "have disadvantage",
   sheet said "your attack rolls have disadvantage" (the correct reading of
   the rules), and the wording differed on roughly a dozen entries. Anyone
   reading a condition on the sheet and the same condition in the combat
   tracker saw different text for the same rule.

   This is the single curated table. The wording follows the sheet's copy,
   which was the accurate one, tightened where both copies were vague.
   Layout stays exactly as it was: the pickers render `list` in order, the
   sheet's condition field whitelist uses `names` (the 15 core conditions,
   alphabetical), and both use `colors` / `exhaustionLevels`.

   Loaded as a plain classic script before dm_tools.js and sheet.js.
*/
(function () {
  var COLORS = {
    Blinded: '#6a0dad;color:#fff', Charmed: '#e75480;color:#fff', Deafened: '#5c6bc0;color:#fff',
    Frightened: '#7b1fa2;color:#fff', Grappled: '#bf360c;color:#fff', Incapacitated: '#b71c1c;color:#fff',
    Invisible: '#1e88e5;color:#fff', Paralyzed: '#880e4f;color:#fff', Petrified: '#4e342e;color:#fff',
    Poisoned: '#2e7d32;color:#fff', Prone: '#e65100;color:#fff', Restrained: '#c62828;color:#fff',
    Stunned: '#d84315;color:#fff', Unconscious: '#37474f;color:#fff', Exhaustion: '#827717;color:#fff',
    Concentration: '#6a1b9a;color:#fff', Blessed: '#f9a825;color:#333', Haste: '#00acc1;color:#fff',
    Baned: '#8b0000;color:#fff',
    Raging: '#e8590c;color:#fff'
  };

  // `core: true` marks the 15 conditions from the rules glossary. The rest are
  // combat-tracker states that behave like conditions (and show in the pickers)
  // but are not part of the sheet's condition field.
  var LIST = [
    { name: 'Blinded', core: true,
      desc: "Can't see. Auto-fails ability checks requiring sight. Attack rolls against you have advantage. Your attack rolls have disadvantage." },
    { name: 'Charmed', core: true,
      desc: "Can't attack the charmer or target them with harmful abilities. Charmer has advantage on social checks against you." },
    { name: 'Deafened', core: true,
      desc: "Can't hear. Auto-fails ability checks requiring hearing." },
    { name: 'Exhaustion', core: true,
      desc: '6 levels. 1: disadvantage on ability checks. 2: speed halved. 3: disadvantage on attack rolls and saving throws. 4: hit point maximum halved. 5: speed 0. 6: death.' },
    { name: 'Frightened', core: true,
      desc: "Disadvantage on ability checks and attack rolls while source of fear is in sight. Can't willingly move closer to the source." },
    { name: 'Grappled', core: true,
      desc: "Speed becomes 0. Grappler can release at any time. Ends if grappler is incapacitated or you're moved out of reach." },
    { name: 'Incapacitated', core: true,
      desc: "Can't take actions or reactions." },
    { name: 'Invisible', core: true,
      desc: "Impossible to see without magic or special senses. Attack rolls against you have disadvantage. Your attacks have advantage." },
    { name: 'Paralyzed', core: true,
      desc: "Can't move or speak. Auto-fails STR/DEX saves. Attackers have advantage and any hit within 5 ft is a critical." },
    { name: 'Petrified', core: true,
      desc: "Transformed to solid stone. Incapacitated, can't move or speak. Resistant to all damage. Immune to poison and disease." },
    { name: 'Poisoned', core: true,
      desc: 'Disadvantage on attack rolls and ability checks.' },
    { name: 'Prone', core: true,
      desc: 'Crawling unless you stand up. Disadvantage on attack rolls. Attackers within 5 ft have advantage; farther have disadvantage.' },
    { name: 'Restrained', core: true,
      desc: 'Speed becomes 0. Disadvantage on attack rolls and DEX saves. Attackers have advantage on attacks against you.' },
    { name: 'Stunned', core: true,
      desc: "Incapacitated, can't move, speak falteringly. Auto-fails STR/DEX saves. Attackers have advantage." },
    { name: 'Unconscious', core: true,
      desc: "Incapacitated, can't move or speak, unaware of surroundings. Auto-fails STR/DEX saves. Attackers have advantage; hits within 5 ft are critical." },
    { name: 'Concentration',
      desc: 'Maintaining a spell. Must make CON save on taking damage (DC 10 or half damage, whichever is higher) or lose the spell.' },
    { name: 'Blessed',
      desc: '+1d4 to attack rolls and saving throws (Bless spell).' },
    { name: 'Haste',
      desc: 'Speed doubled, +2 AC, adv on DEX saves, extra action (Attack/Dash/Disengage/Hide/Use Object).' },
    { name: 'Baned',
      desc: 'Subtract 1d4 from attack rolls and saving throws (Bane spell).' },
    { name: 'Raging',
      desc: "Adv on STR checks/saves. +melee damage. Resistant to bludgeoning/piercing/slashing. Can't cast or concentrate. Ends if no attack or damage taken in a round." }
  ];

  var EXHAUSTION_LEVELS = [
    { level: 1, desc: 'Disadvantage on ability checks.' },
    { level: 2, desc: 'Speed halved.' },
    { level: 3, desc: 'Disadvantage on attack rolls and saving throws.' },
    { level: 4, desc: 'Hit point maximum halved.' },
    { level: 5, desc: 'Speed reduced to 0.' },
    { level: 6, desc: 'Death.' }
  ];

  var BY_NAME = {};
  var NAMES = [];
  for (var i = 0; i < LIST.length; i++) {
    BY_NAME[LIST[i].name] = LIST[i];
    if (LIST[i].core) NAMES.push(LIST[i].name);
  }
  NAMES.sort();

  window.DND_CONDITIONS = {
    colors: COLORS,
    list: LIST,
    names: NAMES,
    byName: BY_NAME,
    exhaustionLevels: EXHAUSTION_LEVELS,
    find: function (name) { return BY_NAME[name] || null; }
  };
})();
