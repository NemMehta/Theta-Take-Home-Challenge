"""Unit tests for dataset.flatten_ids (pure)."""

from taskbundle.dataset import flatten_ids


def test_nested_list():
    assert flatten_ids([["a::b"]]) == ["a::b"]


def test_json_encoded_string():
    assert flatten_ids('["a::b","c::d"]') == ["a::b", "c::d"]


def test_repr_string_single_quotes():
    # SWE-Bench Pro often double-wraps: a JSON list whose element is a repr string.
    assert flatten_ids(["['a::b']"]) == ["a::b"]


def test_bare_scalar_string_is_single_id():
    # flatten_ids treats a bare (non-bracketed) string as ONE id; it does not
    # whitespace-split (that fallback lived only in the discovery scripts).
    assert flatten_ids("a::b") == ["a::b"]
    assert flatten_ids("a::b c::d") == ["a::b c::d"]


def test_dedupe_preserves_first_seen_order():
    assert flatten_ids('["c::d","a::b","c::d","a::b"]') == ["c::d", "a::b"]
