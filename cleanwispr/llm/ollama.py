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
    InstallableModel,
    LlmModelInfo,
    LlmProvider,
    LlmProviderError,
)

log = logging.getLogger(__name__)

_CHAT_TIMEOUT = httpx.Timeout(connect=5, read=600, write=30, pool=30)

# Models from the Ollama library. Ollama has no public "browse the library" API,
# so — like OpenWhispr's static model registry — we ship a hand-picked list the
# user can search/filter (any model can still be installed by exact name via the
# pull box). size_gb ≈ the Q4 download; min_memory_gb is the GPU/unified/RAM
# headroom to run it smoothly (weights + KV cache + overhead).
#
# `recommended=True` marks the vetted general-purpose instruct models the
# hardware recommender may auto-pick; the rest are for browsing.
_CATALOG: tuple[InstallableModel, ...] = (
    # --- Gemma (Google) ---
    InstallableModel(
        "gemma3:1b", "Gemma 3 1B", "Tiny and fast — runs on almost anything",
        size_gb=0.8, min_memory_gb=2.0, family="gemma", recommended=True,
    ),
    InstallableModel(
        "gemma3:4b", "Gemma 3 4B", "Great all-rounder for most PCs",
        size_gb=3.3, min_memory_gb=5.0, family="gemma", recommended=True,
    ),
    InstallableModel(
        "gemma4:12b", "Gemma 4 12B", "High quality — needs a capable GPU",
        size_gb=8.1, min_memory_gb=10.0, family="gemma", recommended=True,
    ),
    InstallableModel(
        "gemma4:26b", "Gemma 4 26B", "Excellent quality for high-VRAM GPUs",
        size_gb=16.0, min_memory_gb=16.0, family="gemma", recommended=True,
    ),
    InstallableModel(
        "gemma4:31b", "Gemma 4 31B", "Best quality — for 24 GB+ GPUs / big Macs",
        size_gb=19.0, min_memory_gb=23.0, family="gemma", recommended=True,
    ),
    # --- Qwen (Alibaba) ---
    InstallableModel(
        "qwen3:1.7b", "Qwen 3 1.7B", "Very small, capable for its size",
        size_gb=1.4, min_memory_gb=3.0, family="qwen",
    ),
    InstallableModel(
        "qwen3:4b", "Qwen 3 4B", "Lightweight and sharp at following instructions",
        size_gb=2.6, min_memory_gb=5.0, family="qwen",
    ),
    InstallableModel(
        "qwen3:8b", "Qwen 3 8B", "Strong instruction-following at a modest size",
        size_gb=5.2, min_memory_gb=7.0, family="qwen", recommended=True,
    ),
    InstallableModel(
        "qwen3:14b", "Qwen 3 14B", "High quality, needs a good GPU",
        size_gb=9.3, min_memory_gb=12.0, family="qwen",
    ),
    InstallableModel(
        "qwen2.5:7b", "Qwen 2.5 7B", "Proven all-rounder, good for editing",
        size_gb=4.7, min_memory_gb=7.0, family="qwen",
    ),
    # --- Llama (Meta) ---
    InstallableModel(
        "llama3.2:1b", "Llama 3.2 1B", "Tiny Meta model for low-end machines",
        size_gb=1.3, min_memory_gb=2.5, family="llama",
    ),
    InstallableModel(
        "llama3.2:3b", "Llama 3.2 3B", "Small and fast, solid general use",
        size_gb=2.0, min_memory_gb=4.0, family="llama",
    ),
    InstallableModel(
        "llama3.1:8b", "Llama 3.1 8B", "Popular general-purpose model",
        size_gb=4.9, min_memory_gb=7.0, family="llama",
    ),
    # --- Mistral ---
    InstallableModel(
        "mistral:7b", "Mistral 7B", "Fast, well-rounded classic",
        size_gb=4.1, min_memory_gb=6.0, family="mistral",
    ),
    InstallableModel(
        "mistral-nemo:12b", "Mistral Nemo 12B", "Larger Mistral, strong quality",
        size_gb=7.1, min_memory_gb=10.0, family="mistral",
    ),
    # --- Phi (Microsoft) ---
    InstallableModel(
        "phi4:14b", "Phi-4 14B", "Microsoft's capable reasoning model",
        size_gb=9.1, min_memory_gb=12.0, family="phi",
    ),
    InstallableModel(
        "phi3:3.8b", "Phi-3 Mini", "Small Microsoft model, efficient",
        size_gb=2.2, min_memory_gb=4.0, family="phi",
    ),
    # --- DeepSeek (reasoning) ---
    InstallableModel(
        "deepseek-r1:7b", "DeepSeek-R1 7B", "Reasoning model — shows its thinking",
        size_gb=4.7, min_memory_gb=7.0, family="deepseek",
    ),
    InstallableModel(
        "deepseek-r1:8b", "DeepSeek-R1 8B", "Reasoning model, a bit stronger",
        size_gb=5.2, min_memory_gb=7.5, family="deepseek",
    ),
)

