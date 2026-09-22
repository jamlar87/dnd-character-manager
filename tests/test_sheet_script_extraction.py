"""Guards for the sheet-script extraction + static-data split (Sept 2026).

`templates/_sheet_scripts.html` used to inline ~306 KB of JavaScript into every
sheet response (~733 KB pages) and the per-character config carried ~245 KB of
reference tables (classes, races, feats, summons, source slugs) that are
identical for every character. Now:

- `static/sheet.js`        — the code (cacheable static asset)
- `static/sheet-ref.js`    — character-independent tables, written at startup by
                             `routes.characters.sheet.ensure_sheet_reference_asset`
- `_sheet_scripts.html`    — only the small inline `window.SHEETCFG` block with
                             per-character values

Contract pinned here: no code creeping back inline, no Jinja in the static files,
and every `SHEETCFG.`/`SHEETREF.` read in the JS must be provided by exactly one
of the two sources (no dead keys either way).
"""

import json
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "templates" / "_sheet_scripts.html"
STATIC_JS = REPO / "static" / "sheet.js"
REF_JS = REPO / "static" / "sheet-ref.js"


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
    keys = list(cols)
    con = sqlite3.connect(str(seeded_db["db_path"]))
    con.execute(
        f"INSERT INTO characters (user_id, name{''.join(', ' + k for k in keys)}) "
        f"VALUES (?, ?{', ?' * len(keys)})",
        [user_id, name, *[cols[k] for k in keys]],
    )
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid


def config_block(html: str) -> str:
    start = html.index("window.SHEETCFG = {")
    end = html.index("\n};", start) + 3
    return html[start:end]


class TestTemplateIsConfigOnly:
    def test_no_javascript_body_left_in_the_template(self):
        tpl = TEMPLATE.read_text()
        assert "window.SHEETCFG = {" in tpl
        assert "sheet-ref.js?v=" in tpl
        assert "/static/sheet.js?v=" in tpl
        for marker in ("function ", "=> {", "if (", "addEventListener", "document."):
            assert marker not in tpl, f"inline JS crept back into the template: {marker!r}"
        assert "sheet.js?v={{ sheet_js_version }}" in tpl, "sheet.js must be version-busted by hash"
        assert len(tpl) < 20000, f"template is {len(tpl)} bytes — config only, expected < 20 KB"

    def test_reference_asset_is_loaded_before_the_code(self):
        tpl = TEMPLATE.read_text()
        assert tpl.index("sheet-ref.js") < tpl.index("sheet.js")


class TestStaticFiles:
    def test_sheet_js_is_substantial_and_jinja_free(self):
        js = STATIC_JS.read_text()
        assert len(js) > 100_000, "sheet.js looks truncated"
        assert "{{" not in js and "{%" not in js, "unrendered Jinja in sheet.js"
        assert "function togglePassiveBody" in js
        assert "function showDetail" in js

    def test_reference_asset_is_jinja_free(self):
        assert REF_JS.exists(), "static/sheet-ref.js missing — startup must generate it"
        src = REF_JS.read_text()
        assert "{{" not in src and "{%" not in src
        assert "window.SHEETREF = {" in src


