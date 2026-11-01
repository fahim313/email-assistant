import asyncio
from datetime import datetime
from typing import TypedDict, cast
from zoneinfo import ZoneInfo

import httpx
from groq import APIError
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.core.config import get_settings
from app.modules.ai.llm import get_structured_llm
from app.modules.ai.prompts.email_analysis import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.modules.analysis.schemas import ActionItemOutput, AnalysisOutput

MAX_ATTEMPTS = 3
MAX_SUMMARY_CHARS = 600
MAX_RISK_CHARS = 300
MAX_ACTION_ITEMS = 5
MAX_ACTION_TEXT_CHARS = 300


class AnalysisError(Exception):
    pass


class GraphState(TypedDict):
    thread_text: str
    attempts: int
    result: AnalysisOutput | None
    error: str | None


def _aware(
    value: datetime | None,
    tz: ZoneInfo,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=tz)

    return value.astimezone(tz)


async def analyze_node(
    state: GraphState,
) -> dict[str, object]:
    settings = get_settings()
    attempts: int = state["attempts"]
    timezone_name: str = str(settings.APP_TIMEZONE)

    if attempts > 0:
        await asyncio.sleep(2.0**attempts)

    now = datetime.now(ZoneInfo(timezone_name))

    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=build_user_prompt(
                state["thread_text"],
                now,
                timezone_name,
            )
        ),
    ]

    try:
        raw = await get_structured_llm(AnalysisOutput).ainvoke(messages)
        result = AnalysisOutput.model_validate(raw)

        return {
            "attempts": attempts + 1,
            "result": result,
            "error": None,
        }

    except (
        APIError,
        httpx.HTTPError,
        TimeoutError,
        ValidationError,
    ) as exc:
        return {
            "attempts": attempts + 1,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}"[:1000],
        }


def normalize_node(
    state: GraphState,
) -> dict[str, object]:
    result = state["result"]

    if result is None:
        return {}

    timezone_name: str = str(get_settings().APP_TIMEZONE)
    tz = ZoneInfo(timezone_name)

    summary = result.summary.strip()[:MAX_SUMMARY_CHARS]

    if not summary:
        return {
            "result": None,
            "error": "Analysis returned an empty summary",
        }

    deadline_at = _aware(result.deadline_at, tz)

    action_items: list[ActionItemOutput] = []

    for item in result.action_items:
        text = item.text.strip()[:MAX_ACTION_TEXT_CHARS]

        if not text:
            continue

        action_items.append(
            ActionItemOutput(
                text=text,
                due_date=_aware(item.due_date, tz),
            )
        )

        if len(action_items) >= MAX_ACTION_ITEMS:
            break

    risk_reason = (
        result.risk_reason.strip()[:MAX_RISK_CHARS]
        if result.is_suspicious and result.risk_reason
        else None
    )

    normalized = AnalysisOutput.model_validate(
        {
            **result.model_dump(),
            "summary": summary,
            "priority": "low" if result.is_spam else result.priority,
            "needs_reply": result.needs_reply and not result.is_spam,
            "follow_up": result.follow_up and not result.is_spam,
            "risk_reason": risk_reason,
            "has_deadline": deadline_at is not None,
            "deadline_at": deadline_at,
            "action_items": action_items,
        }
    )

    return {
        "result": normalized,
        "error": None,
    }


def route_after_normalize(
    state: GraphState,
) -> str:
    if state["result"] is not None:
        return "end"

    if state["attempts"] < MAX_ATTEMPTS:
        return "analyze"

    return "end"


def build_graph():
    graph = StateGraph(GraphState)

    _ = graph.add_node(  # pyright: ignore[reportUnknownMemberType]
        "analyze",
        analyze_node,
    )

    _ = graph.add_node(  # pyright: ignore[reportUnknownMemberType]
        "normalize",
        normalize_node,
    )

    _ = graph.add_edge(START, "analyze")
    _ = graph.add_edge("analyze", "normalize")

    _ = graph.add_conditional_edges(
        "normalize",
        route_after_normalize,
        {
            "analyze": "analyze",
            "end": END,
        },
    )

    return graph.compile()  # pyright: ignore[reportUnknownMemberType]


analysis_graph = build_graph()


async def run_analysis(
    thread_text: str,
) -> AnalysisOutput:
    final = cast(
        GraphState,
        await analysis_graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
            {
                "thread_text": thread_text,
                "attempts": 0,
                "result": None,
                "error": None,
            }
        ),
    )

    result = final["result"]

    if result is None:
        raise AnalysisError(
            final["error"] or "Email analysis failed"
        )

    return result