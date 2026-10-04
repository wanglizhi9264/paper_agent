"""Collect generation context and citations for answerable standard-v2 dev rows only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eval.pdf_v2_release import LiveAPIAdapter, validate_resolved_dataset


def _dev_answerable(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("dataset")
    if not isinstance(values, list):
        raise ValueError("Dataset must contain a dataset list")
    items = [
        dict(value)
        for value in values
        if isinstance(value, dict)
        and value.get("split") == "dev"
        and value.get("answerable") is True
    ]
    if len(items) != 37:
        raise ValueError(f"Expected exactly 37 answerable dev rows, got {len(items)}")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-live-api", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_api:
        parser.error("Live dev generation requires --allow-live-api")
    if args.output.exists():
        parser.error("Output exists; refusing to overwrite a prior dev run")

    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    validate_resolved_dataset(payload)
    items = _dev_answerable(payload)
    adapter = LiveAPIAdapter(args.api_base, args.timeout)
    predictions: list[dict[str, Any]] = []
    try:
        for number, item in enumerate(items, start=1):
            prediction = adapter.predict(item)
            predictions.append({"id": item["id"], **prediction})
            print(f"citation dev {number}/37 {item['id']}", flush=True)
    finally:
        adapter.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "split": "dev",
                "test_evaluated": False,
                "predictions": predictions,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
