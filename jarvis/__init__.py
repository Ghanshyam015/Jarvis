"""Jarvis — a small, hackable personal assistant.

Two entries share one core:

    python -m jarvis chat          # interactive terminal assistant
    python -m jarvis serve         # web UI + JSON/SSE API
"""

from .agent import Jarvis, build_jarvis, make_provider
from .config import Settings
from .memory import MemoryStore
from .tools import ToolRegistry, build_registry

__version__ = "1.0.0"

__all__ = [
    "Jarvis",
    "MemoryStore",
    "Settings",
    "ToolRegistry",
    "__version__",
    "build_jarvis",
    "build_registry",
    "make_provider",
]
