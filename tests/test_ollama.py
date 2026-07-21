import json
from threading import Event

import httpx
import pytest

from cleanwispr.llm.base import ChatMessage, ChatOptions, LlmProvider, LlmProviderError
from cleanwispr.llm.ollama import OllamaProvider, parse_pull_command


@pytest.mark.parametrize(
    ("text", "expected_model", "has_notice"),
    [
        ("ollama pull qwen3:8b", "qwen3:8b", False),
        ("ollama run llama3.2", "llama3.2", True),  # run converted, with notice
        ("gemma4:e4b", "gemma4:e4b", False),  # bare model name
        ("  OLLAMA PULL hf.co/user/model:Q4_K_M  ", "hf.co/user/model:Q4_K_M", False),
    ],
)
def test_parse_pull_command(text, expected_model, has_notice):
    model, notice = parse_pull_command(text)
    assert model == expected_model
    assert (notice is not None) is has_notice


def test_parse_pull_rejects_run_when_disabled():
    with pytest.raises(ValueError, match="run"):
        parse_pull_command("ollama run qwen3:8b", interpret_run_as_pull=False)


@pytest.mark.parametrize("bad", ["", "ollama pull", "ollama rm x; calc.exe", "pull a b c"])
def test_parse_pull_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_pull_command(bad)


def test_pull_streams_progress():
    def handler(request):
        assert request.url.path == "/api/pull"
        assert json.loads(request.content)["model"] == "qwen3:8b"
        lines = [
            json.dumps({"status": "pulling", "total": 1000, "completed": 250}),
            json.dumps({"status": "pulling", "total": 1000, "completed": 1000}),
            json.dumps({"status": "success"}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())

    provider = make_provider(handler)
    seen = []
    provider.pull("qwen3:8b", progress=lambda c, t: seen.append((c, t)))
    assert seen == [(250, 1000), (1000, 1000)]


def test_pull_error_raises():
    def handler(request):
        return httpx.Response(
            200, content=json.dumps({"error": "pull model manifest: not found"}).encode()
        )

    with pytest.raises(LlmProviderError, match="not found"):
        make_provider(handler).pull("nope:1b")


def test_pull_stops_when_cancelled():
    def handler(request):
        lines = [json.dumps({"total": 1000, "completed": i}) for i in (100, 200, 300, 400)]
        return httpx.Response(200, content="\n".join(lines).encode())

    cancel = Event()
    seen = []

    def progress(completed, total):
        seen.append(completed)
        cancel.set()  # cancel after the first progress line

    make_provider(handler).pull("qwen3:8b", progress=progress, cancel=cancel)
    assert seen == [100]  # stopped instead of draining all four lines


def test_delete_model_sends_delete_request():
    seen = {}

    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/api/delete"
        seen.update(json.loads(request.content))
        return httpx.Response(200)

    make_provider(handler).delete_model("gemma3:4b")
    assert seen["model"] == "gemma3:4b"


def test_delete_model_tolerates_missing():
    make_provider(lambda request: httpx.Response(404)).delete_model("gone:1b")  # no raise


def test_ollama_advertises_install_and_a_catalog():
    provider = OllamaProvider()
    assert provider.supports_install is True
    catalog = provider.catalog()
    assert catalog and all(m.size_gb > 0 and m.min_memory_gb > 0 for m in catalog)


def test_catalog_spans_families_beyond_gemma_and_marks_recommendations():
    catalog = OllamaProvider().catalog()
    families = {m.family for m in catalog}
    # the search list must offer more than just gemma
    assert {"gemma", "qwen", "llama", "mistral"} <= families
    assert len(families) >= 5
    recommended = [m for m in catalog if m.recommended]
    assert recommended and len(recommended) < len(catalog)  # a vetted subset


def test_installable_model_search_matches_id_family_and_description():
    catalog = OllamaProvider().catalog()
    by_id = {m.id: m for m in catalog}
    llama = next(m for m in catalog if m.family == "llama")
    assert llama.matches("LLAMA")  # case-insensitive on family
    assert llama.matches(llama.id)  # matches its own id
    assert by_id["deepseek-r1:7b"].matches("reasoning")  # matches description
    assert not by_id["gemma3:1b"].matches("mistral")
    assert by_id["gemma3:1b"].matches("")  # empty query matches everything


def test_base_provider_defaults_reject_install():
    class Bare(LlmProvider):
        name = "bare"

        def is_available(self):
            return True

        def list_models(self):
            return []

        def model_info(self, model_id):
            raise NotImplementedError

        def chat(self, messages, options, on_thinking=None):
            yield ""

    bare = Bare()
    assert bare.supports_install is False
    assert bare.catalog() == []
    with pytest.raises(LlmProviderError):
        bare.pull("x")
    with pytest.raises(LlmProviderError):
        bare.delete_model("x")


def make_provider(handler) -> OllamaProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://127.0.0.1:11434", client=client)


def test_list_models():
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen2.5:7b",
                        "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
                    },
                    {"name": "llama3.2:3b", "details": {}},
                ]
            },
        )

    models = make_provider(handler).list_models()
    assert [m.id for m in models] == ["qwen2.5:7b", "llama3.2:3b"]
    assert models[0].parameter_size == "7.6B"
    assert models[0].quantization == "Q4_K_M"


