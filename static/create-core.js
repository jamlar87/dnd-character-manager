/* split from static/create.js — classic script, shares the global scope of the other create-*.js files. */
/* The /create wizard: step flow, race/class/subclass pickers, ability + skill pickers,
   spell picker and portrait step. Served as a versioned static asset (the page keeps only
   per-user HTML + the SHEETCFG-style small inline glue), so the browser and Cloudflare
   cache it instead of re-parsing ~90 KB of JS on every wizard load.

   Global data it reads comes from /static/create-consts.js (window.RACES etc.);
   RACE_NAME_POOLS there carries the server-side name pools merged into RACE_NAMES. */


const SKILL_DESCS = {
  "Acrobatics": "Balance, flips, and staying on your feet. Escape grapples, walk tightropes, and land safely from falls. (Dex)",
  "Animal Handling": "Calming animals, predicting behavior, and controlling mounts. Soothe a spooked horse or befriend a wild beast. (Wis)",
  "Arcana": "Knowledge of spells, magic items, eldritch symbols, and the planes. Recall lore about magical traditions and identify spells. (Int)",
  "Athletics": "Climbing, jumping, swimming, and feats of strength. Scale walls, break free of restraints, and win physical contests. (Str)",
  "Deception": "Lying, misdirection, and concealing the truth. Bluff guards, disguise intentions, and pull off cons. (Cha)",
  "History": "Knowledge of past events, legendary figures, ancient kingdoms, and wars. Recall historical context about people and places. (Int)",
  "Insight": "Reading body language and sensing hidden motives. Detect lies, gauge intentions, and predict someone's next move. (Wis)",
  "Intimidation": "Coercion through threats, menace, or implied violence. Interrogate prisoners, cow enemies, and project authority. (Cha)",
  "Investigation": "Deduction, puzzle-solving, and searching for clues. Find hidden objects, piece together evidence, and spot traps. (Int)",
  "Medicine": "Diagnosing illness, stabilizing the dying, and treating injuries. Identify poisons, perform first aid, and understand anatomy. (Wis)",
  "Nature": "Knowledge of terrain, plants, animals, and weather. Identify flora/fauna, predict weather, and navigate wilderness. (Int)",
  "Perception": "Spotting, hearing, and noticing details. The most-used skill — determines passive Perception, detects ambushes, and finds hidden threats. (Wis)",
  "Performance": "Entertaining audiences through music, dance, or storytelling. Earn coin, distract crowds, and command attention. (Cha)",
  "Persuasion": "Influencing people through charm, logic, or diplomacy. Negotiate deals, convince NPCs, and talk your way past guards. (Cha)",
  "Religion": "Knowledge of gods, cults, rituals, and holy symbols. Recall lore about deities, undead, and divine magic. (Int)",
  "Sleight of Hand": "Pickpocketing, palming objects, and manual trickery. Lift keys, conceal weapons, and perform stage magic. (Dex)",
  "Stealth": "Moving silently and staying hidden. Sneak past guards, tail targets, and set ambushes. (Dex)",
  "Survival": "Tracking, foraging, and navigating the wild. Follow trails, find food/water, and avoid natural hazards. (Wis)",
};