# Vision detection. On Ollama >= 0.6.4 the /api/show `capabilities` array is
# authoritative; these are pre-0.6.4 fallbacks used only when it's absent/empty,
# mirroring how Ollama itself derives the "vision" capability from the GGUF.
_VISION_MODEL_INFO_MARKER = ".vision."  # e.g. clip.vision.block_count, gemma3.vision.*
_VISION_ARCH_FAMILIES = frozenset({"clip", "mllama"})

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
    supports_install = True

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434", client: httpx.Client | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=_CHAT_TIMEOUT)
        self._thinking_cache: dict[str, bool] = {}
        self._vision_cache: dict[str, bool] = {}

    def catalog(self) -> list[InstallableModel]:
        return list(_CATALOG)

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
        # re-detect vision against the warm model — capabilities can misreport
        # for ~30s during a cold start (ollama #12950/#13459)
        self._vision_cache.pop(model_id, None)
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

    def delete_model(self, model_id: str) -> None:
        """Remove an installed model via /api/delete."""
        try:
            response = self._client.request(
                "DELETE", self._url("/api/delete"), json={"model": model_id}, timeout=30
            )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code not in (200, 404):
            raise LlmProviderError(
                f"Ollama could not delete {model_id}: {response.text[:200]}"
            )

    def supports_thinking(self, model_id: str) -> bool:
        """Does the model expose reasoning tokens? (/api/show capabilities)."""
        return self._has_capability(model_id, "thinking", self._thinking_cache)

    def _has_capability(self, model_id: str, capability: str, cache: dict[str, bool]) -> bool:
        cached = cache.get(model_id)
        if cached is not None:
            return cached
        try:
            response = self._client.post(self._url("/api/show"), json={"model": model_id})
            response.raise_for_status()
            result = capability in (response.json().get("capabilities") or [])
        except httpx.HTTPError:
            result = False
        cache[model_id] = result
        return result

    def supports_vision(self, model_id: str) -> bool:
        """Can this model accept images via /api/chat `images`?

        Deliberately low false-positive: sending images to a text-only Ollama
        model is a hard HTTP 500 ('this model is missing data required for image
        input'), not a silent no-op, so we return True only when the *pulled blob*
        actually carries a vision projector. Signals, most-authoritative first:
          1. capabilities[] contains "vision" or "image"  (Ollama >= 0.6.4)
          2. any model_info key with '.vision.'            (old-server fallback)
          3. details.family(-ies): 'clip' or 'mllama'      (old-server fallback)
        """
        cached = self._vision_cache.get(model_id)
        if cached is not None:
            return cached
        result = self._detect_vision(model_id)
        self._vision_cache[model_id] = result
        return result

    def _detect_vision(self, model_id: str) -> bool:
        try:
            response = self._client.post(
                self._url("/api/show"), json={"model": model_id, "verbose": True}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("vision-detect %s: /api/show failed (%s)", model_id, exc)
            return False

        caps = payload.get("capabilities") or []
        details = payload.get("details") or {}
        model_info = payload.get("model_info") or {}

        if "vision" in caps or "image" in caps:  # authoritative on Ollama >= 0.6.4
            log.info("vision-detect %s: True (capabilities=%s)", model_id, caps)
            return True
        # a populated capabilities list without vision/image is definitive — do NOT
        # override with heuristics (that is exactly what triggers the runner 500)
        if caps:
            log.info("vision-detect %s: False (capabilities=%s)", model_id, caps)
            return False

        # pre-0.6.4 fallbacks (no capabilities field at all)
        if any(_VISION_MODEL_INFO_MARKER in key for key in model_info):
            log.info("vision-detect %s: True (model_info .vision. key)", model_id)
            return True
        families = {*(details.get("families") or []), details.get("family")}
        if families & _VISION_ARCH_FAMILIES:
            log.info("vision-detect %s: True (family=%s)", model_id, families)
            return True

        log.info(
            "vision-detect %s: False (no capabilities; family=%s) — run "
            "'ollama show %s' to confirm",
            model_id, details.get("family"), model_id,
        )
        return False

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
        wire_messages = []
        for message in messages:
            entry = {"role": message.role, "content": message.content}
            if message.images:  # Ollama accepts base64 images per message (vision models)
                entry["images"] = message.images
            wire_messages.append(entry)
        payload = {
            "model": options.model,
            "messages": wire_messages,
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
                    if response.status_code == 500 and (
                        "image input" in body or "missing data required for image" in body
                    ):
                        raise LlmProviderError(
                            f"{options.model} was pulled without image support — it can't "
                            "read images. Use a vision model (e.g. gemma3:4b, llava, "
                            "llama3.2-vision) for image edits."
                        )
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
