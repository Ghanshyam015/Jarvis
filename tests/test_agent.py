
from jarvis.agent import Jarvis
from jarvis.config import Settings
from jarvis.llm import LLMError, Provider


def make_jarvis(tmp_path, provider=None, **kwargs):
    settings = Settings.from_env(api_key="", data_dir=tmp_path, offline=True, **kwargs)
    jarvis = Jarvis(settings)
    if provider is not None:
        jarvis.provider = provider
    return jarvis


class ScriptedProvider(Provider):
    """Replays canned event lists, one per step."""

    name = "scripted"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def stream_chat(self, messages, tools=None):
        self.calls.append((list(messages), tools))
        script = self.scripts.pop(0) if self.scripts else [
            {"type": "text", "text": "done"},
            {"type": "done", "finish_reason": "stop"},
        ]
        yield from script


def test_offline_stub_produces_text(tmp_path):
    jarvis = make_jarvis(tmp_path)
    assert jarvis.settings.uses_stub
    events = list(jarvis.stream_reply([{"role": "user", "content": "hello there"}]))
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert "offline" in text.lower()
    assert events[-1]["type"] == "done"


def test_offline_stub_calls_time_tool(tmp_path):
    jarvis = make_jarvis(tmp_path)
    events = list(jarvis.stream_reply([{"role": "user", "content": "what time is it"}]))
    kinds = [e["type"] for e in events]
    assert "tool_start" in kinds and "tool_result" in kinds
    assert events[-1]["type"] == "done"


def test_tool_roundtrip_feeds_result_back_to_model(tmp_path):
    provider = ScriptedProvider(
        [
            [
                {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "call_1",
                    "name": "calculate",
                    "arguments": '{"expression": "6*7"}',
                },
                {"type": "done", "finish_reason": "tool_calls"},
            ],
            [
                {"type": "text", "text": "It is "},
                {"type": "text", "text": "42."},
                {"type": "done", "finish_reason": "stop"},
            ],
        ]
    )
    jarvis = make_jarvis(tmp_path, provider=provider)
    events = list(jarvis.stream_reply([{"role": "user", "content": "6*7?"}]))

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert "42" in tool_result["result"]
    assert events[-1]["text"] == "It is 42."

    # second model call must include the assistant tool_call + tool message
    second_messages, _ = provider.calls[1]
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_messages)
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["tool_call_id"] == "call_1"


def test_max_steps_is_enforced(tmp_path):
    loop = [
        [
            {
                "type": "tool_call_delta",
                "index": 0,
                "id": "c",
                "name": "get_current_time",
                "arguments": "{}",
            },
            {"type": "done", "finish_reason": "tool_calls"},
        ]
    ]
    jarvis = make_jarvis(tmp_path, provider=ScriptedProvider(loop * 5), max_steps=2)
    events = list(jarvis.stream_reply([{"role": "user", "content": "loop please"}]))
    assert events[-1]["finish_reason"] == "max_steps"
    assert events[-1]["steps"] == 2


def test_provider_error_is_reported_not_raised(tmp_path):
    class Broken(Provider):
        name = "broken"

        def stream_chat(self, messages, tools=None):
            raise LLMError("boom")
            yield  # pragma: no cover

    jarvis = make_jarvis(tmp_path, provider=Broken())
    events = list(jarvis.stream_reply([{"role": "user", "content": "hi"}]))
    assert events[0]["type"] == "error"
    assert events[-1]["finish_reason"] == "error"


def test_memory_is_injected_into_system_prompt(tmp_path):
    jarvis = make_jarvis(tmp_path)
    jarvis.memory.remember("Prefers metric units", key="units")
    system = jarvis.system_message()["content"]
    assert "Prefers metric units" in system


def test_health_shape(tmp_path):
    jarvis = make_jarvis(tmp_path)
    health = jarvis.health()
    assert health["ok"] is True
    assert health["provider"] == "offline"
    assert "calculate" in health["tools"]