class TestConfigContract:
    def test_keys_line_up_on_both_sides(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Config Probe", class_name="Rogue", level=5,
                        class_levels=json.dumps({"Rogue": 5}))
        html = client.get(f"/character/{cid}", headers=auth_headers).text
        assert "{{" not in html, "unrendered Jinja in the sheet page"
        block = config_block(html)

        js = STATIC_JS.read_text()
        cfg_used = set(re.findall(r"SHEETCFG\.([A-Za-z_][A-Za-z0-9_]*)", js))
        ref_used = set(re.findall(r"SHEETREF\.([A-Za-z_][A-Za-z0-9_]*)", js))
        cfg_keys = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", block, re.M))

        ref_src = REF_JS.read_text()
        start = ref_src.index("window.SHEETREF = ") + len("window.SHEETREF = ")
        ref_payload = json.loads(ref_src[start:ref_src.index(";\n", start)])
        ref_keys = set(ref_payload)

        assert cfg_used, "no SHEETCFG reads found in sheet.js"
        assert ref_used, "no SHEETREF reads found in sheet.js"
        assert not (cfg_used - cfg_keys), \
            f"sheet.js reads SHEETCFG keys the page does not render: {sorted(cfg_used - cfg_keys)}"
        assert not (cfg_keys - cfg_used), \
            f"page renders unused SHEETCFG keys (dead weight): {sorted(cfg_keys - cfg_used)}"
        assert not (ref_used - ref_keys), \
            f"sheet.js reads SHEETREF keys the asset lacks: {sorted(ref_used - ref_keys)}"
        assert not (ref_keys - ref_used), \
            f"reference asset carries unused keys: {sorted(ref_keys - ref_used)}"

    def test_reference_asset_carries_real_tables(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Ref Probe")
        client.get(f"/character/{cid}", headers=auth_headers)   # triggers generation
        src = REF_JS.read_text()
        start = src.index("window.SHEETREF = ") + len("window.SHEETREF = ")
        payload = json.loads(src[start:src.index(";\n", start)])
        from main import CLASSES, RACES
        assert set(payload) == {"classes", "races", "bgInfo", "summonTemplates",
                                "sourceSlugMap", "namedItemTypes", "featDetails",
                                "knownFeats", "maneuverOptions", "skillAbMap"}
        assert set(payload["classes"]) == set(CLASSES), "classes table drifted from the app"
        assert set(payload["races"]) >= set(RACES), "races table drifted from the app"
        assert len(payload["summonTemplates"]) > 5
        assert len(payload["sourceSlugMap"]) > 5

    def test_per_character_values_are_real(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Value Probe", class_name="Rogue", level=7,
                        class_levels=json.dumps({"Rogue": 7}), strength=17,
                        expertise_skills=json.dumps(["Stealth"]))
        html = client.get(f"/character/{cid}", headers=auth_headers).text
        block = config_block(html)
        assert re.search(rf"charId:\s*{cid}\b", block)
        assert re.search(r"charLevel:\s*7\b", block)
        assert re.search(r"charClassName:\s*\"Rogue\"", block)
        assert re.search(r"strScore:\s*17\b", block)
        assert "expertiseCount" in block

    def test_page_stays_small_and_has_no_inline_code(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Weight Probe", class_name="Fighter", level=3)
        html = client.get(f"/character/{cid}", headers=auth_headers).text
        assert "function togglePassiveBody" not in html, "sheet code is inline again"
        block = config_block(html)
        assert len(block) < 40_000, f"inline config block is {len(block)} bytes"
        # reference tables must NOT be inlined any more
        assert "window.SHEETREF" not in html, "reference tables inlined into the page again"
        assert "/static/sheet-ref.js?v=" in html


class TestStaticAssetsAreCacheable:
    """Cloudflare caches a response only when it carries no Set-Cookie, so the
    global CSRF cookie made every /static/ asset cf-cache-status: BYPASS. Static
    responses must stay cookie-free and explicitly cacheable."""

    def test_static_response_has_no_cookie_and_is_cacheable(self, client):
        r = client.get("/static/sheet.js")
        assert r.status_code == 200
        assert "set-cookie" not in {k.lower() for k in r.headers}, \
            "a Set-Cookie on /static/ makes Cloudflare bypass the cache"
        assert "max-age" in r.headers.get("cache-control", ""), \
            "static assets need an explicit Cache-Control"

    def test_html_pages_still_get_the_csrf_cookie(self, client):
        r = client.get("/")
        assert "csrf_token" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}

    def test_sheet_js_url_matches_the_file_on_disk(self, client, seeded_db, auth_headers):
        import hashlib
        from routes.characters.sheet import sheet_asset_version
        cid = make_char(seeded_db, "Hash Probe")
        html = client.get(f"/character/{cid}", headers=auth_headers).text
        want = sheet_asset_version("sheet.js")
        assert f"/static/sheet.js?v={want}" in html
        assert hashlib.sha1(STATIC_JS.read_bytes()).hexdigest()[:10] == want


class TestConfigValuesAreJsonEncoded:
    """A Jinja precedence trap shipped a broken page once: `{{ x or ""|tojson }}`
    applies `tojson` to `""` alone, so a non-empty subclass/background/alignment
    leaked out unquoted (`charSubclass: Assassin`) and killed the whole SHEETCFG
    block — every sheet function then failed with 'SHEETCFG is not defined'.
    An empty-valued character hides it, so these use a populated one."""

    def populated(self, client, seeded_db, auth_headers):
        cid = make_char(
            seeded_db, "Quoting Probe", race="Tortle", subrace="Mire Tortle",
            class_name="Rogue", subclass="Assassin", level=5,
            background="Turtle Shell Tactician", alignment="Chaotic O'Good",
            class_levels=json.dumps({"Rogue": 5}), strength=14, dexterity=18,
            constitution=14, intelligence=12, wisdom=10, charisma=8,
            skills=json.dumps(["Stealth", "Perception"]),
            expertise_skills=json.dumps(["Stealth"]),
        )
        return client.get(f"/character/{cid}", headers=auth_headers).text

    def test_every_value_parses_as_json(self, client, seeded_db, auth_headers):
        block = config_block(self.populated(client, seeded_db, auth_headers))
        bad = []
        for mm in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*): (.*?),?$", block, re.M):
            try:
                json.loads(mm.group(2))
            except Exception:
                bad.append(f"{mm.group(1)}: {mm.group(2)[:50]}")
        assert not bad, f"config values are not JSON-encoded (JS breaks): {bad}"

    def test_string_values_keep_their_quotes(self, client, seeded_db, auth_headers):
        block = config_block(self.populated(client, seeded_db, auth_headers))
        vals = dict(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*): (.*?),?$", block, re.M))
        # decoded round-trip: catches unquoted values AND mangled escaping
        assert json.loads(vals["charSubclass"]) == "Assassin"
        assert json.loads(vals["charBackground"]) == "Turtle Shell Tactician"
        assert json.loads(vals["charAlignment"]) == "Chaotic O'Good"
        assert json.loads(vals["charRace"]) == "Tortle"
        assert json.loads(vals["charNameJson"]) == "Quoting Probe"