def test_model_info_context_length():
    def handler(request):
        assert request.url.path == "/api/show"
        return httpx.Response(
            200,
            json={
                "details": {"parameter_size": "7.6B"},
                "model_info": {"qwen2.arch": "x", "qwen2.context_length": 32768},
            },
        )

    info = make_provider(handler).model_info("qwen2.5:7b")
    assert info.context_length == 32768


def test_chat_streams_chunks():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen2.5:7b"
        assert payload["options"] == {"num_ctx": 8192, "temperature": 0.2}
        assert payload["messages"][0]["role"] == "system"
        lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())

    provider = make_provider(handler)
    chunks = list(
        provider.chat(
            [ChatMessage("system", "sys"), ChatMessage("user", "hi")],
            ChatOptions(model="qwen2.5:7b"),
        )
    )
    assert "".join(chunks) == "Hello world"


def test_chat_streams_thinking_separately():
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        payload = json.loads(request.content)
        assert payload["think"] is True  # enabled because the model supports it
        lines = [
            json.dumps({"message": {"thinking": "Let me consider..."}, "done": False}),
            json.dumps({"message": {"thinking": " the second sentence."}, "done": False}),
            json.dumps({"message": {"content": "Edited."}, "done": False}),
            json.dumps({"message": {}, "done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())

    provider = make_provider(handler)
    thoughts = []
    chunks = list(
        provider.chat(
            [ChatMessage("user", "hi")],
            ChatOptions(model="qwen3:8b"),
            on_thinking=thoughts.append,
        )
    )
    assert "".join(chunks) == "Edited."  # thinking not mixed into the result
    assert "".join(thoughts) == "Let me consider... the second sentence."


def test_chat_skips_think_flag_for_non_thinking_models():
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        payload = json.loads(request.content)
        assert "think" not in payload  # would 400 on non-thinking models
        line = json.dumps({"message": {"content": "ok"}, "done": True})
        return httpx.Response(200, content=line.encode())

    provider = make_provider(handler)
    chunks = list(
        provider.chat(
            [ChatMessage("user", "hi")],
            ChatOptions(model="gemma4:e4b"),
            on_thinking=lambda t: None,
        )
    )
    assert chunks == ["ok"]


def test_supports_vision_from_capabilities():
    calls = []

    def handler(request):
        assert request.url.path == "/api/show"
        calls.append(1)
        return httpx.Response(200, json={"capabilities": ["completion", "vision"]})

    provider = make_provider(handler)
    assert provider.supports_vision("llava:7b") is True
    assert provider.supports_vision("llava:7b") is True  # cached — no second request
    assert len(calls) == 1


def test_supports_vision_false_for_text_model():
    provider = make_provider(
        lambda request: httpx.Response(200, json={"capabilities": ["completion"]})
    )
    assert provider.supports_vision("gemma3:4b") is False


def test_supports_vision_capabilities_are_authoritative():
    # a populated capabilities list without vision is definitive — a stray
    # model_info key must NOT flip it to True (that would 500 on a text model)
    provider = make_provider(
        lambda request: httpx.Response(
            200,
            json={"capabilities": ["completion"], "model_info": {"gemma3.vision.foo": 1}},
        )
    )
    assert provider.supports_vision("gemma3:1b") is False


def test_supports_vision_via_model_info_when_capabilities_missing():
    # older Ollama: no "capabilities" field, but the GGUF carries a .vision. KV
    provider = make_provider(
        lambda request: httpx.Response(200, json={"model_info": {"clip.vision.image_size": 336}})
    )
    assert provider.supports_vision("moondream") is True


def test_supports_vision_via_clip_family_when_capabilities_missing():
    provider = make_provider(
        lambda request: httpx.Response(200, json={"details": {"families": ["llama", "clip"]}})
    )
    assert provider.supports_vision("llava:7b") is True


def test_chat_maps_image_500_to_friendly_error():
    def handler(request):
        return httpx.Response(
            500, content=b"Failed to process inputs: this model is missing data "
            b"required for image input"
        )

    provider = make_provider(handler)
    messages = [ChatMessage("user", "describe", images=["QkFTRTY0"])]
    with pytest.raises(LlmProviderError, match="without image support"):
        list(provider.chat(messages, ChatOptions(model="gemma3:1b")))


def test_chat_sends_images_on_message():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["messages"][-1]["images"] == ["QkFTRTY0"]  # base64 forwarded verbatim
        line = json.dumps({"message": {"content": "a cat"}, "done": True})
        return httpx.Response(200, content=line.encode())

    provider = make_provider(handler)
    messages = [ChatMessage("user", "what is this?", images=["QkFTRTY0"])]
    assert list(provider.chat(messages, ChatOptions(model="llava:7b"))) == ["a cat"]


def test_chat_omits_images_key_when_absent():
    def handler(request):
        payload = json.loads(request.content)
        assert "images" not in payload["messages"][0]  # text-only message stays clean
        line = json.dumps({"message": {"content": "ok"}, "done": True})
        return httpx.Response(200, content=line.encode())

    provider = make_provider(handler)
    list(provider.chat([ChatMessage("user", "hi")], ChatOptions(model="x")))


def test_chat_without_model_raises():
    provider = make_provider(lambda request: httpx.Response(200))
    with pytest.raises(LlmProviderError, match="No Ollama model selected"):
        list(provider.chat([ChatMessage("user", "hi")], ChatOptions(model="")))


def test_chat_error_payload_raises():
    def handler(request):
        return httpx.Response(200, content=json.dumps({"error": "model not found"}).encode())

    provider = make_provider(handler)
    with pytest.raises(LlmProviderError, match="model not found"):
        list(provider.chat([ChatMessage("user", "hi")], ChatOptions(model="x")))


def test_is_model_loaded_via_ps():
    def handler(request):
        assert request.url.path == "/api/ps"
        return httpx.Response(200, json={"models": [{"name": "gemma4:e4b", "model": "gemma4:e4b"}]})

    provider = make_provider(handler)
    assert provider.is_model_loaded("gemma4:e4b") is True
    assert provider.is_model_loaded("qwen3-coder:30b") is False


def test_is_model_loaded_unknown_when_ps_fails():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert make_provider(handler).is_model_loaded("gemma4:e4b") is None


def test_load_model_sends_empty_chat():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "done_reason": "load"})

    make_provider(handler).load_model("gemma4:e4b", keep_alive="30m")
    assert seen["model"] == "gemma4:e4b"
    assert seen["messages"] == []
    assert seen["keep_alive"] == "30m"


def test_load_model_error_raises():
    def handler(request):
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(LlmProviderError, match="could not load"):
        make_provider(handler).load_model("nope:1b")


def test_unreachable_server_raises_actionable_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    provider = make_provider(handler)
    with pytest.raises(LlmProviderError, match="is it running"):
        provider.list_models()
