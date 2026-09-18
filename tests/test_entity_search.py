"""Entity search: the nav search bar's "Internal data" section.

`services/entity_search.py` indexes everything the app knows internally (SRD core
+ ingested manual JSON) so the nav search can list items, creatures, NPCs, feats,
races, traits, spells, backgrounds, subclasses, traps and class features
alongside manual page hits.

Expected row shape: {kind, name, subtitle, snippet, source, slug, page, score}
"""

import pytest
from pathlib import Path

from services.entity_search import (
    KIND_META,
    entity_detail,
    index_stats,
    search_entities,
)

ALL_KINDS = {"item", "creature", "npc", "feat", "race", "trait",
             "spell", "background", "subclass", "trap", "feature"}


def kinds_of(results):
    return {r["kind"] for r in results}


def by_name(results, name):
    return [r for r in results if r["name"].lower() == name.lower()]


class TestIndex:
    def test_index_covers_every_kind(self):
        stats = index_stats()
        assert ALL_KINDS <= set(stats["counts"]), f"missing kinds: {ALL_KINDS - set(stats['counts'])}"
        for kind, n in stats["counts"].items():
            assert n > 0, f"{kind} indexed nothing"

    def test_kind_meta_has_icon_and_label(self):
        for kind in ALL_KINDS:
            meta = KIND_META[kind]
            assert meta["icon"] and meta["label"], f"{kind} missing meta"

    def test_rows_carry_the_contract_fields(self):
        rows = search_entities("longsword")
        assert rows, "longsword should match something"
        for r in rows:
            assert set(r) >= {"kind", "name", "subtitle", "snippet", "score"}
            assert r["kind"] in ALL_KINDS


class TestSearchByKind:
    def test_finds_item_by_name(self):
        rows = by_name(search_entities("longsword"), "Longsword")
        assert rows and rows[0]["kind"] == "item"

    def test_finds_creature_by_name(self):
        rows = by_name(search_entities("goblin"), "Goblin")
        # 'Goblin' is also a playable race in the manual data — the stat block
        # must still be listed alongside it.
        assert any(r["kind"] == "creature" for r in rows), [r["kind"] for r in rows]

    def test_creature_subtitle_carries_cr_and_type(self):
        row = next(r for r in by_name(search_entities("goblin"), "Goblin")
                   if r["kind"] == "creature")
        assert "CR" in row["subtitle"]

    def test_finds_npc_by_name(self):
        rows = by_name(search_entities("Ayo Jabe"), "Ayo Jabe")
        assert rows and rows[0]["kind"] == "npc"

    def test_finds_trap_by_name(self):
        rows = [r for r in search_entities("electric shock") if r["kind"] == "trap"]
        assert rows, "trap text should be searchable"

    def test_finds_feat_from_data_py(self):
        rows = [r for r in search_entities("gunner") if r["kind"] == "feat"]
        assert rows, "data.py feats (e.g. Gunner) must be searchable"

    def test_finds_feat_from_manual_json(self):
        rows = by_name(search_entities("explorer"), "Explorer")
        assert rows and rows[0]["kind"] == "feat"

    def test_finds_race_by_name(self):
        rows = by_name(search_entities("dwarf"), "Dwarf")
        kinds = {r["kind"] for r in rows}
        assert "race" in kinds, f"playable race missing for 'dwarf': {kinds}"
        # 'Dwarf' is also a bestiary stat block — both belong in the results,
        # the race first (equal score, race ranks above creature).
        assert rows[0]["kind"] == "race"
        assert "creature" in kinds

    def test_finds_racial_trait_and_names_its_race(self):
        rows = [r for r in search_entities("dwarven resilience") if r["kind"] == "trait"]
        assert rows, "racial traits must be searchable"
        assert "Dwarf" in rows[0]["subtitle"]

    def test_finds_spell_with_level_school_and_classes(self):
        rows = by_name(search_entities("fireball"), "Fireball")
        assert rows and rows[0]["kind"] == "spell"
        assert "3rd" in rows[0]["subtitle"] or "3" in rows[0]["subtitle"]

    def test_finds_background(self):
        rows = by_name(search_entities("grinner"), "Grinner")
        assert rows and rows[0]["kind"] == "background"

    def test_finds_subclass(self):
        rows = by_name(search_entities("echo knight"), "Echo Knight")
        assert rows and rows[0]["kind"] == "subclass"

    def test_finds_class_feature(self):
        rows = [r for r in search_entities("action surge") if r["kind"] == "feature"]
        assert rows, "class features must be searchable"

    def test_description_only_hit_still_returns(self):
        rows = [r for r in search_entities("adelicate") if "adelicate" in r["snippet"].lower()]
        assert isinstance(rows, list)  # no crash on description-only words


