"""LlmProvider — the seam every local LLM server integration implements.

v1 ships Ollama; an OpenAI-compatible provider later covers LM Studio,
llama.cpp llama-server, vLLM, Jan behind the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass


@dataclass(slots=True)
class LlmModelInfo:
    id: str  # e.g. "qwen2.5:7b-instruct"
    label: str
    parameter_size: str | None = None  # e.g. "7.6B"
    quantization: str | None = None  # e.g. "Q4_K_M"
    context_length: int | None = None


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(slots=True)
class ChatOptions:
    model: str
    num_ctx: int = 8192
    temperature: float = 0.2
    keep_alive: str = "10m"


class LlmProviderError(RuntimeError):
    """Raised for connection failures / bad responses; message is user-presentable."""


class LlmProvider(ABC):
    """Contract: all methods are blocking and called from worker threads only.
    Implementations must raise LlmProviderError with an actionable message
    (e.g. 'Ollama is not running at http://127.0.0.1:11434')."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap health check (server reachable?)."""

    @abstractmethod
    def list_models(self) -> list[LlmModelInfo]:
        """Installed models, for auto-discovery in the settings UI."""

    @abstractmethod
    def model_info(self, model_id: str) -> LlmModelInfo:
        """Detailed info for one model (context length, quantization...)."""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        options: ChatOptions,
        on_thinking: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        """Stream response text chunks. Callers join them; injection happens once
        complete. For reasoning models, thinking tokens stream to on_thinking
        instead of the result (providers without the concept ignore it)."""

    def is_model_loaded(self, model_id: str) -> bool | None:
        """Is the model resident in memory right now? None = provider can't tell."""
        return None

    def load_model(self, model_id: str, keep_alive: str = "10m") -> None:
        """Block until the model is loaded (no-op for providers without the concept)."""
        return None  # deliberate non-abstract default
