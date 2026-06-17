"""Unit tests for the solver's pure parsing/guardrail helpers."""

from taskbundle.solver import (
    _dominant_model,
    is_disallowed_solver_path,
    parse_file_blocks,
    strip_code_fence,
)


def test_strip_code_fence_fenced_and_unfenced():
    assert strip_code_fence("```python\nx = 1\ny = 2\n```") == "x = 1\ny = 2"
    assert strip_code_fence("x = 1\ny = 2") == "x = 1\ny = 2"


def test_parse_file_blocks_multifile_with_stray_fence():
    text = (
        "=== BEGIN FILE: a.py ===\n"
        "print(1)\n"
        "=== END FILE: a.py ===\n"
        "=== BEGIN FILE: pkg/b.py ===\n"
        "```python\n"
        "print(2)\n"
        "```\n"
        "=== END FILE: pkg/b.py ===\n"
    )
    files = parse_file_blocks(text)
    assert files == {"a.py": "print(1)", "pkg/b.py": "print(2)"}


def test_dominant_model_prefers_high_output_tokens():
    data = {
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"outputTokens": 20},
            "claude-sonnet-4-6": {"outputTokens": 7000},
        }
    }
    assert _dominant_model(data) == "claude-sonnet-4-6"


def test_dominant_model_prefers_top_level_field():
    data = {"model": "claude-opus-4-8", "modelUsage": {"x": {"outputTokens": 9}}}
    assert _dominant_model(data) == "claude-opus-4-8"


def test_path_guardrail_accepts_source():
    assert is_disallowed_solver_path("lib/ansible/cli/adhoc.py", []) is False


def test_path_guardrail_rejects_escapes_and_tests():
    selected = ["test/units/cli/test_adhoc.py"]
    for bad in ("../x.py", "/abs/x.py", "test/x.py", "tests/x.py",
                "test_x.py", "x_test.py", "conftest.py",
                "test/units/cli/test_adhoc.py"):
        assert is_disallowed_solver_path(bad, selected) is True
