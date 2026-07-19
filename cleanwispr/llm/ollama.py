"""Ollama provider: localhost REST API (/api/tags, /api/show, /api/chat).

The httpx client is injectable so tests can use MockTransport.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator

import httpx

from cleanwispr.llm.base import (
    ChatMessage,
    ChatOptions,
    LlmModelInfo,
    LlmProvider,
    LlmProviderError,
)

log = logging.getLogger(__name__)

_CHAT_TIMEOUT = httpx.Timeout(connect=5, read=600, write=30, pool=30)

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-:/]*$")
_COMMAND_RE = re.compile(r"^(?:ollama\s+(?P<verb>pull|run)\s+)?(?P<model>\S+)\s*$", re.IGNORECASE)


def parse_pull_command(text: str, *, interpret_run_as_pull: bool = True) -> tuple[str, str | None]:
    """Extract a model name from pasted text ('ollama pull x', 'ollama run x', or
    a bare name). Returns (model, notice). NOTHING is ever executed — the name is
    validated and downloaded via Ollama's HTTP pull API.
    """
    match = _COMMAND_RE.match(text.strip())
    if not match:
        raise ValueError("Paste an Ollama command or a model name, e.g. 'ollama pull qwen3:8b'")
    verb = (match.group("verb") or "pull").lower()
    model = match.group("model")
    if not _MODEL_NAME_RE.match(model):
        raise ValueError(f"'{model}' does not look like a valid Ollama model name")
    notice = None
    if verb == "run":
        if not interpret_run_as_pull:
            raise ValueError(
                "That is an 'ollama run' command. Enable “treat 'run' as 'pull'” "
                "below, or paste a pull command."
            )
        notice = f"'ollama run' interpreted as pull — downloading '{model}'"
    return model, notice


class OllamaProvider(LlmProvider):
    name = "ollama"

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434", client: httpx.Client | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=_CHAT_TIMEOUT)
        self._thinking_cache: dict[str, bool] = {}

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _unreachable(self, exc: Exception) -> LlmProviderError:
        return LlmProviderError(
            f"Ollama is not reachable at {self._base} — is it running? ({exc})"
        )

    def is_available(self) -> bool:
        try:
            return self._client.get(self._url("/api/version"), timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def server_version(self) -> str:
        try:
            response = self._client.get(self._url("/api/version"), timeout=5)
            response.raise_for_status()
            return response.json().get("version", "?")
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    def list_models(self) -> list[LlmModelInfo]:
        try:
            response = self._client.get(self._url("/api/tags"))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        models = []
        for item in response.json().get("models", []):
            details = item.get("details") or {}
            models.append(
                LlmModelInfo(
                    id=item["name"],
                    label=item["name"],
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                )
            )
        return models

    def model_info(self, model_id: str) -> LlmModelInfo:
        try:
            response = self._client.post(self._url("/api/show"), json={"model": model_id})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        payload = response.json()
        details = payload.get("details") or {}
        context_length = None
        for key, value in (payload.get("model_info") or {}).items():
            if key.endswith(".context_length"):
                context_length = int(value)
                break
        return LlmModelInfo(
            id=model_id,
            label=model_id,
            parameter_size=details.get("parameter_size"),
            quantization=details.get("quantization_level"),
            context_length=context_length,
        )

    def is_model_loaded(self, model_id: str) -> bool | None:
        """/api/ps lists models currently resident in memory."""
        try:
            response = self._client.get(self._url("/api/ps"), timeout=5)
            response.raise_for_status()
        except httpx.HTTPError:
            return None  # can't tell — don't block the edit on it
        loaded = set()
        for item in response.json().get("models", []):
            loaded.add(item.get("name"))
            loaded.add(item.get("model"))
        return model_id in loaded

    def load_model(self, model_id: str, keep_alive: str = "10m") -> None:
        """An empty chat request makes Ollama load the model and return when done."""
        try:
            response = self._client.post(
                self._url("/api/chat"),
                json={"model": model_id, "messages": [], "stream": False,
                      "keep_alive": keep_alive},
                timeout=600,
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise LlmProviderError(
                f"Ollama could not load {model_id}: {response.text[:200]}"
            )

    def pull(self, model_id: str, *, progress=None, cancel=None) -> None:
        """Download a model via /api/pull (streaming progress). No shell involved."""
        payload = {"model": model_id, "stream": True}
        try:
            with self._client.stream(
                "POST", self._url("/api/pull"), json=payload,
                timeout=httpx.Timeout(connect=5, read=3600, write=30, pool=30),
            ) as response:
                if response.status_code != 200:
                    body = response.read().decode(errors="replace")[:300]
                    raise LlmProviderError(f"Ollama pull failed ({response.status_code}): {body}")
                for line in response.iter_lines():
                    if cancel is not None and cancel.is_set():
                        return
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LlmProviderError(f"Ollama pull failed: {data['error']}")
                    total = data.get("total")
                    if progress is not None and total:
                        progress(data.get("completed", 0), total)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    def supports_thinking(self, model_id: str) -> bool:
        """Does the model expose reasoning tokens? (/api/show capabilities)."""
        cached = self._thinking_cache.get(model_id)
        if cached is not None:
            return cached
        try:
            response = self._client.post(self._url("/api/show"), json={"model": model_id})
            response.raise_for_status()
            result = "thinking" in (response.json().get("capabilities") or [])
        except httpx.HTTPError:
            result = False
        self._thinking_cache[model_id] = result
        return result

    def chat(
        self,
        messages: list[ChatMessage],
        options: ChatOptions,
        on_thinking: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        if not options.model:
            raise LlmProviderError(
                "No Ollama model selected — pick one in Settings → Editor (LLM)."
            )
        payload = {
            "model": options.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "keep_alive": options.keep_alive,
            "options": {"num_ctx": options.num_ctx, "temperature": options.temperature},
        }
        if on_thinking is not None and self.supports_thinking(options.model):
            payload["think"] = True
        try:
            with self._client.stream("POST", self._url("/api/chat"), json=payload) as response:
                if response.status_code != 200:
                    body = response.read().decode(errors="replace")[:300]
                    raise LlmProviderError(f"Ollama error {response.status_code}: {body}")
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LlmProviderError(f"Ollama error: {chunk['error']}")
                    message = chunk.get("message") or {}
                    thinking = message.get("thinking")
                    if thinking and on_thinking is not None:
                        on_thinking(thinking)
                    content = message.get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
