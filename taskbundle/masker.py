"""Pluggable test-hiding "masker".

The masker edits the SOLVE working tree so the solver cannot see the scored
tests. Scoring re-stages the real tests afterwards, so masking never affects the
score. Two strategies:
- file-level: delete each selected test file (over-hides unrelated tests).
- function-level (Python, default): parse with `ast` and remove ONLY the scored
  test functions/methods, preserving every other test. Falls back to file-level
  delete per-file if anything is unsafe (never leaves a scored test visible).
"""

from __future__ import annotations

import ast
import os
import shlex
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class MaskResult:
    strategy: str
    masked_files: list[str] = field(default_factory=list)
    total_tests_hidden: int = 0
    scored_hidden: int = 0
    non_scored_hidden: int = 0
    non_scored_names: list[str] = field(default_factory=list)
    removed_tests: list[str] = field(default_factory=list)
    preserved_tests: list[str] = field(default_factory=list)
    per_file: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_node_id(node_id: str) -> tuple[str, Optional[str], str]:
    """Split a pytest node ID into (file_path, class_name|None, func_name).

    Strips any parametrization suffix '[...]' from the leaf.
    """
    parts = node_id.split("::")
    file_path = parts[0]
    leaf = parts[-1].split("[", 1)[0]
    class_name = parts[-2] if len(parts) >= 3 else None
    return file_path, class_name, leaf


def _collect_tests(source: str) -> list[str]:
    """Return test node names in a file: 'test_x' or 'Class::test_x'."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test_"):
                    names.append(f"{node.name}::{sub.name}")
    return names


def _classify(names: list[str], tf: str, scored_node_ids) -> tuple[list[str], list[str]]:
    """Split collected test names into (scored, non_scored) for file `tf`."""
    scored_leaves = {parse_node_id(n)[2] for n in scored_node_ids if parse_node_id(n)[0] == tf}
    scored, non_scored = [], []
    for name in names:
        leaf = name.split("::")[-1]
        (scored if leaf in scored_leaves else non_scored).append(name)
    return scored, non_scored


def _is_test_def(node) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")


def _span(node) -> tuple[int, int]:
    """1-indexed inclusive line span of a def, including decorators."""
    start = node.lineno
    if node.decorator_list:
        start = min(start, min(d.lineno for d in node.decorator_list))
    return start, node.end_lineno


class FileLevelMasker:
    """Delete each selected test file from the working tree (hides everything)."""

    strategy = "file"

    def mask(self, handle, repo_path, selected_test_files, scored_node_ids) -> MaskResult:
        result = MaskResult(strategy=self.strategy)
        for tf in selected_test_files:
            rc, source, _ = handle.exec(f"cat {shlex.quote(repo_path)}/{shlex.quote(tf)}")
            if rc != 0:
                continue
            _apply_file_delete(handle, repo_path, tf, source, scored_node_ids, result,
                               strategy_used="file", fallback_reason=None)
        return result


def _apply_file_delete(handle, repo_path, tf, source, scored_node_ids, result,
                       strategy_used, fallback_reason) -> None:
    """Delete `tf` from the working tree and update `result` accounting."""
    try:
        names = _collect_tests(source)
    except SyntaxError:
        names = []
    scored, non_scored = _classify(names, tf, scored_node_ids)
    result.total_tests_hidden += len(scored) + len(non_scored)
    result.scored_hidden += len(scored)
    result.non_scored_hidden += len(non_scored)
    result.non_scored_names.extend(n.split("::")[-1] for n in non_scored)
    result.removed_tests.extend(n.split("::")[-1] for n in scored)
    handle.exec(f"rm -f {shlex.quote(repo_path)}/{shlex.quote(tf)}", check=False)
    result.masked_files.append(tf)
    result.per_file.append({
        "file": tf, "strategy_used": strategy_used,
        "removed": [n.split("::")[-1] for n in scored],
        "preserved": [],
        "fallback_reason": fallback_reason,
    })


@dataclass
class PyMaskResult:
    """Outcome of the pure AST mask transform for a single Python source string."""
    ok: bool
    new_source: Optional[str] = None
    removed: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    fallback_reason: Optional[str] = None


def mask_python_source(source: str, scored_targets: set) -> PyMaskResult:
    """Remove the scored test funcs/methods from `source` (pure; no I/O).

    `scored_targets` is a set of (class_name|None, func_name). Returns a
    PyMaskResult: on success carries the rewritten source plus removed/preserved
    leaf names; on any unsafe condition returns ok=False with a fallback reason
    (parse failure, an unmatched scored target, or an edit that would not re-parse)
    so the caller can fall back to a file-level delete (never under-hides).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return PyMaskResult(ok=False, fallback_reason=f"parse failed: {e}")

    edits: list[tuple[int, int, list[str]]] = []  # (start, end, replacement)
    matched: set[tuple[Optional[str], str]] = set()
    removed_leaves: list[str] = []

    for node in tree.body:
        if _is_test_def(node) and (None, node.name) in scored_targets:
            s, e = _span(node)
            edits.append((s, e, []))
            matched.add((None, node.name))
            removed_leaves.append(node.name)
        elif isinstance(node, ast.ClassDef):
            removed_methods = []
            for sub in node.body:
                if _is_test_def(sub) and (node.name, sub.name) in scored_targets:
                    removed_methods.append(sub)
                    matched.add((node.name, sub.name))
                    removed_leaves.append(sub.name)
            if not removed_methods:
                continue
            remaining = [st for st in node.body if st not in removed_methods]
            # If removing empties the class body, replace the first removed
            # method's span with a `pass` at its indent.
            pass_idx = None
            if not remaining:
                first = min(removed_methods, key=lambda m: m.lineno)
                pass_idx = id(first)
            for m in removed_methods:
                s, e = _span(m)
                repl = [" " * m.col_offset + "pass\n"] if id(m) == pass_idx else []
                edits.append((s, e, repl))

    # SAFETY: every scored target must have matched.
    unmatched = scored_targets - matched
    if unmatched:
        return PyMaskResult(
            ok=False, fallback_reason=f"unmatched scored test(s): {sorted(unmatched)}")

    # Apply edits to source lines, descending by start.
    lines = source.splitlines(keepends=True)
    for start, end, repl in sorted(edits, key=lambda x: x[0], reverse=True):
        lines[start - 1:end] = repl
    new_content = "".join(lines)

    try:
        ast.parse(new_content)
    except SyntaxError as e:
        return PyMaskResult(ok=False, fallback_reason=f"post-edit parse failed: {e}")

    preserved_leaves = [p.split("::")[-1] for p in _collect_tests(new_content)]
    return PyMaskResult(ok=True, new_source=new_content,
                        removed=removed_leaves, preserved=preserved_leaves)


