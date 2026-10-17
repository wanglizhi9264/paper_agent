from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from app.agent.contracts import RuntimeState
from app.agent.runtime import PaperAgent
from app.llm.base import LLMMessage


class GraphState(TypedDict):
    runtime: RuntimeState


async def run_graph(
    agent: PaperAgent,
    state: RuntimeState,
    query: str,
    history: list[LLMMessage],
    deadline: datetime,
) -> RuntimeState:
    async def advance(value: GraphState) -> GraphState:
        result = await agent.run(value["runtime"], query, history, deadline, one_step=True)
        return {"runtime": result}

    def route(value: GraphState) -> str:
        return END if value["runtime"].phase == "done" else "advance"

    builder = StateGraph(GraphState)
    builder.add_node("advance", RunnableLambda(advance))
    builder.add_edge(START, "advance")
    builder.add_conditional_edges("advance", route)
    # The service-owned transactional checkpoint is authoritative. No second DB saver.
    result = await builder.compile().ainvoke({"runtime": state}, {"recursion_limit": 64})
    return RuntimeState.model_validate(result["runtime"])
