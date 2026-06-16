"""Build a task bundle from a SWE-Bench Pro dataset row.

A bundle is a self-contained task directory:
    task.json, description.md, gold_patch.diff, test_patch.diff,
    tests/fail_to_pass.json, tests/pass_to_pass.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskbundle.dataset import flatten_ids

SCHEMA_VERSION = 1
IMAGE_REPO = "jefzda/sweap-images"


def sanitize_instance_id(instance_id: str) -> str:
    """Make an instance_id safe for use as a single path component."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", instance_id)


def default_bundle_dir(instance_id: str) -> Path:
    return Path("bundles") / sanitize_instance_id(instance_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_description(row: dict[str, Any]) -> str:
    problem = (row.get("problem_statement") or "").strip()
    requirements = (row.get("requirements") or "").strip()
    interface = (row.get("interface") or "").strip()
    parts = [problem]
    if requirements:
        parts.append("## Requirements\n\n" + requirements)
    if interface:
        parts.append("## Interface\n\n" + interface)
    return "\n\n".join(parts).rstrip() + "\n"


def build_bundle(item: dict[str, Any], bundle_dir: Path) -> dict[str, Any]:
    """Write a bundle from a datasets-server row item.

    `item` is the full row item: {"row": {...}, "truncated_cells": [...]}.
    Returns a summary dict (paths, counts, image, truncated flag/details).

    Raises ValueError if the patch or test_patch cell is truncated.
    """
    truncated = item.get("truncated_cells") or []
    bad = [c for c in ("patch", "test_patch") if c in truncated]
    if bad:
        raise ValueError(
            f"row has truncated patch cell(s): {bad}; bundle would be unusable"
        )

    row = item["row"]
    instance_id = row["instance_id"]
    repo = row["repo"]
    language = (row.get("repo_language") or "").strip().lower()

    f2p = flatten_ids(row.get("fail_to_pass"))
    p2p = flatten_ids(row.get("pass_to_pass"))
    test_files = flatten_ids(row.get("selected_test_files_to_run"))

    full_tag = row["dockerhub_tag"]
    image = f"{IMAGE_REPO}:{full_tag}"

    task = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "source": "swe-bench-pro",
        "repo": repo,
        "repo_url": f"https://github.com/{repo}",
        "base_commit": row["base_commit"],
        "language": language,
        "image": image,
        "image_digest": None,
        "repo_path_in_container": None,
        "before_repo_set_cmd": row.get("before_repo_set_cmd"),
        "test": {"runner": "pytest", "selected_test_files": test_files},
        "counts": {"fail_to_pass": len(f2p), "pass_to_pass": len(p2p)},
        "created_at": _now_iso(),
    }

    bundle_dir = Path(bundle_dir)
    tests_dir = bundle_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    (bundle_dir / "task.json").write_text(
        json.dumps(task, indent=2) + "\n", encoding="utf-8"
    )
    (bundle_dir / "description.md").write_text(
        _build_description(row), encoding="utf-8"
    )
    (bundle_dir / "gold_patch.diff").write_text(
        row.get("patch") or "", encoding="utf-8"
    )
    (bundle_dir / "test_patch.diff").write_text(
        row.get("test_patch") or "", encoding="utf-8"
    )
    (tests_dir / "fail_to_pass.json").write_text(
        json.dumps(f2p, indent=2) + "\n", encoding="utf-8"
    )
    (tests_dir / "pass_to_pass.json").write_text(
        json.dumps(p2p, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "bundle_dir": str(bundle_dir),
        "image": image,
        "instance_id": instance_id,
        "n_f2p": len(f2p),
        "n_p2p": len(p2p),
        "n_test_files": len(test_files),
    }
