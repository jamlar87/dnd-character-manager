"""Regression: pdf.shared._get_item_description must work on every call.

The resolver used to be a function-local assigned only inside the "cache empty"
branch, so the FIRST lookup worked and every later one raised
``UnboundLocalError: cannot access local variable '_GET_ITEM_RESOLVER'``. The
route-level PDF tests never caught it because their seeded character carries no
(or one) equipment row — a real sheet with 2+ items 500'd in production.
"""

def test_item_description_survives_repeat_calls():
    from pdf import shared

    shared._ITEM_INDEX = None
    shared._GET_ITEM_RESOLVER = None

    first = shared._get_item_description("Longsword")
    second = shared._get_item_description("Longsword")
    third = shared._get_item_description("Definitely Not An Item")
    assert isinstance(first, str)
    assert first == second
    assert third == ""


def test_item_description_with_preset_index(monkeypatch):
    from pdf import shared

    shared._ITEM_INDEX = {}
    shared._GET_ITEM_RESOLVER = None
    try:
        assert isinstance(shared._get_item_description("Longsword"), str)
        assert callable(shared._GET_ITEM_RESOLVER)
    finally:
        shared._ITEM_INDEX = None
        shared._GET_ITEM_RESOLVER = None
