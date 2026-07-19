"""Provider factory — the only place (besides app wiring) that may import
concrete LLM providers."""

from __future__ import annotations

from cleanwispr.llm.base import ChatOptions, LlmProvider, LlmProviderError
from cleanwispr.llm.ollama import OllamaProvider
from cleanwispr.storage.settings import LlmSettings


def create_provider(llm: LlmSettings) -> LlmProvider:
    if llm.provider == "ollama":
        return OllamaProvider(llm.ollama.base_url)
    raise LlmProviderError(f"Unknown LLM provider: {llm.provider}")


def chat_options(llm: LlmSettings) -> ChatOptions:
    ollama = llm.ollama
    return ChatOptions(
        model=ollama.model,
        num_ctx=ollama.num_ctx,
        temperature=ollama.temperature,
        keep_alive=ollama.keep_alive,
    )