class FunctionLevelMasker:
    """Remove only the scored test functions/methods via AST surgery; preserve
    all other tests. Falls back to file-level delete per-file when unsafe."""

    strategy = "function"

    def mask(self, handle, repo_path, selected_test_files, scored_node_ids) -> MaskResult:
        result = MaskResult(strategy=self.strategy)
        for tf in selected_test_files:
            # Scored targets for THIS file: {(class_name|None, func_name)}.
            targets = {
                (parse_node_id(n)[1], parse_node_id(n)[2])
                for n in scored_node_ids if parse_node_id(n)[0] == tf
            }
            if not targets:
                continue

            rc, source, _ = handle.exec(f"cat {shlex.quote(repo_path)}/{shlex.quote(tf)}")
            if rc != 0:
                _apply_file_delete(handle, repo_path, tf, "", scored_node_ids, result,
                                   "file-fallback", "cat failed")
                continue

            res = mask_python_source(source, targets)
            if not res.ok:
                _apply_file_delete(handle, repo_path, tf, source, scored_node_ids, result,
                                   "file-fallback", res.fallback_reason)
                continue

            # Write the edited file back into the container working tree.
            _write_back(handle, repo_path, tf, res.new_source)
            result.masked_files.append(tf)
            result.total_tests_hidden += len(res.removed)
            result.scored_hidden += len(res.removed)
            result.removed_tests.extend(res.removed)
            result.preserved_tests.extend(res.preserved)
            result.per_file.append({
                "file": tf, "strategy_used": "function",
                "removed": res.removed, "preserved": res.preserved,
                "fallback_reason": None,
            })
        return result


def _write_back(handle, repo_path, tf, content) -> None:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    try:
        tmp.write(content)
        tmp.close()
        handle.cp_to(tmp.name, f"{repo_path}/{tf}")
    finally:
        os.unlink(tmp.name)


def get_masker(strategy: str):
    if strategy == "file":
        return FileLevelMasker()
    if strategy == "function":
        return FunctionLevelMasker()
    raise ValueError(f"unknown mask strategy: {strategy}")
