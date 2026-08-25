from __future__ import annotations

from app.llm.base import LLMMessage

SYSTEM_PROMPT = """You are a paper research assistant. Answer questions strictly based on the provided sources.

Rules:
- Only use information from the Sources section below.
- Distinguish between facts from sources and your inferences.
- If evidence is insufficient, say so explicitly.
- For every verifiable claim, cite the source number like [1] or [2].
- Never fabricate source numbers that do not exist.
- Answer in the same language as the question.

Sources:
{sources}
"""

REWRITE_PROMPT = """Given the conversation history, fixed session scope, and current question, rewrite the question into a standalone retrieval query.

Rules:
- Only resolve references and add context from the conversation.
- Do NOT answer the question.
- Do NOT add facts not present in the conversation.
- Preserve paper, dataset, method variant, and metric names in separate hint lists.
- Return JSON only and include every required key.

Session scope:
{scope}

Conversation history:
{history}

Current question: {question}

Respond in JSON format:
{{"standalone_query":"...","paper_hints":[],"dataset_hints":[],"method_hints":[],"metric_hints":[]}}
"""


def build_system_prompt(sources: str) -> str:
    return SYSTEM_PROMPT.format(sources=sources)


def build_rewrite_prompt(
    history: list[tuple[str, str]], question: str, scope: str = '{"type":"all"}'
) -> str:
    history_text = "\n".join(f"{role}: {text}" for role, text in history[-4:])
    return REWRITE_PROMPT.format(history=history_text, question=question, scope=scope)


def build_messages(
    system_prompt: str,
    history: list[tuple[str, str]],
    query: str,
) -> list[LLMMessage]:
    messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
    for role, content in history[-8:]:
        messages.append(LLMMessage(role=role, content=content))
    messages.append(LLMMessage(role="user", content=query))
    return messages
