from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agent.contracts import AgentError, Evidence, TokenCounter
from app.llm.base import LLMMessage
from app.llm.openai_compatible import serialize_message
from app.schemas.chat import SourceOut


class LocalTokenCounter:
    """Local-only model tokenizer with its pinned chat template; never downloads."""

    def __init__(self, path: Path, chat_format: str | None = None) -> None:
        try:
            if chat_format is None:
                manifest = path / "agent_manifest.json"
                chat_format = (
                    json.loads(manifest.read_text(encoding="utf-8")).get("chat_format", "hf")
                    if manifest.is_file()
                    else "hf"
                )
            self.chat_format = chat_format
            if chat_format not in {"hf", "deepseek-v4"}:
                raise AgentError("TOKENIZER_UNAVAILABLE")
            self.tokenizer: Any
            if chat_format == "deepseek-v4":
                from tokenizers import Tokenizer

                self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
            else:
                from transformers import AutoTokenizer

                # Third-party factory is untyped; keep the boundary inside this adapter.
                factory: Any = AutoTokenizer
                self.tokenizer = factory.from_pretrained(
                    str(path), local_files_only=True, trust_remote_code=False
                )
            if chat_format == "hf" and not self.tokenizer.chat_template:
                raise AgentError("TOKENIZER_UNAVAILABLE")
            files = sorted(path.glob("*.json")) + sorted(path.glob("*.jinja"))
            digest = hashlib.sha256()
            digest.update(f"{chat_format}:text-nonthinking-v1".encode())
            for file in files:
                if file.name == "agent_manifest.json":
                    continue
                digest.update(file.name.encode() + b"\0" + file.read_bytes())
            self.signature = digest.hexdigest()
        except (ImportError, OSError, ValueError) as exc:
            raise AgentError("TOKENIZER_UNAVAILABLE") from exc

    def count(self, text: str) -> int:
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        return len(encoded.ids) if self.chat_format == "deepseek-v4" else len(encoded)

    def count_messages(self, messages: list[LLMMessage]) -> int:
        if self.chat_format == "deepseek-v4":
            return self.count(render_deepseek_v4(messages))
        try:
            return len(
                self.tokenizer.apply_chat_template(
                    [serialize_message(m) for m in messages],
                    tokenize=True,
                    add_generation_prompt=True,
                )
            )
        except (ValueError, TypeError) as exc:
            raise AgentError("TOKENIZER_UNAVAILABLE") from exc

    def count_request(self, messages: list[LLMMessage], tools: list[dict[str, object]]) -> int:
        if self.chat_format != "hf" or "tools" not in str(self.tokenizer.chat_template):
            raise AgentError("AGENT_CAPABILITY_UNSUPPORTED")
        try:
            return len(
                self.tokenizer.apply_chat_template(
                    [serialize_message(m) for m in messages],
                    tools=tools,
                    tokenize=True,
                    add_generation_prompt=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise AgentError("TOKENIZER_UNAVAILABLE") from exc


def render_deepseek_v4(messages: list[LLMMessage]) -> str:
    """Official V4 text/non-thinking subset; no native tools or mid-system messages.

    Reference: deepseek-ai/deepseek-recipe encoding/v4 (MIT), checked 2026-10-16.
    Unsupported shapes fail rather than silently guessing the provider's template.
    """
    rendered = "<｜begin▁of▁sentence｜>"
    previous = ""
    for index, message in enumerate(messages):
        if message.role == "system" and index == 0:
            rendered += message.content
        elif message.role == "user":
            rendered += ("\n\n" if previous == "user" else "<｜User｜>") + message.content
        elif message.role == "assistant":
            rendered += "<｜Assistant｜></think>" + message.content + "<｜end▁of▁sentence｜>"
        else:
            raise AgentError("TOKENIZER_MESSAGE_UNSUPPORTED")
        previous = message.role
    return rendered + "<｜Assistant｜></think>"


def truncate_text(text: str, limit: int, counter: TokenCounter) -> str:
    if counter.count(text) <= limit:
        return text
    low, high = 0, len(text)
    # Verify the returned prefix: tokenization need not be monotonic at every character.
    while low < high:
        middle = (low + high + 1) // 2
        if counter.count(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    result = text[:low]
    while result and counter.count(result) > limit:
        result = result[:-1]
    return result


def source_text(source: SourceOut) -> str:
    return (
        f"[Source {source.index}]\nDocument: {source.document_title}\n"
        f"Section: {' > '.join(source.section_path)}\nPage: {source.page}\n"
        f"Chunk-ID: {source.chunk_id}\nContent:\n{source.content}"
    )


def fit_messages(
    system: str,
    query: str,
    history: list[LLMMessage],
    counter: TokenCounter,
    input_limit: int,
) -> list[LLMMessage]:
    if counter.count(query) > 800:
        raise AgentError("QUERY_TOO_LONG")
    selected: list[LLMMessage] = []
    # Preserve complete user/assistant turns, never interrupted output.
    for index in range(len(history) - 2, -1, -2):
        pair = history[index : index + 2]
        if [m.role for m in pair] != ["user", "assistant"]:
            continue
        candidate = pair + selected
        if sum(counter.count(m.content) for m in candidate) > 1200:
            break
        if (
            counter.count_messages(
                [LLMMessage("system", system), *candidate, LLMMessage("user", query)]
            )
            > input_limit
        ):
            break
        selected = candidate
    result = [LLMMessage("system", system), *selected, LLMMessage("user", query)]
    if counter.count_messages(result) > input_limit:
        raise AgentError("CONTEXT_BUDGET_EXCEEDED")
    return result


def pack_sources(
    evidence: list[Evidence],
    counter: TokenCounter,
    budget: int,
) -> list[SourceOut]:
    return pack_source_blocks([item.source for item in evidence], counter, budget)


def pack_source_blocks(
    candidates: list[SourceOut], counter: TokenCounter, budget: int
) -> list[SourceOut]:
    sources: list[SourceOut] = []
    seen: set[str] = set()
    for original in candidates:
        if str(original.chunk_id) in seen:
            continue
        source = original.model_copy(update={"index": len(sources) + 1})
        remaining = budget - counter.count("\n\n".join(source_text(s) for s in sources))
        overhead = counter.count(source_text(source.model_copy(update={"content": ""}))) + 8
        if remaining <= overhead:
            continue
        content = truncate_text(source.content, remaining - overhead, counter)
        if content != source.content:
            boundary = max(content.rfind(c) for c in (".", "。", "!", "！", "?", "？", "\n"))
            if boundary > len(content) // 2:
                content = content[: boundary + 1]
        if not content.strip():
            continue
        source = source.model_copy(
            update={"content": content, "truncated": source.truncated or content != source.content}
        )
        candidate = [*sources, source]
        if counter.count("\n\n".join(source_text(s) for s in candidate)) > budget:
            continue
        sources.append(source)
        seen.add(str(source.chunk_id))
    return sources


def build_grounded_context(
    candidates: list[SourceOut],
    history: list[LLMMessage],
    query: str,
    counter: TokenCounter,
    context_limit: int,
    *,
    guidance: str = "",
    reduced: bool = False,
) -> tuple[list[LLMMessage], list[SourceOut]]:
    from app.llm.prompts import build_system_prompt

    suffix = "\nReading workflow and continuity (not evidence):\n" + guidance if guidance else ""
    empty_system = build_system_prompt("") + suffix
    base = fit_messages(empty_system, query, history, counter, context_limit - 2012)
    budget = max(0, context_limit - 2012 - counter.count_messages(base) - 64)
    if reduced:
        budget //= 2
    sources = pack_source_blocks(candidates, counter, budget)
    if candidates and not sources:
        raise AgentError("CONTEXT_BUDGET_EXCEEDED")
    system = build_system_prompt("\n\n".join(source_text(source) for source in sources)) + suffix
    return fit_messages(system, query, history, counter, context_limit - 2012), sources


def compact_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
