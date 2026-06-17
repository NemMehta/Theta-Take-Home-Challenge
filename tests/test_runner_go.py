"""Unit tests for the `go test -json` stream parser (pure, no Go needed)."""

import json

from taskbundle.runner import parse_go_test_json


def _stream(events):
    return "\n".join(json.dumps(e) for e in events)


def test_pass_fail_skip_outcomes():
    stream = _stream([
        {"Action": "run", "Package": "p", "Test": "TestPass"},
        {"Action": "pass", "Package": "p", "Test": "TestPass"},
        {"Action": "run", "Package": "p", "Test": "TestFail"},
        {"Action": "fail", "Package": "p", "Test": "TestFail"},
        {"Action": "skip", "Package": "p", "Test": "TestSkip"},
        {"Action": "pass", "Package": "p"},  # package-level (no Test) — ignored
    ])
    out = parse_go_test_json(stream, ["TestPass", "TestFail", "TestSkip"])
    assert out == {"TestPass": "passed", "TestFail": "failed", "TestSkip": "skipped"}


def test_subtest_events_do_not_override_parent():
    stream = _stream([
        {"Action": "run", "Package": "p", "Test": "TestParent"},
        {"Action": "fail", "Package": "p", "Test": "TestParent/case_a"},  # subtest -> ignored
        {"Action": "pass", "Package": "p", "Test": "TestParent"},
    ])
    assert parse_go_test_json(stream, ["TestParent"]) == {"TestParent": "passed"}


def test_absent_name_is_missing_when_stream_has_events():
    stream = _stream([
        {"Action": "run", "Package": "p", "Test": "TestPresent"},
        {"Action": "pass", "Package": "p", "Test": "TestPresent"},
    ])
    out = parse_go_test_json(stream, ["TestPresent", "TestGone"])
    assert out == {"TestPresent": "passed", "TestGone": "missing"}


def test_compile_failure_no_test_events_marks_all_error():
    stream = _stream([
        {"Action": "output", "Package": "p", "Output": "# p\n"},
        {"Action": "output", "Package": "p", "Output": "FAIL\tp [build failed]\n"},
        {"Action": "fail", "Package": "p", "Elapsed": 0},
    ])
    out = parse_go_test_json(stream, ["TestA", "TestB"])
    assert out == {"TestA": "error", "TestB": "error"}


def test_empty_stream_marks_all_error():
    assert parse_go_test_json("", ["TestX"]) == {"TestX": "error"}
