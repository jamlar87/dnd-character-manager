"""Dashboard character cards render the portrait when one is set.

Portraits are stored as multi-MB base64 data URLs, so the card must reference
the cacheable image route (downscaled) instead of inlining the blob — the
dashboard lists every character a user can see.
"""

import base64
import io
import re
import sqlite3

import pytest


def make_char(seeded_db, name, user_id=1, **cols):
    cols.setdefault("race", "Human")
    cols.setdefault("class_name", "Fighter")
    cols.setdefault("level", 3)
    cols.setdefault("hp_current", 10)
    cols.setdefault("hp_max", 12)
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


def png_data_url(w=400, h=300, color=(120, 60, 200)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class TestDashboardCardPortrait:
    def test_base64_portrait_becomes_a_thumbnail_url(self, client, seeded_db, auth_headers):
        url = png_data_url()
        cid = make_char(seeded_db, "Portrait Probe", portrait_url=url)
        html = client.get("/dashboard", headers=auth_headers).text
        assert f'/api/character/{cid}/portrait-image?size=128' in html
        # the portrait blob itself must never be inlined (layout's SVG favicon is fine)
        assert "data:image/png;base64" not in html, "base64 portrait inlined into the dashboard"
        assert url.split(",", 1)[1][:80] not in html
        assert re.search(rf'<img[^>]+src="/api/character/{cid}/portrait-image\?size=128"[^>]*>', html)
        assert 'class="char-card-portrait"' in html
        assert 'loading="lazy"' in html

    def test_character_without_portrait_gets_an_initial_placeholder(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Zed Noart", portrait_url=None)
        html = client.get("/dashboard", headers=auth_headers).text
        card = html[html.index(f'id="char-card-{cid}"'):]
        card = card[:card.index("</div>\n</div>")] if "</div>\n</div>" in card else card[:3000]
        assert "char-card-portrait-empty" in card
        assert ">Z<" in card or ">\n      Z" in card
        assert f'/api/character/{cid}/portrait-image' not in card

    def test_empty_string_portrait_is_treated_as_absent(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Blank Portrait", portrait_url="")
        html = client.get("/dashboard", headers=auth_headers).text
        card = html[html.index(f'id="char-card-{cid}"'):][:2000]
        assert "char-card-portrait-empty" in card
        assert f'/api/character/{cid}/portrait-image' not in card

    def test_external_url_is_used_directly(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Remote Portrait",
                        portrait_url="https://example.com/portrait.png")
        html = client.get("/dashboard", headers=auth_headers).text
        card = html[html.index(f'id="char-card-{cid}"'):][:2000]
        assert 'src="https://example.com/portrait.png"' in card


class TestPortraitThumbnail:
    def test_thumbnail_is_bounded_and_smaller(self, client, seeded_db, auth_headers):
        from PIL import Image
        cid = make_char(seeded_db, "Thumb Probe", portrait_url=png_data_url(600, 400))
        full = client.get(f"/api/character/{cid}/portrait-image", headers=auth_headers)
        thumb = client.get(f"/api/character/{cid}/portrait-image?size=128", headers=auth_headers)
        assert full.status_code == thumb.status_code == 200
        assert full.headers["content-type"] == "image/png"
        img = Image.open(io.BytesIO(thumb.content))
        assert max(img.size) <= 128, f"thumbnail is {img.size}"
        assert max(img.size) > 64, "thumbnail got downscaled far past the request"
        assert len(thumb.content) < len(full.content)
        assert thumb.headers["content-type"] == "image/webp"
        assert "max-age" in thumb.headers.get("cache-control", "")

    def test_size_is_clamped(self, client, seeded_db, auth_headers):
        from PIL import Image
        cid = make_char(seeded_db, "Clamp Probe", portrait_url=png_data_url(1200, 900))
        r = client.get(f"/api/character/{cid}/portrait-image?size=99999", headers=auth_headers)
        assert r.status_code == 200
        assert max(Image.open(io.BytesIO(r.content)).size) <= 1024
        r2 = client.get(f"/api/character/{cid}/portrait-image?size=1", headers=auth_headers)
        assert r2.status_code == 200
        assert max(Image.open(io.BytesIO(r2.content)).size) <= 16

    def test_no_size_returns_the_original_bytes(self, client, seeded_db, auth_headers):
        url = png_data_url(300, 300)
        cid = make_char(seeded_db, "Original Probe", portrait_url=url)
        r = client.get(f"/api/character/{cid}/portrait-image", headers=auth_headers)
        assert r.content == base64.b64decode(url.split(",", 1)[1])

    def test_scope_is_enforced_with_size(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Other Users Char", user_id=2, portrait_url=png_data_url())
        r = client.get(f"/api/character/{cid}/portrait-image?size=128", headers=auth_headers)
        assert r.status_code == 404

    def test_external_portrait_still_redirects(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Redirect Probe",
                        portrait_url="https://example.com/p.png")
        r = client.get(f"/api/character/{cid}/portrait-image?size=128",
                       headers=auth_headers, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "https://example.com/p.png"

    def test_missing_portrait_404s(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "No Portrait")
        r = client.get(f"/api/character/{cid}/portrait-image?size=128", headers=auth_headers)
        assert r.status_code == 404

    def test_broken_data_url_does_not_500(self, client, seeded_db, auth_headers):
        cid = make_char(seeded_db, "Broken Portrait", portrait_url="data:image/png;base64,!!!not-base64!!!")
        r = client.get(f"/api/character/{cid}/portrait-image?size=128", headers=auth_headers)
        assert r.status_code == 404
