"""Tests for cached JSON data access."""

import json

from services.data_loader import cache_info, clear_cache, load_json


def test_loader_reads_json_and_caches_result(tmp_path):
    (tmp_path / "items.json").write_text(json.dumps([{"name": "Torch"}]))
    clear_cache()
    first = load_json(str(tmp_path), "items.json")
    (tmp_path / "items.json").write_text(json.dumps([{"name": "Changed"}]))
    second = load_json(str(tmp_path), "items.json")
    assert first == [{"name": "Torch"}]
    assert second == first
    assert cache_info().hits >= 1


def test_loader_returns_empty_for_missing_or_invalid_json(tmp_path):
    clear_cache()
    assert load_json(str(tmp_path), "missing.json") == []
    (tmp_path / "bad.json").write_text("not json")
    assert load_json(str(tmp_path), "bad.json") == []
