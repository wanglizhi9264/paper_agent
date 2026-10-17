"""Explicit local paper-agent trial. Run --help before using an existing corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.agent.context import LocalTokenCounter
from app.agent.contracts import AgentError, RuntimeState
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine, get_sessionmaker
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMError, LLMMessage
from app.llm.openai_compatible import get_llm_provider
from app.models.agent_run import AgentTrialRun
from app.models.enums import MessageStatus, SessionScopeType
from app.models.session import Message, Session
from app.schemas.search import SearchScope
from app.services.agent_trial import CorpusTools, TrialStore, corpus_pins, create_trial

SKILL_ROOT = Path(__file__).resolve().parents[2] / "agent_skills"
CLI_LOCK = 731946218004


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Paper Agent CLI trial (explicit opt-in)")
    result.add_argument("--list-skills", action="store_true")
    result.add_argument("--validate-skill", type=Path)
    result.add_argument("--enable-skill", type=Path)
    result.add_argument("--query", help="Question; use --session-id for follow-up questions")
    result.add_argument("--session-id", type=uuid.UUID)
    result.add_argument("--document-id", type=uuid.UUID, action="append", default=[])
    result.add_argument("--skill", action="append", default=[])
    result.add_argument(
        "--resume", type=uuid.UUID, help="Resume an interrupted run without resetting budgets"
    )
    result.add_argument("--show-run", type=uuid.UUID, help="Show run metadata and event trace")
    result.add_argument("--tokenizer", type=Path, default=Path("storage/models/agent-tokenizer"))
    result.add_argument(
        "--setup-tokenizer", metavar="HF_REPO", help="Explicit tokenizer-only download"
    )
    result.add_argument(
        "--revision", default="main", help="Setup resolves this ref to an immutable SHA"
    )
    result.add_argument("--chat-format", choices=["hf", "deepseek-v4"], default="hf")
    return result


def setup_tokenizer(repo: str, revision: str, destination: Path, chat_format: str) -> None:
    from huggingface_hub import HfApi, snapshot_download

    settings = get_settings()
    if not destination.resolve().is_relative_to(settings.storage_dir.resolve()):
        raise AgentError("TOKENIZER_PATH_OUTSIDE_STORAGE")
    sha = HfApi().model_info(repo, revision=revision).sha
    if not sha:
        raise AgentError("TOKENIZER_REVISION_UNAVAILABLE")
    snapshot_download(
        repo,
        revision=sha,
        local_dir=destination,
        allow_patterns=[
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "chat_template.jinja",
            "config.json",
            "LICENSE",
        ],
    )
    counter = LocalTokenCounter(destination, chat_format)
    counter.count_messages([LLMMessage("system", "test"), LLMMessage("user", "你好 hello")])
    (destination / "agent_manifest.json").write_text(
        json.dumps(
            {
                "repo": repo,
                "revision": sha,
                "tokenizer_signature": counter.signature,
                "chat_format": chat_format,
                "model": settings.llm_model,
            }
        ),
        encoding="utf-8",
    )
    print(f"Tokenizer ready: {repo}@{sha}; no model weights downloaded.")


async def execute(args: argparse.Namespace, registry: SkillRegistry) -> None:
    settings = get_settings()
    sessions = get_sessionmaker()
    if args.show_run:
        async with sessions() as db:
            run = await db.get(AgentTrialRun, args.show_run)
            if run is None:
                raise AgentError("RUN_NOT_FOUND")
            state = RuntimeState.model_validate(run.state)
            print(
                json.dumps(
                    {
                        "run_id": str(run.id),
                        "session_id": str(run.session_id),
                        "status": run.status,
                        "error_code": run.error_code,
                        "stop_reason": state.stop_reason,
                        "events": run.events,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return
    if not args.resume and (not args.query or not args.query.strip() or len(args.query) > 4000):
        raise AgentError("QUERY_REQUIRED")
    if args.resume and (args.query or args.session_id or args.document_id or args.skill):
        raise AgentError("RESUME_ARGUMENT_CONFLICT")
    counter = LocalTokenCounter(args.tokenizer)
    manifest_path = args.tokenizer / "agent_manifest.json"
    if not manifest_path.is_file():
        raise AgentError("TOKENIZER_SETUP_REQUIRED")
    tokenizer_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tokenizer_manifest.get("tokenizer_signature") != counter.signature:
        raise AgentError("TOKENIZER_CHANGED")
    if tokenizer_manifest.get("model") != settings.llm_model:
        raise AgentError("TOKENIZER_MODEL_MISMATCH")
    # Explicit CLI selection is the capability configuration; no heuristic model guessing.
    runtime_pins = {
        "runtime": "cli-json-v1",
        "skills": registry.signature,
        "tokenizer": counter.signature,
        "tokenizer_repo": tokenizer_manifest["repo"],
        "model": settings.llm_model,
        "provider": settings.llm_base_url,
        "context_limit": settings.llm_context_tokens,
    }
    async with get_engine().connect() as lock_connection:
        locked = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": CLI_LOCK}
        )
        await lock_connection.commit()
        if not locked:
            raise AgentError("AGENT_TRIAL_ACTIVE")
        store: TrialStore | None = None
        try:
            async with sessions() as db, db.begin():
                if args.resume:
                    run = await db.get(AgentTrialRun, args.resume, with_for_update=True)
                    if run is None:
                        raise AgentError("RUN_NOT_FOUND")
                    if run.status not in {"running", "interrupted"}:
                        raise AgentError("RUN_NOT_RESUMABLE")
                    if run.pins.get("runtime") != runtime_pins:
                        raise AgentError("RESUME_INCOMPATIBLE")
                    session = await db.get(Session, run.session_id)
                    if session is None:
                        raise AgentError("SESSION_NOT_FOUND")
                    query = run.query
                else:
                    query = args.query.strip()
                    if args.session_id:
                        session = await db.get(Session, args.session_id)
                        if session is None:
                            raise AgentError("SESSION_NOT_FOUND")
                        if args.document_id:
                            raise AgentError("SESSION_SCOPE_IMMUTABLE")
                    else:
                        scope = (
                            SearchScope(type="documents", document_ids=args.document_id)
                            if args.document_id
                            else SearchScope(type="all")
                        )
                        session = Session(
                            title="Paper Agent trial",
                            scope_type=SessionScopeType(scope.type),
                            scope_payload=scope.model_dump(mode="json", exclude={"type"}),
                        )
                        db.add(session)
                        await db.flush()
                    existing = await db.scalar(
                        select(AgentTrialRun.id).where(
                            AgentTrialRun.session_id == session.id,
                            AgentTrialRun.status.in_(["running", "interrupted"]),
                        )
                    )
                    if existing:
                        raise AgentError("SESSION_RUN_ACTIVE")
                    scope = SearchScope.model_validate(
                        {"type": session.scope_type, **session.scope_payload}
                    )
                    pins = await corpus_pins(db, scope)
                    pins["runtime"] = runtime_pins
                    names = args.skill or ["paper-reading"]
                    if len(set(names)) > 2:
                        raise AgentError("SKILL_LIMIT_EXCEEDED")
                    loaded = {name: registry.get(name).digest for name in names}
                    counter.count_messages(
                        [LLMMessage("system", "test"), LLMMessage("user", query)]
                    )
                    if counter.count(query) > 800:
                        raise AgentError("QUERY_TOO_LONG")
                    run = await create_trial(db, session, query, pins, loaded)
                state = RuntimeState.model_validate(run.state)
                deadline = (
                    run.deadline.replace(tzinfo=UTC)
                    if run.deadline.tzinfo is None
                    else run.deadline
                )
                history_rows = list(
                    (
                        await db.execute(
                            select(Message)
                            .where(
                                Message.session_id == session.id,
                                Message.status == MessageStatus.COMPLETE,
                            )
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .limit(40)
                        )
                    ).scalars()
                )
                # New user message is already persisted at creation; exclude it from history.
                history_rows.reverse()
                if history_rows and history_rows[-1].role == "user":
                    history_rows.pop()
                history = [LLMMessage(str(m.role), m.content) for m in history_rows]
                run_id, session_id, pins = run.id, session.id, run.pins
            print(
                f"Run: {run_id}\nSession: {session_id}\nSkills: {', '.join(state.loaded_skills)}",
                flush=True,
            )
            tools = CorpusTools(sessions, pins, get_embedding_provider(settings), settings, counter)
            store = TrialStore(sessions, run_id, tools)
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                raise AgentError("RUN_DEADLINE_EXCEEDED")
            agent = PaperAgent(
                get_llm_provider(settings),
                counter,
                tools,
                store,
                registry,
                context_limit=settings.llm_context_tokens,
            )
            result = await asyncio.wait_for(agent.run(state, query, history, deadline), remaining)
            print("\n" + (result.answer or ""))
            print(f"\nStop: {result.stop_reason}; token reservation: {result.tokens}/32000")
            for source in result.packed_sources:
                print(
                    f"[{source.index}] {source.document_title}, page {source.page}, chunk {source.chunk_id}"
                )
        except asyncio.CancelledError:
            if store is not None:
                await asyncio.shield(store.mark_error("INTERRUPTED", interrupted=True))
            raise
        except (AgentError, LLMError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, AgentError | LLMError) else "RUN_DEADLINE_EXCEEDED"
            if store is not None:
                await store.mark_error(code)
            raise AgentError(code) from exc
        finally:
            await lock_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": CLI_LOCK}
            )
            await lock_connection.commit()


async def _run(args: argparse.Namespace, registry: SkillRegistry) -> None:
    try:
        await execute(args, registry)
    finally:
        await dispose_engine()


def main() -> int:
    args = parser().parse_args()
    try:
        registry = SkillRegistry(SKILL_ROOT, get_settings().storage_dir / "skills" / "enabled")
        if args.validate_skill or args.enable_skill:
            package = args.validate_skill or args.enable_skill
            validated = SkillRegistry(package)
            if len(validated.skills) != 1:
                raise AgentError("SKILL_INVALID")
            skill = next(iter(validated.skills.values()))
            if args.enable_skill:
                if skill.name in registry.skills:
                    raise AgentError("SKILL_NAME_CONFLICT")
                target = get_settings().storage_dir / "skills" / "enabled" / skill.name
                if target.exists():
                    raise AgentError("SKILL_ALREADY_ENABLED")
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = (
                    get_settings().storage_dir
                    / "skills"
                    / "staging"
                    / str(uuid.uuid4())
                    / skill.name
                )
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(package, staged)
                checked = SkillRegistry(staged).get(skill.name)
                if checked.digest != skill.digest:
                    raise AgentError("SKILL_CHANGED")
                staged.replace(target)
            print(f"Validated {skill.name}@{skill.digest}")
        elif args.list_skills:
            for skill in registry.skills.values():
                print(f"{skill.name}: {skill.description} [{skill.digest[:12]}]")
        elif args.setup_tokenizer:
            setup_tokenizer(args.setup_tokenizer, args.revision, args.tokenizer, args.chat_format)
        else:
            asyncio.run(_run(args, registry))
    except AgentError as exc:
        print(f"Agent stopped: {exc.code}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Agent stopped: DATABASE_UNAVAILABLE_OR_MIGRATION_REQUIRED", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Resume using --resume RUN_ID (the original deadline still applies).")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