class TestRankingAndLimits:
    def test_exact_name_match_ranks_first(self):
        rows = search_entities("goblin")
        assert rows[0]["name"].lower().startswith("goblin")

    def test_per_kind_cap_is_respected(self):
        rows = search_entities("dragon", per_kind=3)
        for kind in kinds_of(rows):
            n = len([r for r in rows if r["kind"] == kind])
            assert n <= 3, f"{kind} returned {n} rows, cap is 3"

    def test_total_limit_is_respected(self):
        assert len(search_entities("dragon", limit=5)) <= 5

    def test_no_match_returns_empty(self):
        assert search_entities("zzqqxx") == []

    def test_empty_query_returns_empty(self):
        for q in ("", "   ", "a"):
            assert search_entities(q) == []

    def test_duplicate_names_are_collapsed(self):
        rows = by_name(search_entities("cloak of protection"), "Cloak of Protection")
        assert len(rows) == 1, "ITEM_INDEX and magic_items.json must not double-list"


class TestSourceScoping:
    def test_slug_and_page_are_extracted_when_present(self):
        row = by_name(search_entities("cloak of protection"), "Cloak of Protection")[0]
        assert row["slug"] == "DMG"
        assert row["page"] == 159

    def test_filter_keeps_only_that_book(self):
        rows = search_entities("dragon", sources={"FGFD"})
        assert rows, "FGFD has dragon entries"
        for r in rows:
            if r.get("source"):
                assert r["slug"] == "FGFD", f"{r['name']} leaked from {r['source']}"

    def test_filter_excludes_entities_without_a_source(self):
        rows = search_entities("fireball", sources={"FGFD"})
        assert all(r["kind"] != "spell" or r["slug"] == "FGFD" for r in rows)

    def test_unfiltered_search_includes_srd_core(self):
        rows = by_name(search_entities("fireball"), "Fireball")
        assert rows, "SRD core spells must appear when no book filter is set"


class TestEntityDetail:
    def test_item_detail(self):
        d = entity_detail("item", "Cloak of Protection")
        assert d and d["kind"] == "item"
        assert d["name"] == "Cloak of Protection"
        assert "attunement" in d["body"].lower()
        assert any(s["label"] == "Rarity" for s in d["stats"])

    def test_creature_detail_has_stat_block_fields(self):
        d = entity_detail("creature", "Goblin")
        labels = {s["label"] for s in d["stats"]}
        assert {"AC", "HP", "CR"} <= labels, labels
        assert d["body"]

    def test_spell_detail(self):
        d = entity_detail("spell", "Fireball")
        labels = {s["label"] for s in d["stats"]}
        assert "Level" in labels and "Casting Time" in labels

    def test_race_detail_lists_traits(self):
        d = entity_detail("race", "Dwarf")
        assert "Dwarven Resilience" in d["body"]

    def test_trait_detail(self):
        d = entity_detail("trait", "Dwarven Resilience")
        assert d and d["kind"] == "trait"
        assert "poison" in d["body"].lower()

    def test_unknown_kind_returns_none(self):
        assert entity_detail("banana", "Goblin") is None

    def test_unknown_name_returns_none(self):
        assert entity_detail("creature", "Nonexistent Beast of Testing") is None


# ── endpoints ──────────────────────────────────────────────────────────────