// PHB 2014 p.125-141 — Background descriptions
const BACKGROUND_INFO = {
  "Acolyte":       "You served in a temple. Skill Proficiencies: Insight, Religion. Languages: Two of your choice. Equipment: Holy symbol, prayer book, 5 sticks of incense, vestments, common clothes, 15 gp. Feature: Shelter of the Faithful.",
  "Charlatan":     "You've made a living by your wits. Skill Proficiencies: Deception, Sleight of Hand. Tool Proficiencies: Disguise kit, forgery kit. Equipment: Fine clothes, disguise kit, tools of the con, 15 gp. Feature: False Identity.",
  "Criminal":      "You are an experienced criminal. Skill Proficiencies: Deception, Stealth. Tool Proficiencies: One gaming set, thieves' tools. Equipment: Crowbar, dark clothes, 15 gp. Feature: Criminal Contact.",
  "Entertainer":   "You thrive before an audience. Skill Proficiencies: Acrobatics, Performance. Tool Proficiencies: Disguise kit, one musical instrument. Equipment: Musical instrument, costume, 15 gp. Feature: By Popular Demand.",
  "Folk Hero":     "You come from a humble social rank. Skill Proficiencies: Animal Handling, Survival. Tool Proficiencies: One artisan's tools, land vehicles. Equipment: Artisan's tools, shovel, iron pot, common clothes, 10 gp. Feature: Rustic Hospitality.",
  "Guild Artisan": "You are a member of an artisan's guild. Skill Proficiencies: Insight, Persuasion. Tool Proficiencies: One artisan's tools. Languages: One of your choice. Equipment: Artisan's tools, letter of introduction, traveler's clothes, 15 gp. Feature: Guild Membership.",
  "Hermit":        "You lived in seclusion. Skill Proficiencies: Medicine, Religion. Tool Proficiencies: Herbalism kit. Equipment: Scroll case of notes, winter blanket, common clothes, herbalism kit, 5 gp. Feature: Discovery.",
  "Noble":         "You were born into wealth and power. Skill Proficiencies: History, Persuasion. Tool Proficiencies: One gaming set. Languages: One of your choice. Equipment: Fine clothes, signet ring, scroll of pedigree, 25 gp. Feature: Position of Privilege.",
  "Outlander":     "You grew up in the wilds. Skill Proficiencies: Athletics, Survival. Tool Proficiencies: One musical instrument. Languages: One of your choice. Equipment: Staff, hunting trap, trophy, traveler's clothes, 10 gp. Feature: Wanderer.",
  "Sage":          "You spent years learning lore. Skill Proficiencies: Arcana, History. Languages: Two of your choice. Equipment: Black ink, quill, small knife, letter from dead colleague, common clothes, 10 gp. Feature: Researcher.",
  "Sailor":        "You have sailed the high seas. Skill Proficiencies: Athletics, Perception. Tool Proficiencies: Navigator's tools, water vehicles. Equipment: Belaying pin, 50 ft silk rope, lucky charm, common clothes, 10 gp. Feature: Ship's Passage.",
  "Soldier":       "You served in a military force. Skill Proficiencies: Athletics, Intimidation. Tool Proficiencies: One gaming set, land vehicles. Equipment: Insignia of rank, trophy, bone dice, common clothes, 10 gp. Feature: Military Rank.",
  "Urchin":        "You grew up on the streets alone. Skill Proficiencies: Sleight of Hand, Stealth. Tool Proficiencies: Disguise kit, thieves' tools. Equipment: Small knife, city map, pet mouse, token of parents, common clothes, 10 gp. Feature: City Secrets.",
  "Custom":        "Define your own background. Equipment: 3 useful items of your choice, traveler's clothes, 10 gp. Feature: Your own unique story.",
};
const STD_ARRAY = [15,14,13,12,10,8];
const ABILITIES = ["strength","dexterity","constitution","intelligence","wisdom","charisma"];

const ABILITY_DESCS = {
  "strength": "Measures bodily power and athletic training. Governs: melee attack & damage rolls, thrown weapons, Athletics skill, carrying capacity (STR × 15 lbs), and breaking objects. Key for Barbarians, Fighters, and Paladins.",
  "dexterity": "Measures agility, reflexes, and balance. Governs: AC bonus (light/medium armor), ranged & finesse attack rolls, initiative, Stealth and Acrobatics, and Dex saves — the most common saving throw.",
  "constitution": "Measures health, stamina, and vital force. Governs: HP gained per level (+CON mod each level), concentration checks for spellcasters, and enduring poison, disease, and extreme conditions. Important for EVERY class.",
  "intelligence": "Measures mental acuity and reasoning. Governs: Wizard spellcasting DC/attacks, Arcana/History/Investigation/Nature/Religion skills, and recalling lore. Key for Wizards and knowledge-seekers.",
  "wisdom": "Measures perception, intuition, and attunement to the world. Governs: Cleric/Druid/Ranger spellcasting, Perception/Insight/Survival/Medicine, passive Perception, and Wisdom saves (vs charm/fear).",
  "charisma": "Measures confidence, eloquence, and force of personality. Governs: Bard/Paladin/Sorcerer/Warlock spellcasting, Deception/Intimidation/Performance/Persuasion, and all social interactions.",
};

