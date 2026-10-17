from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from app.schemas.evidence import (
    EvidenceChunks,
    EvidenceOperationOut,
    EvidenceResponse,
    EvidenceSearch,
    EvidenceTable,
    PaperList,
    PaperListRequest,
)


class EvidenceClientError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceClient:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise EvidenceClientError("API_BASE_MUST_BE_LOOPBACK")
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=2),
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request[T: BaseModel](
        self,
        method: str,
        path: str,
        result_type: type[T],
        body: BaseModel | None = None,
        *,
        key: str | None = None,
    ) -> T:
        try:
            async with self.http.stream(
                method,
                path,
                json=body.model_dump(mode="json") if body else None,
                headers={"Idempotency-Key": key} if key else {},
            ) as reply:
                data = bytearray()
                async for part in reply.aiter_bytes():
                    data.extend(part)
                    if len(data) > 131072:
                        raise EvidenceClientError("TOOL_OUTPUT_TOO_LARGE")
                payload = json.loads(data)
                if reply.status_code >= 300:
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    code = error.get("code", "BACKEND_UNAVAILABLE")
                    # Do not echo arbitrary server text, paths or stack traces.
                    if (
                        not isinstance(code, str)
                        or not code.replace("_", "").isalnum()
                        or not code.isupper()
                        or len(code) > 64
                    ):
                        code = "BACKEND_UNAVAILABLE"
                    raise EvidenceClientError(code)
                return result_type.model_validate(payload)
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError) as exc:
            raise EvidenceClientError("BACKEND_UNAVAILABLE") from exc

    async def list_papers(self, body: PaperListRequest) -> PaperList:
        return await self.request("POST", "/api/v1/evidence/papers", PaperList, body)

    async def search_papers(self, body: EvidenceSearch) -> EvidenceResponse:
        operation_id: uuid.UUID | None = None
        complete = False
        try:
            async with asyncio.timeout(75):
                op = await self.request(
                    "POST",
                    "/api/v1/evidence/search",
                    EvidenceOperationOut,
                    body,
                    key=str(uuid.uuid4()),
                )
                operation_id = op.operation_id
                while op.status in {"queued", "running"}:
                    await asyncio.sleep(0.25)
                    op = await self.request(
                        "GET", f"/api/v1/evidence/operations/{operation_id}", EvidenceOperationOut
                    )
                complete = True
                if op.status != "succeeded" or op.result is None:
                    raise EvidenceClientError(op.error_code or "SEARCH_FAILED")
                return op.result
        except TimeoutError as exc:
            raise EvidenceClientError("OPERATION_DEADLINE_EXCEEDED") from exc
        finally:
            if operation_id and not complete:
                # Cancellation is bounded and best effort; the backend deadline is durable.
                async def cancel() -> None:
                    try:
                        async with asyncio.timeout(2):
                            await self.request(
                                "POST",
                                f"/api/v1/evidence/operations/{operation_id}/cancel",
                                EvidenceOperationOut,
                            )
                    except (EvidenceClientError, TimeoutError):
                        pass  # Backend deadline/lease still releases abandoned work.

                task = asyncio.create_task(cancel())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    task.cancel()
                    raise

    async def read_chunks(self, body: EvidenceChunks) -> EvidenceResponse:
        return await self.request("POST", "/api/v1/evidence/chunks", EvidenceResponse, body)

    async def read_table(self, body: EvidenceTable) -> EvidenceResponse:
        return await self.request("POST", "/api/v1/evidence/table", EvidenceResponse, body)
