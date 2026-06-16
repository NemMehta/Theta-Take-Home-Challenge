"""Pluggable test-hiding "masker" (file-level baseline).

The masker edits the SOLVE working tree so the solver cannot see the scored
tests. Scoring re-stages the real tests afterwards, so masking never affects the
score. File-level masking deletes whole selected test files; the function-level
masker (Phase 4) will remove only the scored functions.
"""

from __future__ import annotations

import ast
import shlex
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class MaskResult:
    strategy: str
    masked_files: list[str] = field(default_factory=list)
    total_tests_hidden: int = 0
    scored_hidden: int = 0
    non_scored_hidden: int = 0
    non_scored_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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


class FileLevelMasker:
    """Delete each selected test file from the working tree (hides everything
    in it). Counts scored vs non-scored tests first for reporting."""

    strategy = "file"

    def mask(self, handle, repo_path, selected_test_files, scored_node_ids) -> MaskResult:
        result = MaskResult(strategy=self.strategy)
        # Index scored leaf/segment forms for matching against ast names.
        scored_leaves = {nid.split("::", 1)[-1] for nid in scored_node_ids}
        for tf in selected_test_files:
            rc, source, err = handle.exec(f"cat {shlex.quote(repo_path)}/{shlex.quote(tf)}")
            if rc != 0:
                # File missing from the tree; nothing to hide for it.
                continue
            try:
                names = _collect_tests(source)
            except SyntaxError:
                names = []
            for name in names:
                result.total_tests_hidden += 1
                # name is 'test_x' or 'Class::test_x'; match the post-:: form.
                leaf = name.split("::")[-1]
                node_form = name  # 'Class::test_x' or 'test_x'
                is_scored = (
                    node_form in scored_leaves
                    or leaf in scored_leaves
                    or f"{tf}::{name}" in scored_node_ids
                )
                if is_scored:
                    result.scored_hidden += 1
                else:
                    result.non_scored_hidden += 1
                    result.non_scored_names.append(name)
            # Working-tree deletion only (NOT git rm) so capture stays clean.
            handle.exec(f"rm -f {shlex.quote(repo_path)}/{shlex.quote(tf)}", check=False)
            result.masked_files.append(tf)
        return result


def get_masker(strategy: str):
    if strategy == "file":
        return FileLevelMasker()
    if strategy == "function":
        raise NotImplementedError("function-level masker arrives in Phase 4")
    raise ValueError(f"unknown mask strategy: {strategy}")