// Ideal ability priority by class (standard array: 15→highest priority, 8→lowest)
const CLASS_PRIORITY = {
  "Barbarian":  ["strength","constitution","dexterity","wisdom","charisma","intelligence"],
  "Bard":       ["charisma","dexterity","constitution","wisdom","intelligence","strength"],
  "Cleric":     ["wisdom","strength","constitution","dexterity","charisma","intelligence"],
  "Druid":      ["wisdom","constitution","dexterity","intelligence","strength","charisma"],
  "Fighter":    ["strength","constitution","dexterity","wisdom","charisma","intelligence"],
  "Monk":       ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
  "Paladin":    ["strength","charisma","constitution","wisdom","dexterity","intelligence"],
  "Ranger":     ["dexterity","wisdom","constitution","strength","intelligence","charisma"],
  "Rogue":      ["dexterity","constitution","wisdom","intelligence","charisma","strength"],
  "Sorcerer":   ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
  "Warlock":    ["charisma","constitution","dexterity","wisdom","intelligence","strength"],
  "Wizard":     ["intelligence","constitution","dexterity","wisdom","charisma","strength"],
};

// Ideal skill picks by class (in priority order, picks up to skill_count)
const CLASS_IDEAL_SKILLS = {
  "Barbarian":  ["Athletics","Perception","Survival","Intimidation","Nature","Animal Handling"],
  "Bard":       ["Persuasion","Deception","Performance","Insight","Acrobatics","Stealth"],
  "Cleric":     ["Insight","Persuasion","Medicine","Religion","History"],
  "Druid":      ["Perception","Survival","Nature","Animal Handling","Insight","Medicine"],
  "Fighter":    ["Athletics","Perception","Survival","Intimidation","Acrobatics","Insight"],
  "Monk":       ["Acrobatics","Stealth","Insight","Athletics","Religion","History"],
  "Paladin":    ["Athletics","Persuasion","Insight","Intimidation","Religion","Medicine"],
  "Ranger":     ["Perception","Stealth","Survival","Nature","Animal Handling","Investigation"],
  "Rogue":      ["Stealth","Perception","Sleight of Hand","Investigation","Acrobatics","Deception"],
  "Sorcerer":   ["Persuasion","Deception","Intimidation","Insight","Arcana","Religion"],
  "Warlock":    ["Deception","Intimidation","Persuasion","Arcana","Investigation","Religion"],
  "Wizard":     ["Arcana","Investigation","Insight","History","Religion","Medicine"],
};

