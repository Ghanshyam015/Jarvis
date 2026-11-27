"""Chat model providers.

The agent only needs one thing from a provider: a *stream* of events for a
conversation. Two implementations ship here:

* :class:`OpenAIProvider` talks to any OpenAI-compatible ``/chat/completions``
  endpoint (OpenAI, Groq, Together, OpenRouter, Ollama, vLLM, ...).
* :class:`StubProvider` is a deterministic offline fallback so the whole app
  (CLI, web UI, tools, tests) works without an API key.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from typing import Any

from .config import Settings

Message = dict[str, Any]
Event = dict[str, Any]


class LLMError(RuntimeError):
    """Raised when the upstream model cannot be reached or rejects a request."""


class Provider:
    """Base class: subclasses implement :meth:`stream_chat`."""

    name = "provider"

    def stream_chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> Iterator[Event]:  # pragma: no cover - interface only
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def tool_spec(tool: dict[str, Any]) -> dict[str, Any]:
        return {"type": "function", "function": tool}


def _accumulated_tool_calls(state: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert streamed tool-call fragments into OpenAI tool_call objects."""
    calls = []
    for index in sorted(state):
        call = state[index]
        calls.append(
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call.get("name", ""),
                    "arguments": call.get("arguments") or "{}",
                },
            }
        )
    return calls


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _payload(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": list(messages),
            "stream": True,
            "temperature": self.settings.temperature,
        }
        if tools:
            payload["tools"] = [self.tool_spec(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        return payload

    def stream_chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> Iterator[Event]:
        body = json.dumps(self._payload(messages, tools)).encode("utf-8")
        request = urllib.request.Request(
            self.settings.chat_completions_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.settings.api_key}",
                "User-Agent": "jarvis-agent/1.0",
            },
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=self.settings.request_timeout
            )
        except urllib.error.HTTPError as exc:  # upstream rejected the request
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise LLMError(f"{exc.code} {exc.reason}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"could not reach {self.settings.base_url}: {exc.reason}") from exc

        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for event in self._events_from_chunk(chunk):
                    yield event

    def _events_from_chunk(self, chunk: dict[str, Any]) -> Iterator[Event]:
        choices = chunk.get("choices") or []
        if not choices:
            return
        choice = choices[0]
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if content:
            yield {"type": "text", "text": content}

        for fragment in delta.get("tool_calls") or []:
            yield {
                "type": "tool_call_delta",
                "index": int(fragment.get("index", 0)),
                "id": fragment.get("id"),
                "name": (fragment.get("function") or {}).get("name"),
                "arguments": (fragment.get("function") or {}).get("arguments") or "",
            }

        finish = choice.get("finish_reason")
        if finish:
            yield {"type": "done", "finish_reason": finish}


_ARITHMETIC = re.compile(r"^[\s\d+\-*/%().^a-zA-Z_]+$")
_ARITH_HINT = re.compile(r"sqrt|log|sin|cos|tan|floor|ceil|\bpi\b", re.IGNORECASE)
_MATH_WORDS = re.compile(
    r"^(?:what(?:\u2019)?(?:'s| is)?|calc(?:ulate)?|compute|evaluate|how much is)\s+", re.IGNORECASE
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class StubProvider(Provider):
    """Deterministic offline provider with just enough smarts to demo the loop.

    It exercises real tool calls for time and arithmetic questions so the agent
    loop, the tool registry, the CLI renderer and the web UI all get tested
    without any network access.
    """

    name = "offline"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    def stream_chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> Iterator[Event]:
        available = {tool["name"] for tool in tools or []}
        user = next(
            (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        lowered = user.lower()
        already_used_tool = any(m.get("role") == "tool" for m in messages)

        if already_used_tool:
            tool_result = next(
                m.get("content") or "" for m in reversed(messages) if m.get("role") == "tool"
            )
            text = f"{tool_result}\n\n_(offline mode — set `OPENAI_API_KEY` for full answers.)_"
            yield from self._emit_text(text)
            yield {"type": "done", "finish_reason": "stop"}
            return

        if "time" in lowered and "get_current_time" in available:
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call_time",
                "name": "get_current_time",
                "arguments": json.dumps({"timezone": "UTC"}),
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
            return

        candidate = _MATH_WORDS.sub("", user.strip()).strip().rstrip("=?").strip()
        if (
            candidate
            and _ARITHMETIC.match(candidate)
            and len(candidate) <= 120
            and (_NUMBER.search(candidate) or _ARITH_HINT.search(candidate))
            and "calculate" in available
        ):
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call_calc",
                "name": "calculate",
                "arguments": json.dumps({"expression": candidate}),
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
            return

        yield from self._emit_text(self._canned_reply(user))
        yield {"type": "done", "finish_reason": "stop"}

    @staticmethod
    def _emit_text(text: str, size: int = 24) -> Iterator[Event]:
        for start in range(0, len(text), size):
            yield {"type": "text", "text": text[start : start + size]}

    @staticmethod
    def _canned_reply(user: str) -> str:
        if not user:
            return "I'm Jarvis, running in offline mode. Ask me the time or some arithmetic."
        return (
            f"I hear you: “{user.strip()}”.\n\n"
            "I'm running in **offline mode**, so this is a canned reply rather than a "
            "model answer. Everything else is live: the web UI, streaming, tool calls "
            "(`get_current_time`, `calculate`, `remember`, `recall`, `fetch_url`) and "
            "conversation memory.\n\n"
            "Add `OPENAI_API_KEY` to `.env` (or the environment) and restart to talk to "
            "a real model."
        )
