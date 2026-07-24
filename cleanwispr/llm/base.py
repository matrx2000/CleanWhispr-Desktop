"""LlmProvider — the seam every local LLM server integration implements.

v1 ships Ollama; an OpenAI-compatible provider later covers LM Studio,
llama.cpp llama-server, vLLM, Jan behind the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Event


@dataclass(slots=True)
class LlmModelInfo:
    id: str  # e.g. "qwen2.5:7b-instruct"
    label: str
    parameter_size: str | None = None  # e.g. "7.6B"
    quantization: str | None = None  # e.g. "Q4_K_M"
    context_length: int | None = None


@dataclass(frozen=True, slots=True)
class InstallableModel:
    """One model a provider can download on demand, described richly enough for
    a hardware-aware recommendation (see llm.hardware.recommend_from_catalog).

    Providers ship a curated catalog so a non-technical user can pick a model
    from a list and have it fetched — no terminal, no knowing model names. The
    metadata is provider-neutral so the same recommender works for any backend.
    """

    id: str  # provider-native pull id, e.g. "gemma3:4b"
    label: str  # human name, e.g. "Gemma 3 4B"
    description: str  # one-line "what it's good for"
    size_gb: float  # approximate download size
    min_memory_gb: float  # GPU/unified/RAM needed to run it comfortably
    family: str = ""  # "gemma", "qwen", "llama" — for grouping/search
    recommended: bool = False  # a vetted default the recommender may pick

    def matches(self, query: str) -> bool:
        """Case-insensitive search over id/label/family/description."""
        q = query.strip().lower()
        if not q:
            return True
        fields = (self.id, self.label, self.family, self.description)
        return any(q in field.lower() for field in fields)


# progress(completed_bytes, total_bytes); total may be 0 before it's known
PullProgressFn = Callable[[int, int], None]


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    images: list[str] | None = None  # base64-encoded images (vision models only)
    # function calling (tools): an assistant message may carry the calls it
    # made; a role="tool" message carries one call's result under its name
    tool_calls: list[dict] | None = None
    tool_name: str | None = None


@dataclass(slots=True)
class ChatTurn:
    """One complete model response: streamed text plus any tool calls the
    model emitted. Wire format of `tool_calls` follows Ollama/OpenAI:
    [{"function": {"name": str, "arguments": dict}}, ...]."""

    content: str
    tool_calls: list[dict]


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

    def supports_vision(self, model_id: str) -> bool:
        """Can this model accept images (multimodal)? Providers that can't tell
        return False so callers fall back to text-only."""
        return False

    def supports_tools(self, model_id: str) -> bool:
        """Does the model do function calling? False → callers chat without
        tools (e.g. gemma3 has no tool template even though gemma4 does)."""
        return False

    def chat_turn(
        self,
        messages: list[ChatMessage],
        options: ChatOptions,
        tools: list[dict] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatTurn:
        """One full response turn, optionally offering `tools` (Ollama/OpenAI
        function definitions). Content still streams via on_content. The
        default consumes chat() and never returns tool calls, so providers
        without function calling keep working unchanged."""
        chunks: list[str] = []
        for chunk in self.chat(messages, options, on_thinking=on_thinking):
            chunks.append(chunk)
            if on_content is not None:
                on_content(chunk)
        return ChatTurn(content="".join(chunks), tool_calls=[])

    def is_model_loaded(self, model_id: str) -> bool | None:
        """Is the model resident in memory right now? None = provider can't tell."""
        return None

    def load_model(self, model_id: str, keep_alive: str = "10m") -> None:
        """Block until the model is loaded (no-op for providers without the concept)."""
        return None  # deliberate non-abstract default

    # --- in-app model installation (optional capability) ---

    supports_install: bool = False  # can this provider download models on demand?

    def catalog(self) -> list[InstallableModel]:
        """Curated models the user can install from within the app. Empty when
        the provider can't download models (e.g. a remote endpoint you point at
        an already-served model)."""
        return []

    def pull(
        self,
        model_id: str,
        *,
        progress: PullProgressFn | None = None,
        cancel: Event | None = None,
    ) -> None:
        """Download/install a model, reporting byte progress and honouring a
        cancel Event. Providers without the capability raise LlmProviderError."""
        raise LlmProviderError(
            f"{self.name} cannot download models from inside the app — install "
            "the model in that tool first."
        )

    def delete_model(self, model_id: str) -> None:
        """Remove an installed model. Providers without the capability raise."""
        raise LlmProviderError(f"{self.name} cannot delete models from inside the app.")