// Race name generator tables — expanded pools for variety
const RACE_NAMES = {
  "Dwarf": {
    male:["Thorin","Durin","Balin","Dwalin","Oin","Gloin","Bofur","Bombur","Nori","Dori","Fili","Kili","Gimli","Dain","Thrain","Thror","Fundin","Gror","Farin","Borin","Nain","Bifur","Floi","Loni","Frar","Onar","Brokk","Eitri","Sindri","Thekk","Vist"],
    female:["Dis","Hilda","Brunhild","Gerta","Helga","Inga","Sigrid","Thyra","Yrsa","Kara","Freya","Astrid","Ragna","Gudrun","Sif","Bodil","Dagmar","Frida","Ingrid","Sigrun","Thora","Aldis","Eira","Liv","Revna","Solveig","Torunn","Unn","Ylva","Brynhild"],
    clan:["Ironforge","Battlehammer","Bronzebeard","Darkiron","Stoutmantle","Deepdelve","Anvilmar","Stonebrow","Hammersmith","Forgefire","Thunderaxe","Runefist","Steelmantle","Goldshaper","Coppervault","Grimstone","Boulderhelm","Firebeard","Frostforge","Shieldbreaker","Oathkeeper","Rubyhelm","Stormshield","Ironfist"]
  },
  "Elf": {
    male:["Elrond","Thranduil","Legolas","Finrod","Celeborn","Haldir","Fingolfin","Glorfindel","Cirdan","Eol","Maedhros","Fingon","Turgon","Thingol","Beleg","Gwindor","Amroth","Oropher","Galathil","Angrod","Aegnor","Caranthir","Celegorm","Curufin","Maglor","Daeron","Saeros","Voronwe","Edrahil","Gildor","Lindir"],
    female:["Galadriel","Arwen","Luthien","Idril","Nimrodel","Earwen","Aredhel","Elwing","Miriel","Finduilas","Melian","Elenwe","Nienna","Varda","Yavanna","Este","Nessa","Indis","Nerdanel","Anaire","Lalwen","Amarie","Celebrian","Elanor","Morwen","Nienor","Rian","Silmarien","Tar-Miriel","Beruthiel","Ilmare"],
    clan:["Silverleaf","Moonshadow","Starbreeze","Dawnwhisper","Nightbreeze","Goldenoak","Swiftarrow","Brightsong","Dewdrop","Windwalker","Starlight","Moonsong","Dawnmist","Silverbrook","Summerstar","Wintershade","Crystalbrook","Amberwave","Fairbreeze","Lightfoot","Silverveil","Dreamweaver","Moonrise","Starfall"]
  },
  "Halfling": {
    male:["Bilbo","Frodo","Samwise","Meriadoc","Peregrin","Fredegar","Tobold","Hamfast","Drogo","Odo","Adelard","Andwise","Blanco","Bodo","Carl","Cottar","Dinodas","Dodinas","Everard","Falco","Ferdinand","Ferumbras","Folco","Fortinbras","Gormadoc","Halfred","Harding","Holman","Isembard","Largo","Lotho","Madoc","Marmadas","Mungo","Nob","Olo","Paladin","Posco","Reginard","Robin","Rorimac","Rudigar","Saradas","Saradoc","Tolman","Wilcome"],
    female:["Rosie","Belladonna","Primula","Lobelia","Daisy","Marigold","Pearl","Esmeralda","Pansy","Ruby","Amaranth","Angelica","Asphodel","Belba","Camellia","Celandine","Cora","Dahlia","Donnamira","Eglantine","Elanor","Estella","Gilly","Hanna","Ivy","Jasmine","Lily","Linda","Malva","Mirabella","Myrtle","Petunia","Poppy","Primrose","Rosa","Rosamunda","Rowan","Salvia","Sapphire","Tulip","Viola","Zinnia"],
    clan:["Baggins","Took","Brandybuck","Gamgee","Bolger","Proudfoot","Greenhand","Brockhouse","Goodbody","Chubb","Banks","Boffin","Bracegirdle","Bunce","Burrows","Cotton","Fairbairn","Goold","Grubb","Hayward","Hornblower","Longbottom","Maggot","Noakes","Pott","Roper","Sandyman","Smallburrow","Twofoot","Whitfoot"]
  },
  "Human": {
    male:["Aldric","Cedric","Edmund","Garret","Harold","Lothar","Merek","Oswald","Roland","Theron","Alaric","Baldwin","Beric","Caspian","Conrad","Corwin","Darius","Darian","Eddard","Eldric","Emmerich","Ewald","Florian","Gareth","Gawain","Geraint","Gregor","Hadrian","Hartwin","Jorah","Kendrick","Leofric","Manfred","Mathis","Odric","Ormund","Percival","Ragnar","Reinhardt","Roderick","Sigismund","Talbot","Thaddeus","Tobias","Tybalt","Ulric","Valerian","Victor","Wilfred","Wolfram"],
    female:["Alys","Brynn","Catelyn","Elara","Gwendolyn","Isolde","Liana","Morwen","Rowena","Seraphine","Adela","Anya","Beatrix","Celandra","Daria","Eleanora","Elyse","Freya","Geneva","Helena","Ilyana","Jessamy","Katrin","Lenore","Lisette","Lyanna","Magnolia","Margot","Mira","Odette","Petra","Ravenna","Renata","Sabine","Selene","Tanith","Tatiana","Thessaly","Valeria","Vesper","Yvette","Zelda"],
    clan:["Hawke","Blackwood","Stormwind","Ashford","Ravencroft","Thornfield","Westbrook","Northrend","Hightower","Greymane","Brightshield","Coldwater","Dawnguard","Eastwatch","Falconer","Frost","Goldcrest","Highcastle","Ironwood","Kingsley","Lockwood","Mistvale","Oakheart","Redmane","Shadowvale","Silverton","Starling","Steel","Strong","Swift","Warbringer","Weatherby","Whiteoak","Windham","Wolfhart"]
  },
  "Dragonborn": {
    male:["Kriv","Medrash","Nadarr","Pandjed","Patrin","Rhogar","Sora","Torrin","Ushar","Vrakas","Adrex","Bharash","Donaar","Durir","Erash","Faar","Ghesh","Harann","Heskan","Kava","Korinn","Korth","Maaz","Menereth","Mishann","Naar","Orin","Orn","Perra","Rhas","Shamash","Shedinn","Tarhun","Thava","Uadjit","Vaal","Verin","Vrondiss","Zaan","Zarosh","Zorath"],
    female:["Akra","Biri","Daar","Farideh","Harann","Jheri","Kava","Korinn","Nala","Sora","Arileth","Baylith","Ceras","Dira","Erris","Fenrys","Ghesha","Havilar","Iris","Kass","Kethra","Lorath","Maeris","Nalass","Ophir","Paela","Quila","Raiann","Shaena","Tazith","Uri","Vessa","Welsa","Xyra","Yrissa","Zofie"],
    clan:["Clethtinthiallor","Daardendrian","Delmirev","Drachedandion","Fenkenkabradon","Kepeshkmolik","Kerrhylon","Nemmonis","Verthisathurgiesh","Yarjerit","Akambherylliax","Bhergav","Cheth","Daar","Dendi","Esthanaar","Gix","Kanjentellequor","Linxakasendalor","Myastan","Norixius","Ophinshtalajiir","Prexijandilin","Shestendeliath","Turnuroth","Vayemniri","Weryon"]
  },
  "Gnome": {
    male:["Fizzwick","Gimble","Nackle","Orryn","Pock","Quill","Sprocket","Tinker","Wizzle","Zook","Alston","Boddynock","Coggle","Dabble","Eldon","Fiddle","Gadget","Hobble","Jingle","Kettle","Loopmottin","Mender","Nix","Oddle","Pibble","Rumble","Snibble","Thimble","Vex","Wobble","Yonkle"],
    female:["Bimpnottin","Caramip","Duvamil","Ellywick","Lilli","Loopmottin","Mardnab","Roywyn","Shamil","Zanna","Arinda","Breena","Carlin","Donella","Ellyjobell","Frug","Gilla","Helva","Joybell","Kithri","Lolly","Mopsa","Nyxie","Orla","Pippa","Quinby","Roslin","Tana","Ummy","Vexia","Wenna","Xelli","Yoli","Zina"],
    clan:["Beren","Daergel","Folkor","Garrick","Nackle","Raulnor","Scheppen","Turen","Warrick","Wiggens","Aleslosh","Ashhearth","Bafflestone","Cogglepot","Dappledew","Fapplestamp","Gimble","Higgle","Jumble","Kettlewhistle","Nimblefizz","Pockle","Quibble","Rangle","Scattercloak","Sparklegem","Thimblegear","Tosslecoat","Whizzle","Zenick"]
  },
  "Half-Elf": {
    male:["Aelar","Caelum","Doran","Eryndor","Fenris","Kael","Lorien","Myles","Theron","Varek","Arannis","Berrian","Coren","Darian","Elrohir","Faelan","Garel","Hadrian","Ilphas","Jorah","Keth","Lirien","Maethor","Nalion","Orin","Peren","Quillan","Raegar","Soril","Talasin","Uldred","Varis","Westin","Xandor","Yorin","Zephyr"],
    female:["Aeris","Caelia","Elowen","Illyria","Kyra","Lyra","Maeris","Nyx","Seren","Vaela","Arwyn","Berenice","Corinne","Delphine","Elara","Fianna","Gwyneth","Iris","Jessara","Kethra","Lunara","Miriel","Nerys","Ophelia","Phaedra","Quinna","Rowena","Sylvie","Thalia","Una","Vianne","Wisteria","Xanthe","Ysolde","Zara"],
    clan:["Amakiir","Ilphelkiir","Moonflower","Wintermere","Summerwind","Autumnvale","Springbrook","Truehart","Goodfellow","Whispermoon","Brightwood","Dawnfield","Evernight","Fairmeadow","Goldengrove","Highhollow","Ivywood","Mistral","Riverstone","Shadowglen","Silvermist","Starfall","Thornwood","Wildrose"]
  },
  "Half-Orc": {
    male:["Durgash","Goruk","Hrogath","Krusk","Lurtz","Morg","Ogruk","Thokk","Ulfgrim","Zugor","Azog","Borgakh","Durgath","Garoth","Grishnak","Huruk","Kazrak","Khargol","Magra","Mog","Nargol","Ogol","Ront","Shagrol","Taruk","Uloth","Vorgak","Wurzak","Xarg","Yagak","Zog"],
    female:["Borga","Druga","Grenka","Hagra","Kella","Murook","Rogga","Sutha","Urzul","Vorga","Azuk","Baggi","Durgath","Ekk","Forga","Gruna","Hurki","Kansif","Lurka","Neega","Ovak","Prug","Quagg","Rendar","Shautha","Taruk","Ugga","Wrek","Yatur","Zogga"],
    clan:["Bonecrusher","Doomhammer","Ironhide","Skullsplitter","Warsong","Bloodfist","Gorehowl","Dreadmaw","Stormrage","Blackrock","Bloodaxe","Bonechewer","Corpsegrinder","Dreadskull","Frostrider","Gorefiend","Hatefury","Ironskin","Killgore","Maul","Ragehowl","Sever","Skullcrusher","Spinebreaker","Thundertusk","Warhowl"]
  },
  "Tiefling": {
    male:["Akmenos","Damakos","Ekemon","Iados","Kairon","Leucis","Melech","Morthos","Phelan","Skamos","Amnon","Arkan","Barachiel","Carac","Caim","Dama","Ged","Hadran","Incus","Israfel","Kallik","Levistus","Malkizid","Mammon","Merodach","Moloch","Naberius","Orias","Raum","Rhyxali","Sallos","Shax","Sitri","Valafar","Vapula","Vepar","Verin","Xaphan","Zagan","Zepar"],
    female:["Akta","Anakis","Bryseis","Criella","Damaia","Ea","Kallista","Lerissa","Makaria","Nemeia","Akriel","Arista","Belladonna","Calista","Demeter","Eris","Fiera","Gorgona","Hecate","Jezebel","Kali","Lamia","Lilith","Medea","Naamah","Nyx","Onyx","Persephone","Raven","Sable","Tempest","Vespera","Willow","Xenia","Yama","Zara"],
    clan:["Art","Carrion","Chant","Creed","Despair","Fear","Glory","Hope","Ideal","Music","Reverie","Sorrow","Torment","Weary","Anguish","Beauty","Chaos","Darkness","Ecstasy","Fury","Grief","Harmony","Infinity","Justice","Knowledge","Liberty","Madness","Nightmare","Oblivion","Pain","Quest","Ruin","Silence","Twilight","Vengeance","Whimsy"]
  }
};

