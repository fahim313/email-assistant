from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq
from pydantic import BaseModel

from app.core.config import get_settings

type StructuredOutput = BaseModel | dict[str, object]
type MessageInput = str | list[BaseMessage]


def build_chat_model(
    provider: str,
    model: str,
) -> BaseChatModel:
    settings = get_settings()

    if provider == "groq":
        if settings.GROQ_API_KEY is None:
            raise RuntimeError("GROQ_API_KEY is not set")

        return ChatGroq(
            model=model,
            api_key=settings.GROQ_API_KEY,
            temperature=0,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=1,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")


@lru_cache
def get_structured_llm(
    schema: type[BaseModel],
) -> Runnable[MessageInput, StructuredOutput]:
    settings = get_settings()

    primary = build_chat_model(
        settings.LLM_PROVIDER,
        settings.LLM_MODEL,
    ).with_structured_output(schema)

    if settings.LLM_FALLBACK_PROVIDER and settings.LLM_FALLBACK_MODEL:
        fallback = build_chat_model(
            settings.LLM_FALLBACK_PROVIDER,
            settings.LLM_FALLBACK_MODEL,
        ).with_structured_output(schema)

        return primary.with_fallbacks([fallback])

    return primary