"""Tests for the OpenAI-compatible streaming client against a fake endpoint."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.config import Settings
from jarvis.llm import LLMError, OpenAIProvider
from jarvis.memory import MemoryStore
from jarvis.tools import build_registry


def _chunk(delta, finish=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )
        + "\n\n"
    )


class FakeOpenAI(BaseHTTPRequestHandler):
    """Serves one canned SSE response per configured script."""

    script = b""
    status = 200
    requests = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).requests.append(json.loads(body))
        if type(self).status != 200:
            payload = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(type(self).status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(type(self).script.encode())
        self.wfile.flush()


@pytest.fixture()
def fake_api():
    FakeOpenAI.requests = []
    FakeOpenAI.script = b""
    FakeOpenAI.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}/v1"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def provider_for(base_url):
    return OpenAIProvider(
        Settings.from_env(api_key="sk-test", base_url=base_url, offline=False)
    )


def test_streams_text_and_sends_tools(fake_api, tmp_path):
    FakeOpenAI.script = (
        _chunk({"role": "assistant", "content": "Hel"})
        + _chunk({"content": "lo!"})
        + _chunk({}, finish="stop")
        + "data: [DONE]\n\n"
    )
    registry = build_registry(MemoryStore(tmp_path / "memory.json"))
    events = list(
        provider_for(fake_api).stream_chat(
            [{"role": "user", "content": "hi"}], registry.specs()
        )
    )
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello!"
    assert events[-1] == {"type": "done", "finish_reason": "stop"}

    sent = FakeOpenAI.requests[0]
    assert sent["stream"] is True
    assert sent["messages"][0]["content"] == "hi"
    assert {tool["function"]["name"] for tool in sent["tools"]} == set(registry.names())
    assert sent["tool_choice"] == "auto"


def test_accumulates_fragmented_tool_calls(fake_api):
    FakeOpenAI.script = (
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "calculate", "arguments": ""},
                    }
                ]
            }
        )
        + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"expression"'}}]})
        + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": ': "2*21"}'}}]})
        + _chunk({}, finish="tool_calls")
        + "data: [DONE]\n\n"
    )
    events = list(provider_for(fake_api).stream_chat([{"role": "user", "content": "x"}]))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert all(e["index"] == 0 for e in deltas)
    assert deltas[0]["name"] == "calculate"
    arguments = "".join(e["arguments"] for e in deltas)
    assert json.loads(arguments) == {"expression": "2*21"}


def test_http_error_becomes_llm_error(fake_api):
    FakeOpenAI.status = 401
    with pytest.raises(LLMError) as excinfo:
        list(provider_for(fake_api).stream_chat([{"role": "user", "content": "x"}]))
    assert "401" in str(excinfo.value)


def test_unreachable_endpoint_becomes_llm_error():
    provider = provider_for("http://127.0.0.1:9/v1")
    with pytest.raises(LLMError):
        list(provider.stream_chat([{"role": "user", "content": "x"}]))


def test_base_url_trailing_slash_is_normalised():
    settings = Settings.from_env(api_key="k", base_url="http://host/v1/")
    assert settings.chat_completions_url == "http://host/v1/chat/completions"


def test_offline_flag_wins_even_with_key():
    settings = Settings.from_env(api_key="k", offline=True)
    assert settings.uses_stub is True
    assert settings.provider_name == "offline"