// Merge server-side expanded race names (includes all 42 ingested races)
Object.assign(RACE_NAMES, window.RACE_NAME_POOLS || {});

const STARTING_EQUIPMENT = {
  "Barbarian":["Greataxe","2 Handaxes","Explorer's Pack","4 Javelins"],
  "Bard":["Rapier","Entertainer's Pack","Lute","Leather Armor","Dagger"],
  "Cleric":["Mace","Scale Mail","Light Crossbow + 20 Bolts","Priest's Pack","Shield","Holy Symbol"],
  "Druid":["Wooden Shield","Scimitar","Leather Armor","Explorer's Pack","Druidic Focus"],
  "Fighter":["Chain Mail","Longsword","Shield","Light Crossbow + 20 Bolts","Dungeoneer's Pack"],
  "Monk":["Shortsword","Dungeoneer's Pack","10 Darts"],
  "Paladin":["Longsword","Shield","5 Javelins","Priest's Pack","Chain Mail","Holy Symbol"],
  "Ranger":["Longbow + 20 Arrows","Shortsword","Scale Mail","Explorer's Pack"],
  "Rogue":["Rapier","Shortbow + 20 Arrows","Burglar's Pack","Leather Armor","2 Daggers","Thieves' Tools"],
  "Sorcerer":["Light Crossbow + 20 Bolts","Arcane Focus","Dungeoneer's Pack","2 Daggers"],
  "Warlock":["Light Crossbow + 20 Bolts","Arcane Focus","Scholar's Pack","Leather Armor","Dagger"],
  "Wizard":["Quarterstaff","Arcane Focus","Scholar's Pack","Spellbook"]
};