class TestEntitiesEndpoint:
    URL = "/api/reference/entities"

    def test_anonymous_is_redirected_to_login(self, client):
        r = client.get(self.URL, params={"q": "fireball"}, follow_redirects=False)
        assert r.status_code == 303

    def test_returns_typed_results(self, client, auth_headers):
        r = client.get(self.URL, params={"q": "fireball"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["query"] == "fireball"
        assert body["results"], body
        assert {"kind", "name", "subtitle", "source", "slug", "page"} <= set(body["results"][0])
        assert body["counts"].get("spell")

    def test_kinds_meta_is_included_for_badges(self, client, auth_headers):
        body = client.get(self.URL, params={"q": "goblin"}, headers=auth_headers).json()
        assert body["kinds"]["creature"]["icon"]

    def test_source_filter_narrows_results(self, client, auth_headers):
        wide = client.get(self.URL, params={"q": "dragon"}, headers=auth_headers).json()
        narrow = client.get(self.URL, params={"q": "dragon", "source": "FGFD"},
                            headers=auth_headers).json()
        assert narrow["results"], "FGFD has dragon entries"
        assert all(r["slug"] == "FGFD" for r in narrow["results"])
        assert narrow["count"] <= wide["count"]

    def test_limit_is_clamped(self, client, auth_headers):
        body = client.get(self.URL, params={"q": "dragon", "limit": 4}, headers=auth_headers).json()
        assert body["count"] <= 4

    def test_short_query_returns_empty(self, client, auth_headers):
        body = client.get(self.URL, params={"q": "a"}, headers=auth_headers).json()
        assert body["results"] == []


class TestEntityDetailEndpoint:
    URL = "/api/reference/entity"

    def test_anonymous_is_redirected_to_login(self, client):
        r = client.get(self.URL, params={"kind": "spell", "name": "Fireball"},
                       follow_redirects=False)
        assert r.status_code == 303

    def test_spell_detail_payload(self, client, auth_headers):
        r = client.get(self.URL, params={"kind": "spell", "name": "Fireball"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Fireball"
        assert body["kind_label"] == "Spell"
        assert body["icon"]
        assert body["body"]
        assert {"label", "value"} <= set(body["stats"][0])

    def test_creature_detail_payload(self, client, auth_headers):
        r = client.get(self.URL, params={"kind": "creature", "name": "Goblin"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        labels = {s["label"] for s in r.json()["stats"]}
        assert {"AC", "HP", "CR"} <= labels

    def test_unknown_kind_is_400(self, client, auth_headers):
        r = client.get(self.URL, params={"kind": "banana", "name": "Goblin"}, headers=auth_headers)
        assert r.status_code == 400

    def test_unknown_name_is_404(self, client, auth_headers):
        r = client.get(self.URL, params={"kind": "creature", "name": "Nonexistent Beast"},
                       headers=auth_headers)
        assert r.status_code == 404


# ── nav wiring ─────────────────────────────────────────────────────────────

class TestNavSearchWiring:
    def test_layout_loads_the_script_with_cache_bust(self, client):
        html = client.get("/login").text
        assert "/static/entity-search.js?v=" in html, "nav script missing from every page"

    def test_nav_has_the_results_container(self, client):
        html = client.get("/login").text
        assert 'id="manualSearchResults"' in html and 'id="manualSearchInput"' in html

    def test_manual_search_folds_in_the_internal_panel(self, client):
        html = client.get("/login").text
        assert "EntitySearch.fetchFor" in html
        assert "EntitySearch.internalHtml" in html
        assert "EntitySearch.markManual" in html

    def test_script_debounces_and_hits_the_entities_endpoint(self):
        js = (Path(__file__).resolve().parent.parent / "static" / "entity-search.js").read_text()
        assert "/api/reference/entities" in js
        assert "/api/reference/entity?" in js
        assert "DEBOUNCE_MS = 250" in js
        assert "addEventListener('input'" in js
        assert "data-kind" in js

    def test_panel_css_is_injected_at_wire_time(self):
        """Regression: injectStyle() used to run only inside modalEl(), so a panel
        rendered without ever opening a modal had NO styles (name/subtitle glued)."""
        js = (Path(__file__).resolve().parent.parent / "static" / "entity-search.js").read_text()
        wire_body = js.split("function wire()", 1)[1].split("function ", 1)[0]
        assert "injectStyle();" in wire_body, "panel styles must be injected on wire()"
