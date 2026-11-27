"""Shared pytest fixtures."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never touch a real data dir or a real API key during tests."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_OFFLINE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield
    os.environ.pop("JARVIS_DATA_DIR", None)
    os.environ.pop("JARVIS_OFFLINE", None)