let state = { race: null, subrace: null, class_name: null, subclass: null, abilities: {}, skills: [], expertise_skills: [], fighting_style: "", metamagic: [], invocations: [], pact_boon: "", maneuvers: [], magical_secrets: [], totem_spirits: {}, hunters_prey: "", infusions: [], favored_enemies: [], favored_terrains: [], asi_picks: [], mpmm_mode: "", mpmm_p2: "", mpmm_p1: "", mpmm_t3: [], name: "", background: "", alignment: "", personality: "", backstory: "", level: 1, equipment: [], showAllSkills: false, unlockedSkills: [], subclass_bonus: [] };
let step = 0;

// Subclass bonus proficiency map (same as backend SUBCLASS_PROFICIENCIES)
const SUBCLASS_BONUS_MAP = {
  "Knowledge Domain": { type:"languages", count:2, options:["Common","Dwarvish","Elvish","Giant","Gnomish","Goblin","Halfling","Orc","Abyssal","Celestial","Draconic","Deep Speech","Infernal","Primordial","Sylvan","Undercommon"] },
  "College of Lore": { type:"skills", count:3 }  // options = ALL_SKILLS from backend
};

const STEP_NAMES = ['Race', 'Class', 'Abilities', 'Skills', 'Equipment', 'Review'];

