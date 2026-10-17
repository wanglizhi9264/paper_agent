from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters
from pydantic import ValidationError

from app.mcp.client import EvidenceClient, EvidenceClientError
from app.schemas.evidence import EvidenceSearch, PaperListRequest


@pytest.fixture
def evidence_backend():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if self.path == "/api/v1/evidence/papers":
                payload = {"schema_version": 1, "items": [], "next_cursor": None}
                self.send_response(200)
            else:
                payload = {
                    "error": {"code": "MCP_DISABLED", "message": "do not expose backend internals"}
                }
                self.send_response(404)
            data = json.dumps(payload).encode()
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


async def test_real_stdio_initialization_discovery_call_and_stable_errors(evidence_backend):
    url, requests = evidence_backend
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=Path(__file__).resolve().parents[2],
        env={"PAPER_RAG_MCP_API_BASE_URL": url},
    )
    async with Client(params, read_timeout_seconds=15) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "list_papers",
            "search_papers",
            "read_chunks",
            "read_table",
        }
        assert all(t.output_schema for t in tools.tools)
        assert all(t.annotations.read_only_hint for t in tools.tools)
        result = await client.call_tool("list_papers", {"request": {}})
        assert not result.is_error
        assert result.structured_content["items"] == []
        failure = await client.call_tool("search_papers", {"request": {"query": "test"}})
        assert failure.is_error
        assert "MCP_DISABLED" in str(failure.content)
        assert "do not expose backend internals" not in str(failure.content)
    assert requests[0][0] == "/api/v1/evidence/papers"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1.evil.test",
        "http://user:secret@localhost",
        "file:///tmp/server",
        "http://localhost/?redirect=remote",
    ],
)
def test_adapter_rejects_non_loopback_or_ambiguous_base_url(url):
    with pytest.raises(EvidenceClientError, match="API_BASE_MUST_BE_LOOPBACK"):
        EvidenceClient(url)


async def test_adapter_does_not_follow_redirect_or_echo_backend_text():
    async def handler(request):
        return httpx.Response(
            302, headers={"Location": "https://example.com"}, text="private paths"
        )

    client = EvidenceClient("http://127.0.0.1:8000", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(EvidenceClientError, match="BACKEND_UNAVAILABLE"):
            await client.list_papers(PaperListRequest())
    finally:
        await client.close()


def test_scope_cannot_be_combined_with_context():
    with pytest.raises(ValidationError):
        EvidenceSearch(
            query="test", scope={"type": "all"}, context_id="e43ebff0-30a7-4c8d-94a6-56f8b381bb56"
        )


async def test_cancelling_tool_requests_backend_cancellation():
    import uuid

    created = asyncio.Event()
    operation_id, context_id = str(uuid.uuid4()), str(uuid.uuid4())
    cancellations = []

    async def handler(request):
        if request.url.path.endswith("/cancel"):
            cancellations.append(request.url.path)
            state = "cancelled"
        else:
            created.set()
            state = "queued"
        return httpx.Response(
            200,
            json={
                "operation_id": operation_id,
                "context_id": context_id,
                "status": state,
                "error_code": None,
                "result": None,
            },
        )

    client = EvidenceClient("http://127.0.0.1:8000", transport=httpx.MockTransport(handler))
    try:
        task = asyncio.create_task(client.search_papers(EvidenceSearch(query="x")))
        await created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancellations == [f"/api/v1/evidence/operations/{operation_id}/cancel"]
    finally:
        await client.close()
