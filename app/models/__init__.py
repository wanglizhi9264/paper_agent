"""Importing this package registers all ORM models on the shared metadata.

Alembic autogenerate and ``Base.metadata`` consumers import this module to
ensure every mapped table is known. Models must not import from api/worker.
"""

from __future__ import annotations

from app.models.agent import (
    AgentCheckpoint,
    AgentEvent,
    AgentEvidence,
    AgentRun,
    AgentRunOutbox,
    AgentStep,
    SessionSummary,
)
from app.models.agent_run import AgentTrialRun
from app.models.chunk import FAISS_ID_SEQ, Chunk, DocumentVersion
from app.models.collection import Collection, CollectionDocument
from app.models.document import Document
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.models.job import IngestionJob
from app.models.retrieval_log import RetrievalLog
from app.models.session import Message, Session

__all__ = [
    "FAISS_ID_SEQ",
    "AgentCheckpoint",
    "AgentEvent",
    "AgentEvidence",
    "AgentRun",
    "AgentRunOutbox",
    "AgentStep",
    "AgentTrialRun",
    "Chunk",
    "Collection",
    "CollectionDocument",
    "Document",
    "DocumentVersion",
    "IndexSnapshot",
    "IngestionJob",
    "Message",
    "RetrievalLog",
    "Session",
    "SessionSummary",
    "SystemState",
]
