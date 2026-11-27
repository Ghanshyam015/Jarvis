"""Configuration loading for Jarvis.

Everything is driven by environment variables so the same code works from the
CLI, from the web server, and inside tests. A ``.env`` file at the repo root is
loaded automatically when present (no third-party dependency needed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

DEFAULT_SYSTEM_PROMPT = (
    "You are Jarvis, a concise and capable personal assistant. "
    "Use the available tools whenever they make an answer more accurate "
    "(current time, arithmetic, long-term memory, web pages). "
    "Prefer short, direct answers. Show your reasoning only when it helps."
)


def load_dotenv(path: Path | None = None) -> None:
    """Populate ``os.environ`` from a simple KEY=VALUE file.

    Existing environment variables always win, so real configuration is never
    shadowed by a stray ``.env`` file.
    """
    env_path = path or REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _as_float(value: str | None, fallback: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_steps: int = 6
    request_timeout: float = 120.0
    host: str = "0.0.0.0"
    port: int = 8000
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    offline: bool = False
    data_dir: Path = REPO_ROOT / "data"
    title: str = "Jarvis"

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        load_dotenv()
        settings = cls(
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            base_url=(os.environ.get("OPENAI_BASE_URL") or cls.base_url).strip(),
            model=(os.environ.get("JARVIS_MODEL") or cls.model).strip(),
            temperature=_as_float(os.environ.get("JARVIS_TEMPERATURE"), cls.temperature),
            max_steps=_as_int(os.environ.get("JARVIS_MAX_STEPS"), cls.max_steps),
            request_timeout=_as_float(
                os.environ.get("JARVIS_TIMEOUT"), cls.request_timeout
            ),
            host=(os.environ.get("JARVIS_HOST") or os.environ.get("HOST") or cls.host),
            port=_as_int(os.environ.get("JARVIS_PORT") or os.environ.get("PORT"), cls.port),
            system_prompt=os.environ.get("JARVIS_SYSTEM_PROMPT") or cls.system_prompt,
            offline=_as_bool(os.environ.get("JARVIS_OFFLINE")),
            data_dir=Path(
                os.environ.get("JARVIS_DATA_DIR") or str(cls.data_dir)
            ).expanduser(),
        )
        return replace(settings, **overrides) if overrides else settings

    @property
    def uses_stub(self) -> bool:
        """True when there is no API key (or offline mode is forced)."""
        return self.offline or not self.api_key

    @property
    def provider_name(self) -> str:
        return "offline" if self.uses_stub else "openai"

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"
