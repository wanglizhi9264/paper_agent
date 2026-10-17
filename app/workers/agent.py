from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.exceptions import RedisError
from sqlalchemy import select

from app.agent.context import LocalTokenCounter
from app.agent.contracts import AgentError, RuntimeState
from app.agent.graph import run_graph
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.api.errors import AppError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.document_ir.errors import ParseError
from app.embedding.base import EmbeddingError, EmbeddingResult, ModelManifest
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMError, LLMMessage
from app.llm.openai_compatible import get_llm_provider
from app.models.agent import AgentRun, AgentRunOutbox
from app.models.session import Message
from app.services.agent_runs import (
    RunStore,
    claim,
    get_run,
    manifest,
    prune,
    reconcile,
    terminate,
    utc,
)
from app.services.agent_trial import CorpusTools
from app.services.gpu_llm import GpuBoundProvider

logger = get_logger(__name__)


async def dispatch_pending(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.agent_runtime_enabled:
        return
    sessions = get_sessionmaker()
    async with sessions() as db, db.begin():
        await reconcile(db)
    own_pool = ctx.get("redis") is None
    pool = ctx.get("redis")
    try:
        if own_pool:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        async with sessions() as db, db.begin():
            rows = list(
                (
                    await db.execute(
                        select(AgentRunOutbox)
                        .join(AgentRun, AgentRun.id == AgentRunOutbox.run_id)
                        .where(AgentRunOutbox.dispatched.is_(False), AgentRun.status == "queued")
                        .with_for_update(skip_locked=True)
                        .limit(30)
                    )
                ).scalars()
            )
            for row in rows:
                await cast(ArqRedis, pool).enqueue_job(
                    "agent_run", str(row.run_id), _job_id=f"agent:{row.run_id}:{row.generation}"
                )
                row.dispatched = True
    except (RedisError, OSError):
        logger.warning("agent_dispatch_deferred")
    finally:
        if own_pool and pool is not None:
            await pool.aclose()


async def cleanup_agent_runs(ctx: dict[str, Any]) -> None:
    del ctx
    async with get_sessionmaker()() as db, db.begin():
        await prune(db, get_settings().agent_retention_days)


async def agent_run(ctx: dict[str, Any], run_id: str) -> None:
    del ctx
    settings = get_settings()
    sessions = get_sessionmaker()
    async with sessions() as db, db.begin():
        claimed = await claim(db, uuid.UUID(run_id), str(uuid.uuid4()))
        if claimed is None:
            return
        run, token = claimed
        state = RuntimeState.model_validate(run.state)
        query, pins, deadline, session_id = run.query, run.pins, run.deadline, run.session_id
    # A lazy backend loads GPU models only when a search actually needs them.
    try:
        counter = LocalTokenCounter(settings.agent_tokenizer_path)
        tokenizer_manifest = json.loads(
            (settings.agent_tokenizer_path / "agent_manifest.json").read_text(encoding="utf-8")
        )
        if (
            tokenizer_manifest.get("tokenizer_signature") != counter.signature
            or tokenizer_manifest.get("model") != settings.llm_model
        ):
            raise AgentError("TOKENIZER_CHANGED")
        if settings.agent_action_protocol == "native":
            counter.count_request([LLMMessage("system", "test"), LLMMessage("user", "test")], [])
    except (AgentError, OSError, ValueError) as exc:
        async with sessions() as db, db.begin():
            stored = await get_run(db, uuid.UUID(run_id), lock=True)
            if stored.fencing_token == token:
                await terminate(
                    db,
                    stored,
                    "failed",
                    exc.code if isinstance(exc, AgentError) else "TOKENIZER_UNAVAILABLE",
                )
        return
    tools = CorpusTools(
        sessions,
        pins,
        LazyEmbedding(),
        settings,
        counter,
        pinned=True,
    )
    store = RunStore(sessions, uuid.UUID(run_id), token, tools)
    graph_task: asyncio.Task[RuntimeState] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        catalog = SkillRegistry(
            settings.agent_skill_root, settings.storage_dir / "skills" / "enabled"
        )
        if pins["runtime"] != manifest(settings, catalog):
            raise AgentError("RESUME_INCOMPATIBLE")
        if deadline is None:
            raise AgentError("CHECKPOINT_INVALID")
        async with sessions() as db:
            messages = list(
                (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.session_id == session_id,
                            Message.id != run.user_message_id,
                            Message.status == "complete",
                        )
                        .order_by(Message.created_at, Message.id)
                    )
                ).scalars()
            )
        history = [
            LLMMessage(m.role.value if hasattr(m.role, "value") else str(m.role), m.content)
            for m in messages
        ]
        provider = GpuBoundProvider(get_llm_provider(settings), sessions)
        agent = PaperAgent(
            provider,
            tools.counter,
            tools,
            store,
            catalog,
            context_limit=settings.llm_context_tokens,
            action_protocol=settings.agent_action_protocol,
            stream_answer=True,
            trace_enabled=bool(pins.get("trace_enabled")),
            reasoning_effort=None
            if settings.agent_reasoning_effort == "provider_default"
            else settings.agent_reasoning_effort,
        )
        await agent.prepare_summary(state, history, query)
        graph_task = asyncio.create_task(run_graph(agent, state, query, history, utc(deadline)))

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(5)
                await store.heartbeat()

        heartbeat_task = asyncio.create_task(heartbeat())
        remaining = max(0.01, (utc(deadline) - datetime.now(UTC)).total_seconds())
        done, _ = await asyncio.wait(
            {graph_task, heartbeat_task}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise AgentError("RUN_DEADLINE_EXCEEDED")
        for task in done:
            task.result()
    except (AgentError, LLMError, AppError, EmbeddingError, ParseError, TimeoutError) as exc:
        code = getattr(exc, "code", "RUN_DEADLINE_EXCEEDED")
        await store.fail(code)
        logger.warning("agent_run_failed", run_id=run_id, error_code=code)
    except asyncio.CancelledError:
        await asyncio.shield(store.interrupt())
        raise
    except Exception:
        await store.fail("INTERNAL_ERROR")
        logger.exception("agent_run_crashed", run_id=run_id)
        raise
    finally:
        for cleanup_task in (graph_task, heartbeat_task):
            if cleanup_task is not None and not cleanup_task.done():
                cleanup_task.cancel()
        await asyncio.gather(
            *(t for t in (graph_task, heartbeat_task) if t is not None), return_exceptions=True
        )


class LazyEmbedding:
    @property
    def manifest(self) -> ModelManifest:
        return get_embedding_provider().manifest

    def embed_query(self, query: str) -> EmbeddingResult:
        return get_embedding_provider().embed_query(query)

    def embed_texts(self, texts: list[str], *, is_query: bool = False) -> EmbeddingResult:
        return get_embedding_provider().embed_texts(texts, is_query=is_query)
