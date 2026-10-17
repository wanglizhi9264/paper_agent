"""Agent trajectory evaluator. Gold input is explicit and never inferred from retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field

from app.agent.contracts import StrictModel
from app.schemas.search import SearchScope


class AgentCase(StrictModel):
    id: str
    split: Literal["dev", "test"]
    category: Literal["reading", "method", "experiment", "comparison", "multi_turn", "unanswerable"]
    query: str
    scope: SearchScope
    skill_names: list[str] = Field(default_factory=list, max_length=2)
    expected_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    answerable: bool
    prior_queries: list[str] = Field(default_factory=list)


class AgentDataset(StrictModel):
    schema_version: Literal[1] = 1
    license: str
    snapshot_id: uuid.UUID
    cases: list[AgentCase]
    manually_verified: bool


async def run_case(
    client: httpx.AsyncClient, case: AgentCase, snapshot_id: uuid.UUID
) -> dict[str, object]:
    session_response = await client.post(
        "/api/v1/sessions",
        json={"title": "Agent evaluation", "scope": case.scope.model_dump(mode="json")},
    )
    session_response.raise_for_status()
    session_id = session_response.json()["id"]
    started = time.perf_counter()
    result: dict[str, object] = {}
    for query in [*case.prior_queries, case.query]:
        response = await client.post(
            "/api/v1/agent/runs",
            json={
                "session_id": session_id,
                "query": query,
                "mode": "agent",
                "skill_names": case.skill_names,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        if response.json()["snapshot_id"] != str(snapshot_id):
            await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
            raise ValueError("Dataset snapshot does not match run snapshot")
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status_response = await client.get(f"/api/v1/agent/runs/{run_id}")
            status_response.raise_for_status()
            result = status_response.json()
            if result["status"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.5)
        else:
            await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
            raise TimeoutError("Agent evaluation deadline")
    sources = result.get("sources") or []
    actual = {source["chunk_id"] for source in sources}
    expected = {str(value) for value in case.expected_chunk_ids}
    answer = str(result.get("answer") or "")
    return {
        "case_id": case.id,
        "category": case.category,
        "run_id": result["run_id"],
        "status": result["status"],
        "error_code": result["error_code"],
        "context_chunk_ids": sorted(actual),
        "context_recall": len(actual & expected) / len(expected) if expected else None,
        "refused": answer.startswith("Insufficient evidence in the provided sources."),
        "answerable": case.answerable,
        "latency_seconds": time.perf_counter() - started,
        "usage": result["usage"],
        "steps": result["steps"],
        "human_claim_review": "pending",
    }


async def evaluate(args: argparse.Namespace) -> None:
    dataset = AgentDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    if not dataset.manually_verified:
        raise ValueError("Gold must be manually verified before evaluation")
    if len(dataset.cases) < 40 and not args.allow_small_smoke:
        raise ValueError("Release suite requires at least 40 manually verified cases")
    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("Duplicate case IDs")
    selected = [case for case in dataset.cases if case.split == args.split]
    if not selected:
        raise ValueError("No selected cases")
    if args.split == "test" and not args.final_holdout:
        raise ValueError("Frozen test requires explicit --final-holdout")
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing evaluation")
    results = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=20, trust_env=False) as client:
        for case in selected:
            result = await run_case(client, case, dataset.snapshot_id)
            results.append(result)
            print(f"{case.id}: {result['status']}", flush=True)
    latencies = sorted(float(r["latency_seconds"]) for r in results)
    recalls = [float(r["context_recall"]) for r in results if r["context_recall"] is not None]
    report = {
        "schema_version": 1,
        "snapshot_id": str(dataset.snapshot_id),
        "split": args.split,
        "case_count": len(results),
        "context_recall": statistics.mean(recalls) if recalls else None,
        "p50_seconds": statistics.median(latencies),
        "p95_seconds": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        "release_gate": "pending_human_claim_and_citation_review",
        "predictions": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--allow-small-smoke", action="store_true")
    parser.add_argument("--final-holdout", action="store_true")
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    main()
