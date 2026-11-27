"""Conversation persistence for the web entry (JSON files under ``data/``)."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory import load_json, save_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_TITLE_MAX = 60


class ConversationStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _valid_id(conversation_id: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{4,64}", conversation_id or ""))

    def _path(self, conversation_id: str) -> Path:
        if not self._valid_id(conversation_id):
            raise ValueError("invalid conversation id")
        return self.directory / f"{conversation_id}.json"

    # -- API -------------------------------------------------------------
    def create(self, title: str = "New chat") -> dict[str, Any]:
        conversation = {
            "id": secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")[:12],
            "title": (title or "New chat")[:_TITLE_MAX],
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        }
        save_json(self._path(conversation["id"]), conversation)
        return conversation

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        try:
            data = load_json(self._path(conversation_id), None)
        except ValueError:
            return None
        if not isinstance(data, dict) or "messages" not in data:
            return None
        data.setdefault("id", conversation_id)
        data.setdefault("title", "New chat")
        return data

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.directory.glob("*.json")):
            data = load_json(path, None)
            if not isinstance(data, dict) or "messages" not in data:
                continue
            items.append(
                {
                    "id": data.get("id") or path.stem,
                    "title": data.get("title") or "New chat",
                    "updated_at": data.get("updated_at") or "",
                    "message_count": len(data.get("messages") or []),
                }
            )
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items

    def save(self, conversation: dict[str, Any]) -> dict[str, Any]:
        conversation["updated_at"] = _now()
        save_json(self._path(conversation["id"]), conversation)
        return conversation

    def delete(self, conversation_id: str) -> bool:
        try:
            path = self._path(conversation_id)
        except ValueError:
            return False
        if path.is_file():
            path.unlink()
            return True
        return False

    def append_message(
        self, conversation: dict[str, Any], role: str, content: str, **extra: Any
    ) -> dict[str, Any]:
        message = {"role": role, "content": content, "at": _now(), **extra}
        conversation.setdefault("messages", []).append(message)
        if role == "user" and conversation.get("title", "New chat") == "New chat":
            conversation["title"] = (content.strip().splitlines() or ["New chat"])[0][
                :_TITLE_MAX
            ]
        return message

    def history(self, conversation: dict[str, Any]) -> list[dict[str, str]]:
        """Only ``user``/``assistant`` text turns are replayed to the model."""
        history: list[dict[str, str]] = []
        for message in conversation.get("messages") or []:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                history.append({"role": role, "content": content})
        return history
