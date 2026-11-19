"""Tests for the generate-then-verify layer.

The VLM itself is not exercised here — these cover the two pure pieces that decide everything the
verifier accepts: what the clause is asking for (expected_from_clause) and how close a render came
(score). A parser bug is what made the first version find no counts at all in "three-legged", so the
compound cases are the point of this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.topology_verify import expected_from_clause, score, _vlm_prompt, parse_counts  # noqa: E402


def test_compound_hyphen_counts():
    assert expected_from_clause("a three-legged stone elemental with three clawed stone arms") == {
        "legs": 3, "arms": 3}


def test_compound_legged_alone():
    assert expected_from_clause("a six-legged scorpion lower body") == {"legs": 6}


def test_plain_counts_and_eyes():
    assert expected_from_clause("three eyes and a huge gaping mouth on top of its head") == {
        "eyes": 3}


def test_digits_are_read():
    assert expected_from_clause("a spider with 8 legs and 2 heads") == {"legs": 8, "heads": 2}


def test_prose_without_counts_is_empty():
    assert expected_from_clause("a blind pale humanoid with a flat noseless face") == {}


def test_adjective_between_number_and_noun():
    assert expected_from_clause("four long muscular arms") == {"arms": 4}


def test_score_exact_match():
    assert score({"legs": 3, "arms": 3}, {"legs": 3, "arms": 3}) == 20


def test_score_off_by_one_still_beats_missing():
    close = score({"legs": 3, "arms": 2}, {"legs": 3, "arms": 3})
    missing = score({"legs": 3}, {"legs": 3, "arms": 3})
    assert close > missing


def test_score_penalises_unrequested_extras():
    # Nine legs on a drider is not a near miss.
    assert score({"legs": 9}, {"legs": 8}) < score({"legs": 8}, {"legs": 8})


def test_score_off_by_one_is_forgiven_once_not_per_part():
    # Two limbs short on a two-part Xorn is the prior reasserting itself, not a shy counter: the
    # second slip-on is charged the full miss so 2/2/3 stops passing a 3/3/3 gate.
    one_slip = score({"legs": 3, "arms": 2, "eyes": 3}, {"legs": 3, "arms": 3, "eyes": 3})
    two_slips = score({"legs": 2, "arms": 2, "eyes": 3}, {"legs": 3, "arms": 3, "eyes": 3})
    assert one_slip >= 8
    assert two_slips < 8


def test_vlm_prompt_is_line_format():
    p = _vlm_prompt({"legs": 8, "arms": 2}, ["eyes"])
    assert "legs: <number>" in p and "arms: <number>" in p and "eyes: <number>" in p
    # A literal placeholder letter gets copied verbatim by qwen3-vl ("legs: N"), which parses to
    # nothing and looks like a dead backend. The instruction must define the substitution.
    assert "replacing <number>" in p


def test_parse_counts_lines_variants():
    raw = "legs: 3\n**arms**: 2\neyes - 3"
    assert parse_counts(raw, ["legs", "arms", "eyes"]) == {"legs": 3, "arms": 2, "eyes": 3}


def test_parse_counts_json_blob():
    assert parse_counts('{"counts": {"legs": 8}}', ["legs"]) == {"legs": 8}


def test_parse_counts_empty_when_no_numbers():
    assert parse_counts("I cannot see the image.", ["legs"]) == {}
