"""Hugging Face datasets-server access for SWE-Bench Pro (stdlib only).

Used by `task init --from-dataset` to fetch a single instance row and to
normalize its list-valued fields.
"""

from __future__ import annotations

import ast
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

DATASET = "ScaleAI/SWE-bench_Pro"
SPLITS_URL = "https://datasets-server.huggingface.co/splits"
ROWS_URL = "https://datasets-server.huggingface.co/rows"
_UA = {"User-Agent": "taskbundle/0.1"}
_PAGE = 100


class DatasetError(RuntimeError):
    """Raised for unrecoverable dataset-access problems."""


def _get_json(url: str, tries: int = 2) -> Any:
    last: Optional[Exception] = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0)
    raise DatasetError(f"GET failed after {tries} tries: {url} ({last})")


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def get_config_split(dataset: str = DATASET) -> tuple[str, str]:
    """Return (config, split) for the dataset's first split."""
    data = _get_json(f"{SPLITS_URL}?dataset={_q(dataset)}")
    splits = data.get("splits") or []
    if not splits:
        raise DatasetError(f"No splits reported for {dataset}")
    s = splits[0]
    return s["config"], s["split"]


def find_row(
    instance_id: str,
    dataset: str = DATASET,
    config: Optional[str] = None,
    split: Optional[str] = None,
) -> dict[str, Any]:
    """Paginate /rows until the row with this instance_id is found.

    Returns the full row item ({"row": {...}, "truncated_cells": [...]}).
    Raises DatasetError if not found.
    """
    if config is None or split is None:
        config, split = get_config_split(dataset)
    offset = 0
    total: Optional[int] = None
    while total is None or offset < total:
        url = (
            f"{ROWS_URL}?dataset={_q(dataset)}&config={_q(config)}&split={_q(split)}"
            f"&offset={offset}&length={_PAGE}"
        )
        data = _get_json(url)
        if total is None:
            total = data.get("num_rows_total")
        for item in data.get("rows", []):
            if item.get("row", {}).get("instance_id") == instance_id:
                return item
        offset += _PAGE
        time.sleep(0.2)
    raise DatasetError(
        f"instance_id not found in {dataset} ({config}/{split}): {instance_id}"
    )


def flatten_ids(value: Any) -> list[str]:
    """Normalize a possibly JSON-encoded / nested list field to a flat, deduped
    list of stripped strings, preserving first-seen order.

    Handles values like '["a::b"]', '[["a::b"]]', and repr-strings "['a::b']".
    """
    out: list[str] = []
    _flatten(value, out)
    seen: set[str] = set()
    result: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


def _flatten(x: Any, out: list[str]) -> None:
    if isinstance(x, (list, tuple)):
        for e in x:
            _flatten(e, out)
        return
    if x is None:
        return
    s = str(x).strip()
    if not s:
        return
    if s[0] in "[(":
        for parser in (json.loads, ast.literal_eval):
            try:
                val = parser(s)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(val, (list, tuple)):
                _flatten(val, out)
                return
        # looked like a list but unparseable: fall through and keep raw
    out.append(s)
