"""The agent loop: model -> tools -> model, with streaming events.

Framework-free by design: the CLI and the web server both consume the same
generator of events, so behaviour can never drift between the two entries.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm import LLMError, Message, OpenAIProvider, Provider, StubProvider
from .memory import MemoryStore
from .tools import ToolRegistry, build_registry

Event = dict[str, Any]


def make_provider(settings: Settings) -> Provider:
    return StubProvider(settings) if settings.uses_stub else OpenAIProvider(settings)


def build_jarvis(settings: Settings | None = None) -> Jarvis:
    return Jarvis(settings or Settings.from_env())


@dataclass
class Jarvis:
    settings: Settings
    memory: MemoryStore = field(init=False)
    tools: ToolRegistry = field(init=False)
    provider: Provider = field(init=False)

    def __post_init__(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory = MemoryStore(self.settings.data_dir / "memory.json")
        self.tools = build_registry(self.memory)
        self.provider = make_provider(self.settings)

    # ------------------------------------------------------------------
    def system_message(self) -> Message:
        parts = [self.settings.system_prompt]
        memory_context = self.memory.as_context()
        if memory_context:
            parts.append(memory_context)
        return {"role": "system", "content": "\n\n".join(parts)}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": self.settings.provider_name,
            "model": "offline-stub" if self.settings.uses_stub else self.settings.model,
            "tools": self.tools.names(),
            "memory_entries": len(self.memory),
        }

    # ------------------------------------------------------------------
    def stream_reply(
        self, history: Sequence[Message], max_steps: int | None = None
    ) -> Iterator[Event]:
        """Run one assistant turn, yielding ``text`` / ``tool_*`` / ``done`` events.

        ``history`` is the full chat transcript (user/assistant/tool messages),
        without the system message. The caller persists the assistant text.
        """
        steps = max(1, max_steps or self.settings.max_steps)
        working: list[Message] = [self.system_message(), *history]
        collected_text: list[str] = []

        for step in range(steps):
            text_parts: list[str] = []
            tool_state: dict[int, dict[str, Any]] = {}
            finish_reason = "stop"

            try:
                stream = self.provider.stream_chat(working, self.tools.specs())
                for event in stream:
                    kind = event.get("type")
                    if kind == "text":
                        chunk = event.get("text") or ""
                        if chunk:
                            text_parts.append(chunk)
                            collected_text.append(chunk)
                            yield {"type": "text", "text": chunk}
                    elif kind == "tool_call_delta":
                        index = int(event.get("index", 0))
                        slot = tool_state.setdefault(
                            index, {"id": None, "name": "", "arguments": ""}
                        )
                        if event.get("id"):
                            slot["id"] = event["id"]
                        if event.get("name"):
                            slot["name"] = event["name"]
                        if event.get("arguments"):
                            slot["arguments"] += event["arguments"]
                    elif kind == "done":
                        finish_reason = event.get("finish_reason") or "stop"
            except LLMError as exc:
                yield {"type": "error", "message": str(exc)}
                yield {"type": "done", "finish_reason": "error", "steps": step + 1}
                return
            except Exception as exc:
                yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                yield {"type": "done", "finish_reason": "error", "steps": step + 1}
                return

            assistant: Message = {"role": "assistant", "content": "".join(text_parts)}
            calls = []
            for index in sorted(tool_state):
                slot = tool_state[index]
                if not slot["name"]:
                    continue
                calls.append(
                    {
                        "id": slot["id"] or f"call_{step}_{index}",
                        "type": "function",
                        "function": {
                            "name": slot["name"],
                            "arguments": slot["arguments"] or "{}",
                        },
                    }
                )

            if not calls:
                yield {
                    "type": "done",
                    "finish_reason": finish_reason,
                    "steps": step + 1,
                    "text": "".join(collected_text),
                }
                return

            assistant["tool_calls"] = calls
            working.append(assistant)

            for call in calls:
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"]
                try:
                    parsed = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                yield {"type": "tool_start", "name": name, "arguments": parsed}
                result = self.tools.run(name, parsed)
                yield {
                    "type": "tool_result",
                    "name": name,
                    "arguments": parsed,
                    "result": result,
                }
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": result,
                    }
                )

        notice = (
            f"\n\n_(stopped after {steps} tool steps — "
            "raise `JARVIS_MAX_STEPS` if the task needs more.)_"
        )
        yield {"type": "text", "text": notice}
        collected_text.append(notice)
        yield {
            "type": "done",
            "finish_reason": "max_steps",
            "steps": steps,
            "text": "".join(collected_text),
        }
