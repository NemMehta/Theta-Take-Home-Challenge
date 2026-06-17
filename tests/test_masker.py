"""Unit tests for the pure AST mask transform (no Docker / network)."""

import ast

import taskbundle.masker as masker_mod
from taskbundle.masker import mask_python_source, parse_node_id

# 3 module-level test fns + a class with 2 test methods + a non-test helper.
FIXTURE = '''\
def helper():
    return 1


def test_alpha():
    assert helper()


def test_beta():
    assert True


def test_gamma():
    assert True


class TestStuff:
    def test_one(self):
        assert True

    def test_two(self):
        assert True
'''


def _parses(src):
    ast.parse(src)
    return True


def test_remove_subset_keeps_rest_class_and_helper():
    res = mask_python_source(FIXTURE, {(None, "test_alpha")})
    assert res.ok
    assert res.removed == ["test_alpha"]
    assert _parses(res.new_source)
    assert "def test_alpha(" not in res.new_source
    # everything else survives
    for kept in ("def helper(", "def test_beta(", "def test_gamma(",
                 "class TestStuff", "def test_one(", "def test_two("):
        assert kept in res.new_source
    assert set(res.preserved) == {"test_beta", "test_gamma", "test_one", "test_two"}


def test_empty_class_body_gets_pass():
    res = mask_python_source(FIXTURE, {("TestStuff", "test_one"), ("TestStuff", "test_two")})
    assert res.ok
    assert sorted(res.removed) == ["test_one", "test_two"]
    assert _parses(res.new_source)
    # class remains, now with a `pass` body
    tree = ast.parse(res.new_source)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestStuff"]
    assert cls and isinstance(cls[0].body[0], ast.Pass)


def test_parametrized_node_id_maps_to_function():
    # parse_node_id strips the [..] param suffix down to the function name
    fp, cls, func = parse_node_id("test_x.py::test_beta[case-1]")
    assert (fp, cls, func) == ("test_x.py", None, "test_beta")
    res = mask_python_source(FIXTURE, {(cls, func)})
    assert res.ok and res.removed == ["test_beta"]
    assert "def test_beta(" not in res.new_source


def test_decorated_function_removed_with_decorator():
    src = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('x', [1, 2])\n"
        "def test_dec(x):\n"
        "    assert x\n\n"
        "def test_plain():\n"
        "    assert True\n"
    )
    res = mask_python_source(src, {(None, "test_dec")})
    assert res.ok
    assert "@pytest.mark.parametrize" not in res.new_source
    assert "def test_dec(" not in res.new_source
    assert "def test_plain(" in res.new_source
    assert _parses(res.new_source)


def test_real_world_preserves_only_test_ansible_version():
    scored = [
        "test_parse", "test_with_command", "test_simple_command", "test_no_argument",
        "test_did_you_mean_playbook", "test_play_ds_positive",
        "test_play_ds_with_include_role", "test_run_import_playbook",
        "test_run_no_extra_vars",
    ]
    names = scored + ["test_ansible_version"]
    src = "\n\n".join(f"def {n}():\n    assert True" for n in names) + "\n"
    res = mask_python_source(src, {(None, n) for n in scored})
    assert res.ok
    assert sorted(res.removed) == sorted(scored)
    assert res.preserved == ["test_ansible_version"]
    assert "def test_ansible_version(" in res.new_source
    for n in scored:
        assert f"def {n}(" not in res.new_source


def test_fallback_on_unmatched_target():
    res = mask_python_source(FIXTURE, {(None, "test_not_here")})
    assert not res.ok
    assert res.fallback_reason.startswith("unmatched scored test(s)")


def test_fallback_on_unparseable_source():
    res = mask_python_source("def broken(:\n    pass\n", {(None, "test_x")})
    assert not res.ok
    assert res.fallback_reason.startswith("parse failed")


def test_fallback_on_post_edit_invalid_python(monkeypatch):
    # Force the post-edit re-parse to fail (the defensive net): first ast.parse
    # (of the original) succeeds, the second (of the edit) raises.
    real_parse = ast.parse
    calls = {"n": 0}

    def flaky(src, *a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SyntaxError("simulated invalid edit")
        return real_parse(src, *a, **k)

    monkeypatch.setattr(masker_mod.ast, "parse", flaky)
    res = mask_python_source(FIXTURE, {(None, "test_alpha")})
    assert not res.ok
    assert res.fallback_reason.startswith("post-edit parse failed")
