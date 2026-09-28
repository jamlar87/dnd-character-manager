"""The shared layout CSS + JS are versioned static assets, not inline on every page.

layout.html shipped ~7.9 KB of <style> and ~9.5 KB of <script> inline, identical on every
page (dashboard, sheet, dm-tools, create …): re-parsed per load and never cacheable. They now
ride in static/layout.css and static/layout.js behind static_asset_version() hashes.

What must not regress: the assets keep the behaviour the inline block had (the CSRF fetch glue,
the theme toggle, the manual-search panel), they are linked on every page, and the CSS still
loads BEFORE the page's own {% block style %} so the cascade is unchanged.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = (ROOT / "templates" / "layout.html").read_text()
CSS = (ROOT / "static" / "layout.css").read_text()
JS = (ROOT / "static" / "layout.js").read_text()


class TestTheLayoutAssetsAreReal:
    def test_the_assets_carry_the_shared_code(self):
        assert len(CSS) > 5_000, "layout.css lost its rule blocks"
        assert len(JS) > 5_000, "layout.js lost its code"
        # the three behaviours the inline block provided
        assert "X-CSRF-Token" in JS, "the CSRF fetch glue is gone"
        assert "csrf_token" in JS
        assert "toggleTheme" in JS and "dnd_theme" in JS
        assert "searchManuals" in JS

    def test_no_jinja_survived_the_move(self):
        """They are static files: a Jinja tag here would ship to the browser verbatim."""
        for name, text in (("layout.css", CSS), ("layout.js", JS)):
            assert not re.search(r"\{\{.*?\}\}|\{%.*?%\}", text, re.S), f"Jinja in {name}"


class TestTheLayoutServesThem:
    def test_every_page_links_both_assets(self, client, seeded_db, auth_headers):
        for path in ("/dashboard", "/dm-tools", "/create"):
            html = client.get(path, headers=auth_headers).text
            assert re.search(r'<link rel="stylesheet" href="/static/layout\.css\?v=\w+">', html), path
            assert re.search(r'/static/layout\.js\?v=\w+', html), path
            # the inline blocks are gone
            assert "Add the double-submit CSRF token" not in html, f"{path} still inlines the glue"
            # the big shared rule block went to the asset (the tiny ai-pdf rule stays inline)
            assert "--bg: #1a1a2e" not in html, f"{path} still inlines the layout CSS"

    def test_the_cascade_order_is_unchanged(self, client, seeded_db, auth_headers):
        """layout.css must come before the page's own {% block style %} output, or a page's
        overrides start losing to the base rules."""
        html = client.get("/create", headers=auth_headers).text
        assert ".wizard-ste" in html, "create.html's own style block vanished"
        assert html.index("/static/layout.css?v=") < html.index(".wizard-ste")

    def test_the_layout_js_loads_before_the_pages_own_scripts(self, client, seeded_db, auth_headers):
        html = client.get("/dm-tools", headers=auth_headers).text
        assert html.index("/static/layout.js?v=") < html.index("dm_tools.js")

    def test_the_page_budget_holds(self, client, seeded_db, auth_headers):
        """The layout assets removed ~17.4 KB from every page."""
        for path, budget in (("/dashboard", 30_000), ("/dm-tools", 100_000), ("/create", 40_000)):
            n = len(client.get(path, headers=auth_headers).text)
            assert n < budget, f"{path} is back to {n} B"