function updateDots() {
  document.querySelectorAll('.step-dot').forEach((d, i) => {
    d.classList.toggle('active', i === step);
    d.classList.toggle('done', i < step);
  });
  const label = document.getElementById('stepName');
  if (label) {
    label.innerHTML = `Step ${step + 1} of 6 · ${STEP_NAMES[step] || ''}` +
      (state.race || state.class_name ? ' <small>— ' + [state.race, state.class_name].filter(Boolean).join(' ') + '</small>' : '');
  }
}

// ── searching the option grids ────────────────────────────────────────────────────────────
// 133 races over 26 rows with no way to look one up is a scroll hunt; the same goes for classes.
function filterOptions(input, gridIds) {
  const q = (input.value || '').trim().toLowerCase();
  let shown = 0, total = 0;
  gridIds.forEach(id => {
    const grid = document.getElementById(id);
    if (!grid) return;
    grid.querySelectorAll('.option-card').forEach(card => {
      total++;
      const hit = !q || (card.textContent || '').toLowerCase().includes(q);
      card.style.display = hit ? '' : 'none';
      if (hit) shown++;
    });
  });
  const host = input.closest('.opt-tools');
  const count = host && host.querySelector('.option-count');
  setText(count, q ? `${shown} of ${total} match` : (total ? `${total} options` : ''));
}

// Writing the SAME text still replaces the text node, which is a childList mutation — and the
// observer below watches for exactly that, so an unguarded write here loops forever.
function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}

// keeps the unfiltered count honest after a grid is (re)built or the source filter changes
function refreshOptionCounts() {
  const pairs = [['race-count', ['race-grid', 'subrace-grid']], ['class-count', ['class-grid', 'subclass-grid']]];
  pairs.forEach(([id, grids]) => {
    const el = document.getElementById(id);
    if (!el) return;
    let n = 0;
    grids.forEach(gid => {
      const g = document.getElementById(gid);
      if (!g) return;
      g.querySelectorAll('.option-card').forEach(c => { if (c.style.display !== 'none') n++; });
    });
    const input = el.closest('.opt-tools') && el.closest('.opt-tools').querySelector('input[type="search"]');
    if (input && input.value.trim()) return;    // a filtered count was set by filterOptions
    setText(el, n ? `${n} options` : '');
  });
}

// ── option cards are buttons in everything but markup ──────────────────────────────────────
// They were <div onclick> only: unreachable by keyboard, and invisible to a screen reader.
document.addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const card = document.activeElement;
  if (!card || !card.classList || !card.classList.contains('option-card')) return;
  event.preventDefault();
  card.click();
});

function makeCardsFocusable(root) {
  (root || document).querySelectorAll('.option-card:not([data-kb])').forEach(card => {
    card.dataset.kb = '1';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    if (!card.hasAttribute('aria-label')) card.setAttribute('aria-label', card.dataset.name || card.textContent.trim().slice(0, 60));
  });
}

// The grids are rebuilt by a dozen builders, so watch the wizard instead of chasing them all.
document.addEventListener('DOMContentLoaded', () => {
  const wizard = document.getElementById('wizard');
  if (!wizard) return;
  makeCardsFocusable(wizard);
  refreshOptionCounts();
  updateDots();                                 // names the step; nothing else runs it on load
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;                         // coalesce: the grids rebuild in bursts
    queued = true;
    requestAnimationFrame(() => { queued = false; makeCardsFocusable(wizard); refreshOptionCounts(); });
  }).observe(wizard, { childList: true, subtree: true });
});

function showStep(n) {
  document.querySelectorAll('.wizard-step').forEach(s => s.classList.remove('active'));
  document.querySelector(`[data-step="${n}"]`).classList.add('active');
  step = n; updateDots();
  if (n === 3) buildSkills();
  if (n === 5) { buildReview(); if (!state.portraitGenerated) { state.portraitGenerated = true; generatePortrait(); } }
}

