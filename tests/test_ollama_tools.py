"""Ollama function calling: tools in the payload, tool_calls out of the stream,
capability detection, and tool-message wire format."""

from __future__ import annotations

import json

import httpx

from cleanwispr.llm.base import ChatMessage, ChatOptions
from cleanwispr.llm.ollama import OllamaProvider


def make_provider(handler) -> OllamaProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://127.0.0.1:11434", client=client)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "http_fetch",
            "description": "fetch a url",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    }
]

OPTIONS = ChatOptions(model="qwen3:8b")


def _chat_lines(*chunks) -> bytes:
    return "\n".join(json.dumps(c) for c in chunks).encode()


def test_chat_turn_sends_tools_and_collects_calls():
    requests = []

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=_chat_lines(
                {"message": {"role": "assistant", "content": ""}, "done": False},
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "http_fetch",
                                          "arguments": {"url": "https://x.y"}}}
                        ],
                    },
                    "done": False,
                },
                {"message": {"role": "assistant", "content": ""}, "done": True},
            ),
        )

    provider = make_provider(handler)
    turn = provider.chat_turn(
        [ChatMessage(role="user", content="fetch x.y")], OPTIONS, tools=TOOLS
    )
    assert requests[0]["tools"] == TOOLS
    assert turn.tool_calls == [
        {"function": {"name": "http_fetch", "arguments": {"url": "https://x.y"}}}
    ]
    assert turn.content == ""


def test_chat_turn_streams_content_without_calls():
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(
            200,
            content=_chat_lines(
                {"message": {"content": "hel"}, "done": False},
                {"message": {"content": "lo"}, "done": True},
            ),
        )

    seen = []
    turn = make_provider(handler).chat_turn(
        [ChatMessage(role="user", content="hi")], OPTIONS, on_content=seen.append
    )
    assert turn.content == "hello"
    assert seen == ["hel", "lo"]
    assert turn.tool_calls == []


def test_tool_messages_carry_tool_name_and_calls_on_wire():
    captured = {}

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_chat_lines({"message": {"content": "ok"},
                                                        "done": True}))

    calls = [{"function": {"name": "http_fetch", "arguments": {"url": "u"}}}]
    messages = [
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="", tool_calls=calls),
        ChatMessage(role="tool", content="result text", tool_name="http_fetch"),
    ]
    make_provider(handler).chat_turn(messages, OPTIONS)
    wire = captured["messages"]
    assert wire[1]["tool_calls"] == calls
    assert wire[2] == {"role": "tool", "content": "result text", "tool_name": "http_fetch"}


def test_supports_tools_capability_detection():
    def handler(request):
        model = json.loads(request.content)["model"]
        caps = ["completion", "tools"] if model == "qwen3:8b" else ["completion"]
        return httpx.Response(200, json={"capabilities": caps})

    provider = make_provider(handler)
    assert provider.supports_tools("qwen3:8b") is True
    assert provider.supports_tools("gemma3:4b") is False  # no tool template
    # cached: a second call must not need the transport (poison it)
    provider._client = None
    assert provider.supports_tools("qwen3:8b") is True
