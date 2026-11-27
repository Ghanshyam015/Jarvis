"""Long-term memory backed by a JSON file.

Deliberately simple and inspectable: a list of ``{key, fact, created_at,
updated_at}`` records. Writes are atomic (temp file + ``os.replace``) so a
crashed process never leaves a half-written store behind.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MemoryEntry:
    key: str
    fact: str
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "fact")[:48]


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._entries: list[MemoryEntry] | None = None

    # -- persistence -----------------------------------------------------
    def _load(self) -> list[MemoryEntry]:
        if self._entries is not None:
            return self._entries
        entries: list[MemoryEntry] = []
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw = raw.get("entries", [])
                for item in raw if isinstance(raw, list) else []:
                    if isinstance(item, dict) and item.get("fact"):
                        entries.append(
                            MemoryEntry(
                                key=str(item.get("key") or _slug(item["fact"])),
                                fact=str(item["fact"]),
                                created_at=str(item.get("created_at") or _now()),
                                updated_at=str(item.get("updated_at") or _now()),
                            )
                        )
            except (OSError, json.JSONDecodeError):
                entries = []  # corrupt store: start clean rather than crash
        self._entries = entries
        return entries

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [entry.to_dict() for entry in self._entries or []]}
        handle, tmp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # -- API -------------------------------------------------------------
    def remember(self, fact: str, key: str = "") -> MemoryEntry:
        fact = (fact or "").strip()
        if not fact:
            raise ValueError("fact must not be empty")
        with self._lock:
            entries = self._load()
            resolved = _slug(key) if key else _slug(fact)
            for entry in entries:
                if entry.key == resolved:
                    entry.fact = fact
                    entry.updated_at = _now()
                    self._flush()
                    return entry
            entry = MemoryEntry(key=resolved, fact=fact)
            entries.append(entry)
            self._flush()
            return entry

    def forget(self, key: str) -> bool:
        resolved = _slug(key) if key else ""
        with self._lock:
            entries = self._load()
            kept = [entry for entry in entries if entry.key != resolved]
            changed = len(kept) != len(entries)
            if changed:
                self._entries = kept
                self._flush()
            return changed

    def search(self, query: str = "", limit: int = 5) -> list[MemoryEntry]:
        entries = self._load()
        terms = [term for term in re.split(r"\W+", (query or "").lower()) if term]
        if not terms:
            scored = [(0, entry.updated_at, entry) for entry in entries]
        else:
            scored = []
            for entry in entries:
                haystack = f"{entry.key} {entry.fact}".lower()
                hits = sum(haystack.count(term) for term in terms)
                if hits:
                    scored.append((-hits, entry.updated_at, entry))
        scored.sort(key=lambda item: (item[0], _reverse(item[1])))
        return [item[2] for item in scored[: max(1, limit)]]

    def all(self) -> list[MemoryEntry]:
        return list(self._load())

    def as_context(self, limit: int = 20) -> str:
        entries = self.all()[-limit:]
        if not entries:
            return ""
        lines = [f"- {entry.key}: {entry.fact}" for entry in entries]
        return "Stored facts about the user:\n" + "\n".join(lines)

    def __len__(self) -> int:
        return len(self._load())


def _reverse(text: str) -> str:
    """Trick to sort newest-first by an ISO timestamp using plain ascending sort."""
    return "".join(chr(255 - ord(ch)) if ord(ch) < 255 else ch for ch in text)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def iter_lines(values: Iterable[str]) -> str:
    return "\n".join(values)
