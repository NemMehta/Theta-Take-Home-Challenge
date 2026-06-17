"""Shared pytest execution + result parsing (stdlib only).

The image's pytest is old (6.1.2) with no JSON-report plugin, so we run with the
built-in JUnit XML writer and parse /tmp/pytest_report.xml. Used by validate and
run.
"""

from __future__ import annotations

import json
import re
import shlex
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from taskbundle.container import ContainerHandle

XML_PATH = "/tmp/pytest_report.xml"
# The instance commit is a 40-hex SHA embedded in the instance_id, either as
# `-<sha>-v<digest>` (Python/ansible ids) or `-<sha>` at the end (Go ids).
_INSTANCE_COMMIT_RE = re.compile(r"-([0-9a-f]{40})(?:-v|$)")


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


# ---------------------------------------------------------------------------
# Go runner (`go test -json`)
# ---------------------------------------------------------------------------

_GO_ACTION = {"pass": "passed", "fail": "failed", "skip": "skipped"}


def parse_go_test_json(stream_text: str, expected_test_names: list) -> dict:
    """Map each expected Go test name to an outcome from a `go test -json` stream.

    Pure (mirrors parse_junit_xml). Each line of the stream is a JSON event; we
    track the terminal Action per TOP-LEVEL test (subtest events, whose Test name
    contains "/", are ignored for the parent's outcome). Rules:
      - a name with a terminal pass/fail/skip -> passed/failed/skipped
      - a name absent from a stream that DID have test events -> "missing"
      - a stream with NO per-test events at all (e.g. a build/compile failure)
        -> ALL expected names "error".
    """
    outcome: dict[str, str] = {}
    saw_test_event = False
    for line in stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        test = ev.get("Test")
        if not test or "/" in test:
            continue  # package-level or subtest event
        saw_test_event = True
        action = ev.get("Action")
        if action in _GO_ACTION:
            outcome[test] = _GO_ACTION[action]
    if not saw_test_event:
        return {name: "error" for name in expected_test_names}
    return {name: outcome.get(name, "missing") for name in expected_test_names}


def _go_prefix(handle, repo_path):
    """Return (shell prefix to put `go` on PATH, human description)."""
    rc, out, _ = handle.exec("command -v go", workdir=repo_path)
    if rc == 0 and out.strip():
        return "", f"go on PATH ({out.strip()})"
    rc2, _, _ = handle.exec("test -x /usr/local/go/bin/go")
    if rc2 == 0:
        return "export PATH=/usr/local/go/bin:$PATH; ", "go via /usr/local/go/bin (PATH prepended)"
    return "", "go not found"


def run_go(handle, repo_path, test_config, scored_ids):
    """Run the scored Go tests via `go test -json` per package; parse the stream."""
    names = list(scored_ids)
    packages = test_config.get("packages") or ["./..."]
    prefix, how = _go_prefix(handle, repo_path)
    pattern = "^(" + "|".join(re.escape(n) for n in names) + ")$"
    stdout_all, stderr_all, rcs = [], [], []
    for pkg in packages:
        cmd = (f"{prefix}go test -json -run {shlex.quote(pattern)} "
               f"-count=1 {shlex.quote(pkg)}")
        rc, out, err = handle.exec(cmd, workdir=repo_path, timeout=1200)
        stdout_all.append(out)
        stderr_all.append(err)
        rcs.append(rc)
    stream = "\n".join(stdout_all)
    by_node = parse_go_test_json(stream, names)
    result = {
        "rc": rcs[-1] if rcs else 0,
        "by_node": by_node,
        "raw_counts": _count(by_node),
        "stdout_tail": _tail(stream),
        "stderr_tail": _tail("\n".join(stderr_all)),
        "go_invocation": how,
    }
    if not stream.strip():
        result["error"] = (f"go test produced no JSON output (rc={rcs}); "
                           f"go: {how}\n{result['stderr_tail']}")
    return result


# ---------------------------------------------------------------------------
# Runner registry — validate/run dispatch on task.json["test"]["runner"].
# ---------------------------------------------------------------------------

class PytestRunner:
    name = "pytest"

    def stage(self, handle, repo_path, instance_commit, test_config):
        stage_tests(handle, repo_path, instance_commit,
                    test_config.get("selected_test_files", []))

    def run(self, handle, repo_path, instance_commit, test_config, scored_ids):
        return run_pytest(handle, repo_path, scored_ids)


class GoRunner:
    name = "go"

    def stage(self, handle, repo_path, instance_commit, test_config):
        stage_tests(handle, repo_path, instance_commit,
                    test_config.get("test_files", []))

    def run(self, handle, repo_path, instance_commit, test_config, scored_ids):
        return run_go(handle, repo_path, test_config, scored_ids)


def get_runner(name: str):
    if name == "pytest":
        return PytestRunner()
    if name == "go":
        return GoRunner()
    raise RunnerError(f"unknown test runner: {name}")
