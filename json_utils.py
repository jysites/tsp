#!/usr/bin/env python3
"""
Shared JSON persistence helpers for Sportsplex scrapers.

The output file is changed only when meaningful scraped data changes.
Writes are atomic so readers never receive a partially written JSON file.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def _comparable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Remove volatile metadata that should not cause a data-change commit.
    """
    comparable = deepcopy(payload)
    comparable.pop("generated_at", None)
    return comparable


def load_existing_json(path: str) -> dict[str, Any] | None:
    """
    Load an existing JSON object.

    Returns None when the file does not exist or is not valid JSON.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def write_json_if_changed(
    out_path: str,
    payload: dict[str, Any],
) -> bool:
    """
    Atomically write payload only when meaningful data changed.

    Returns True when the output file was replaced and False when the
    existing file already contained the same meaningful data.
    """
    existing = load_existing_json(out_path)

    if (
        existing is not None
        and _comparable_payload(existing) == _comparable_payload(payload)
    ):
        return False

    payload_to_write = deepcopy(payload)
    payload_to_write["generated_at"] = datetime.now(timezone.utc).isoformat()

    output_directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(output_directory, exist_ok=True)

    temporary_path = ""

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_directory,
            prefix=".sportsplex-",
            suffix=".json.tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name

            json.dump(
                payload_to_write,
                temporary_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, out_path)
        return True

    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