function nextStep() {
  if (step === 0 && !state.race) { alert('Choose a race'); return; }
  if (step === 1 && !state.class_name) { alert('Choose a class'); return; }
  // Intercept: fighting style picker for Fighter L1 (after class step)
  if (step === 1 && hasL1FightingStyle() && !state._fightingStyleShown) {
    showFightingStylePicker();
    return;
  }
  // Intercept: infusion picker for Artificer L2 (after class step)
  if (step === 1 && hasL1Infusions() && !state._infusionShown) {
    showInfusionPicker();
    return;
  }
  // Intercept: favored enemy picker for Ranger L1
  if (step === 1 && hasL1FavoredEnemy() && !state._favoredEnemyShown) {
    showFavoredEnemyPicker();
    return;
  }
  // Intercept: favored terrain picker for Ranger L1 (after FE picked)
  if (step === 1 && hasL1FavoredTerrain() && state._favoredEnemyShown && !state._favoredTerrainShown) {
    showFavoredTerrainPicker();
    return;
  }
  // Intercept: expertise picker for classes with L1 Expertise (after skills step)
  if (step === 3 && hasL1Expertise() && !state._expertiseShown) {
    showExpertisePicker();
    return;
  }
  // Intercept: known casters go to spell picker before review
  if (step === 4 && isKnownCaster()) {
    showSpellPicker();
    return;
  }
  // Intercept: subclass with bonus proficiencies (e.g. Knowledge Domain languages)
  if (step === 4 && state.subclass && SUBCLASS_BONUS_MAP[state.subclass]) {
    showSubclassBonusPicker();
    return;
  }
  showStep(Math.min(step + 1, 5));
}

function prevStep() { showStep(Math.max(step - 1, 0)); }

// Abbreviate source string for compact display
function _shortSrc(src) {
  if (!src) return '';
  const map = {
    'Handbook': 'PHB',
    'Xanathar': 'XGE',
    'Sword Coast': 'SCAG',
    'Tasha': 'TCE',
    'Dungeon Master': 'DMG',
    'Volo': 'VGM',
    'Elemental Evil': 'EEPC',
    'Eberron': 'ERLW',
    'Guildmasters': 'GGR',
    'Mordenkainen': 'MTF',
    'Tortle Package': 'TTP',
  };
  for (const [key, abbr] of Object.entries(map)) {
    if (src.includes(key)) return abbr;
  }
  return src.length > 20 ? src.slice(0,18)+'…' : src;
}

function _srcBadge(src, slug) {
  if (!src) return '';
  const short = _shortSrc(src);
  const safeSrc = src.replace(/'/g, "\\\\'");
  const safeSlug = slug ? slug.replace(/'/g, "\\\\'") : '';
  return `<span class="src-badge" onclick="openSourceRef(this.dataset.src, this.dataset.slug)" data-src="${src}" data-slug="${slug||''}" title="Click to open ${src}">📚 ${short}</span>`;
}

// openSourceRef() lives in static/ui/source_ref.js.


// ── Step 0: Race ──
let _coreOnlyCreate = localStorage.getItem('coreOnly') === 'true';
const _CORE_BOOKS_C = ['PHB','PHB 2014','DMG','DMG 2014','MM','XGE','TCE','TCE 2020','VGM','MTF','SCAG','ERLW','FTD','MPMM','BPGG'];
const _CORE_NAMES_C = ["player's handbook","dungeon master's guide","monster manual","xanathar's guide","tasha's cauldron","volo's guide","mordenkainen's tome","sword coast adventurer's guide","eberron: rising","fizban's treasury","mordenkainen presents","bigby presents","glory of the giants","elemental evil player's companion"];
function isCoreSourceC(src) {
  if (!src) return true;
  const lower = src.toLowerCase();
  const abbr = lower.split(' p.')[0].trim();
  if (_CORE_BOOKS_C.some(b => abbr === b.toLowerCase())) return true;
  if (_CORE_NAMES_C.some(n => lower.includes(n))) return true;
  return false;
}
function toggleCoreCreate(el) {
  _coreOnlyCreate = !_coreOnlyCreate;
  localStorage.setItem('coreOnly', _coreOnlyCreate);
  el.querySelector('.core-create-box').textContent = _coreOnlyCreate ? '☑' : '☐';
  el.style.color = _coreOnlyCreate ? 'var(--accent)' : 'var(--text-muted)';
  // Update all toggle labels to stay in sync
  document.querySelectorAll('.core-create-box').forEach(b => b.textContent = _coreOnlyCreate ? '☑' : '☐');
  document.querySelectorAll('label').forEach(l => { if (l.querySelector('.core-create-box')) l.style.color = _coreOnlyCreate ? 'var(--accent)' : 'var(--text-muted)'; });
  if (typeof refreshOptionCounts === 'function') refreshOptionCounts();
  buildRaces();
  buildClasses();
  // Refresh subrace/subclass grids for any selected race/class
  if (state.race) selectRace(state.race);
  if (state.class_name) selectClass(state.class_name);
}
