"""The LLM tool loop: execution rounds, confirmation gates, budget exhaustion."""

from __future__ import annotations

import json

import pytest

from cleanwispr.llm.base import ChatMessage, ChatOptions, ChatTurn, LlmProvider
from cleanwispr.llm.toolloop import ToolConfirmRequest, run_tool_loop
from toolkit.library import ToolLibrary


def write_tool(root, tool_id, *, code=None, extra=None):
    folder = root / tool_id
    folder.mkdir(parents=True)
    manifest = {
        "id": tool_id,
        "name": tool_id,
        "description": "test tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    manifest.update(extra or {})
    (folder / "tool.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "tool.py").write_text(
        code or "def run(text: str) -> str:\n    return 'echo:' + text\n", encoding="utf-8"
    )


class ScriptedProvider(LlmProvider):
    """Returns pre-scripted ChatTurns and records what it was sent."""

    name = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []  # (messages, tools) per chat_turn call

    def is_available(self):
        return True

    def list_models(self):
        return []

    def model_info(self, model_id):
        raise NotImplementedError

    def chat(self, messages, options, on_thinking=None):
        yield from ()

    def chat_turn(self, messages, options, tools=None, on_thinking=None, on_content=None):
        self.calls.append((list(messages), tools))
        return self.turns.pop(0)


def tool_call(name, **args):
    return {"function": {"name": name, "arguments": args}}


@pytest.fixture
def library(tmp_path):
    lib = ToolLibrary(tmp_path / "tools", state_path=tmp_path / "state.json")
    write_tool(lib.root, "echo")
    lib.refresh()
    return lib


OPTIONS = ChatOptions(model="test-model")
MESSAGES = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]


def test_loop_executes_tool_and_returns_final_answer(library):
    provider = ScriptedProvider(
        [
            ChatTurn(content="", tool_calls=[tool_call("echo", text="ping")]),
            ChatTurn(content="the tool said echo:ping", tool_calls=[]),
        ]
    )
    result = run_tool_loop(provider, MESSAGES, OPTIONS, library)
    assert result == "the tool said echo:ping"
    # second turn must carry the assistant's call + the fenced tool result
    second_messages, _ = provider.calls[1]
    roles = [m.role for m in second_messages]
    assert roles[-2:] == ["assistant", "tool"]
    assert second_messages[-1].tool_name == "echo"
    assert "echo:ping" in second_messages[-1].content
    assert "DATA" in second_messages[-1].content  # spotlighting fence


def test_loop_without_calls_is_single_turn(library):
    provider = ScriptedProvider([ChatTurn(content="plain answer", tool_calls=[])])
    assert run_tool_loop(provider, MESSAGES, OPTIONS, library) == "plain answer"
    _, tools = provider.calls[0]
    assert tools and tools[0]["function"]["name"] == "echo"  # tools were offered


def test_confirm_required_and_denied(library, tmp_path):
    write_tool(library.root, "danger", extra={"confirm": True})
    library.refresh()
    provider = ScriptedProvider(
        [
            ChatTurn(content="", tool_calls=[tool_call("danger", text="x")]),
            ChatTurn(content="done without it", tool_calls=[]),
        ]
    )
    asked = []

    def deny(spec, args):
        asked.append((spec.id, args))
        return False

    result = run_tool_loop(provider, MESSAGES, OPTIONS, library, request_confirm=deny)
    assert result == "done without it"
    assert asked == [("danger", {"text": "x"})]
    tool_message = provider.calls[1][0][-1]
    assert "DENIED" in tool_message.content


def test_confirm_defaults_to_deny_without_handler(library):
    write_tool(library.root, "danger", extra={"confirm": True})
    library.refresh()
    provider = ScriptedProvider(
        [
            ChatTurn(content="", tool_calls=[tool_call("danger", text="x")]),
            ChatTurn(content="ok", tool_calls=[]),
        ]
    )
    run_tool_loop(provider, MESSAGES, OPTIONS, library)  # no request_confirm wired
    assert "DENIED" in provider.calls[1][0][-1].content


def test_confirm_all_asks_for_every_tool(library):
    library.set_config(confirm_all=True)
    provider = ScriptedProvider(
        [
            ChatTurn(content="", tool_calls=[tool_call("echo", text="a")]),
            ChatTurn(content="fine", tool_calls=[]),
        ]
    )
    asked = []
    run_tool_loop(
        provider, MESSAGES, OPTIONS, library,
        request_confirm=lambda spec, args: asked.append(spec.id) or True,
    )
    assert asked == ["echo"]
    assert "echo:a" in provider.calls[1][0][-1].content


def test_unknown_tool_reported_to_model(library):
    provider = ScriptedProvider(
        [
            ChatTurn(content="", tool_calls=[tool_call("nonexistent", text="x")]),
            ChatTurn(content="ok", tool_calls=[]),
        ]
    )
    run_tool_loop(provider, MESSAGES, OPTIONS, library)
    assert "Unknown or disabled tool" in provider.calls[1][0][-1].content


def test_round_budget_forces_final_answer(library):
    library.set_config(max_rounds=2)
    looping = ChatTurn(content="", tool_calls=[tool_call("echo", text="again")])
    provider = ScriptedProvider(
        [looping, looping, ChatTurn(content="forced answer", tool_calls=[])]
    )
    result = run_tool_loop(provider, MESSAGES, OPTIONS, library)
    assert result == "forced answer"
    final_messages, final_tools = provider.calls[-1]
    assert final_tools is None  # tools taken away on the forced turn
    assert "Tool budget exhausted" in final_messages[-1].content


def test_authoring_guide_injected_when_create_tool_armed(library):
    write_tool(
        library.root,
        "create-tool",
        extra={
            "native": "create_tool",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    )
    library.refresh()
    provider = ScriptedProvider([ChatTurn(content="hi", tool_calls=[])])
    run_tool_loop(provider, MESSAGES, OPTIONS, library)
    first_messages, _ = provider.calls[0]
    assert first_messages[0].role == "system"
    assert "TOOL AUTHORING REFERENCE" in first_messages[1].content


def test_string_encoded_arguments_are_parsed(library):
    provider = ScriptedProvider(
        [
            ChatTurn(
                content="",
                tool_calls=[{"function": {"name": "echo", "arguments": '{"text": "ping"}'}}],
            ),
            ChatTurn(content="ok", tool_calls=[]),
        ]
    )
    run_tool_loop(provider, MESSAGES, OPTIONS, library)
    assert "echo:ping" in provider.calls[1][0][-1].content


def test_confirm_request_wait_times_out_to_deny():
    request = ToolConfirmRequest(tool=None, args={})
    assert request.wait(timeout_s=0.05) is False
    request.resolve(True)
    assert request.wait(timeout_s=0.05) is True
