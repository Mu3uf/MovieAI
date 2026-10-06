"""LLM abstraction: provider/model come from env vars, so swapping models never touches the agent."""
import logging
from functools import lru_cache

from config import settings

log = logging.getLogger("llm")


class LLMConfigError(Exception):
    pass


@lru_cache(maxsize=1)
def get_llm():
    if not settings.LLM_API_KEY:
        raise LLMConfigError("LLM_API_KEY is not set.")
    provider, model = settings.LLM_PROVIDER, settings.LLM_MODEL
    log.info("LLM provider=%s model=%s", provider, model)
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=settings.LLM_API_KEY,
                        temperature=settings.LLM_TEMPERATURE, timeout=30, max_retries=1)
    # Any other LangChain provider (needs `pip install langchain langchain-<provider>`)
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, model_provider=provider, api_key=settings.LLM_API_KEY,
                           temperature=settings.LLM_TEMPERATURE)


def friendly_llm_error(e: Exception) -> tuple[str, int]:
    """(message safe for users, http status). Technical details are logged, not shown."""
    text = str(e).lower()
    if "rate" in text and "limit" in text or "429" in text:
        return "The AI service is busy right now (rate limit). Please wait a few seconds and try again.", 429
    if "401" in text or "invalid api key" in text or "authentication" in text:
        return "The AI service key is not valid. The site owner needs to check the configuration.", 503
    if "timeout" in text or "timed out" in text or "connection" in text:
        return "The AI service did not answer in time. Please try again.", 504
    return "The AI service had a problem. Please try again.", 502
