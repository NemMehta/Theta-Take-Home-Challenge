"""Unit tests for JUnit-XML -> per-node outcome mapping (pure)."""

from taskbundle.runner import parse_junit_xml

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="5">
  <testcase classname="test.units.cli.test_adhoc" name="test_pass"/>
  <testcase classname="test.units.cli.test_adhoc" name="test_fail"><failure>boom</failure></testcase>
  <testcase classname="test.units.cli.test_adhoc" name="test_err"><error>kaboom</error></testcase>
  <testcase classname="test.units.cli.test_adhoc" name="test_skip"><skipped/></testcase>
  <testcase classname="test.units.parsing.TestDumper" name="test_method"/>
</testsuite>
"""

F = "test/units/cli/test_adhoc.py"


def test_outcomes_map_passed_failed_error_skipped_and_missing():
    expected = [
        f"{F}::test_pass", f"{F}::test_fail", f"{F}::test_err",
        f"{F}::test_skip", f"{F}::test_absent",
    ]
    by_node = parse_junit_xml(XML, expected)
    assert by_node[f"{F}::test_pass"] == "passed"
    assert by_node[f"{F}::test_fail"] == "failed"
    assert by_node[f"{F}::test_err"] == "error"
    assert by_node[f"{F}::test_skip"] == "skipped"
    assert by_node[f"{F}::test_absent"] == "missing"


def test_class_method_disambiguation_via_classname():
    nid = "test/units/parsing/test_dumper.py::TestDumper::test_method"
    by_node = parse_junit_xml(XML, [nid])
    assert by_node[nid] == "passed"


def test_class_method_wrong_class_is_missing():
    # leaf name matches a testcase, but the class segment does not appear in any
    # <testcase classname> -> no match -> missing.
    nid = "test/units/parsing/test_dumper.py::OtherClass::test_method"
    by_node = parse_junit_xml(XML, [nid])
    assert by_node[nid] == "missing"


def test_plain_function_leaf_matching():
    by_node = parse_junit_xml(XML, [f"{F}::test_pass"])
    assert by_node[f"{F}::test_pass"] == "passed"
