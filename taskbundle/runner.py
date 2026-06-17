"""Shared pytest execution + result parsing (stdlib only).

The image's pytest is old (6.1.2) with no JSON-report plugin, so we run with the
built-in JUnit XML writer and parse /tmp/pytest_report.xml. Used by validate and
run.
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from taskbundle.container import ContainerHandle

XML_PATH = "/tmp/pytest_report.xml"
_INSTANCE_COMMIT_RE = re.compile(r"-([0-9a-f]{40})-v")


class RunnerError(RuntimeError):
    """Raised for unrecoverable staging/execution problems."""


def instance_commit_from_id(instance_id: str) -> str:
    """Extract the 40-hex instance commit embedded in a SWE-Bench Pro id."""
    m = _INSTANCE_COMMIT_RE.search(instance_id)
    if not m:
        raise RunnerError(
            f"could not derive instance commit from instance_id: {instance_id}"
        )
    return m.group(1)


def stage_tests(handle, repo_path, instance_commit, selected_test_files):
    """Restore each selected test file to its instance-commit version.

    Overwrites any solver tampering. Raises RunnerError on git failure.
    """
    for tf in selected_test_files:
        cmd = f"git -C {shlex.quote(repo_path)} checkout {shlex.quote(instance_commit)} -- {shlex.quote(tf)}"
        rc, out, err = handle.exec(cmd)
        if rc != 0:
            raise RunnerError(
                f"staging failed for {tf}: git checkout rc={rc}\n{err.strip()}"
            )


def _tail(text: str, n: int = 50) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def run_pytest(handle, repo_path, node_ids):
    """Run all node_ids in one pytest invocation; parse JUnit XML.

    Returns a dict:
      { "rc", "by_node": {node_id: outcome}, "raw_counts": {...},
        "stdout_tail", "stderr_tail" }
    or, if the XML is missing/empty, a dict additionally carrying "error".
    """
    quoted = " ".join(shlex.quote(n) for n in node_ids)
    # --forked runs each test in its own subprocess (pytest-forked, shipped in the
    # image). Required for correctness: these repos' tests share process-global
    # singletons (e.g. ansible's context.CLIARGS), so running them in one process
    # causes order-dependent pollution — confirmed on our instance, where the gold
    # state only scores 9/9 under --forked.
    cmd = (
        f"rm -f {XML_PATH}; python -m pytest {quoted} --forked "
        f"--junitxml={XML_PATH} -p no:cacheprovider -o cache_dir=/tmp/pytest_cache -rN"
    )
    rc, out, err = handle.exec(cmd, workdir=repo_path, timeout=900)

    cat_rc, xml_text, cat_err = handle.exec(f"cat {XML_PATH}")
    stdout_tail = _tail(out)
    stderr_tail = _tail(err)
    if cat_rc != 0 or not xml_text.strip():
        return {
            "rc": rc,
            "by_node": {n: "missing" for n in node_ids},
            "raw_counts": _count({n: "missing" for n in node_ids}),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "error": f"JUnit XML missing/empty at {XML_PATH} (cat rc={cat_rc})",
        }

    try:
        by_node = parse_junit_xml(xml_text, node_ids)
    except ET.ParseError as e:
        return {
            "rc": rc,
            "by_node": {n: "missing" for n in node_ids},
            "raw_counts": _count({n: "missing" for n in node_ids}),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "error": f"could not parse JUnit XML: {e}",
        }

    return {
        "rc": rc,
        "by_node": by_node,
        "raw_counts": _count(by_node),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }


def parse_junit_xml(xml_text: str, expected_node_ids: list) -> dict:
    """Map each expected node ID to an outcome (passed|failed|error|skipped|missing).

    Pure: parses JUnit XML and matches <testcase>s to node IDs by leaf name, using
    the classname to disambiguate Class::method IDs. Raises ET.ParseError on
    malformed XML (the caller decides how to surface it).
    """
    return _map_nodes(expected_node_ids, _parse_testcases(xml_text))


def _parse_testcases(xml_text: str):
    """Return list of (name, classname, outcome) from JUnit XML."""
    root = ET.fromstring(xml_text)
    cases = []
    for tc in root.iter("testcase"):
        outcome = "passed"
        for child in tc:
            tag = child.tag
            if tag == "failure":
                outcome = "failed"
            elif tag == "error":
                outcome = "error"
            elif tag == "skipped":
                outcome = "skipped"
        cases.append((tc.get("name", ""), tc.get("classname", ""), outcome))
    return cases


def _leaf(node_id: str) -> str:
    return node_id.split("::")[-1]


def _map_nodes(node_ids, testcases):
    """Map each expected node_id to a testcase outcome by leaf name, using the
    classname to disambiguate Class::method IDs. Unmatched -> 'missing'."""
    by_node = {}
    used = set()
    for node_id in node_ids:
        leaf = _leaf(node_id)
        # class segment (if any): the part before the leaf, after the file.
        segs = node_id.split("::")
        cls = segs[-2] if len(segs) >= 3 else None
        match = None
        for i, (name, classname, outcome) in enumerate(testcases):
            if i in used or name != leaf:
                continue
            if cls is not None and cls not in (classname or ""):
                continue
            match = (i, outcome)
            break
        if match is None:
            by_node[node_id] = "missing"
        else:
            used.add(match[0])
            by_node[node_id] = match[1]
    return by_node


def _count(by_node):
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "missing": 0}
    for outcome in by_node.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
