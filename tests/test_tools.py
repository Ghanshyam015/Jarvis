import json

import pytest

from jarvis.memory import MemoryStore
from jarvis.tools import ToolError, build_registry, calculate, html_to_text


def test_calculate_arithmetic():
    assert calculate("(17*23)+sqrt(81)") == "(17*23)+sqrt(81) = 400"
    assert calculate("2^10") == "2^10 = 1024"
    assert calculate("10/4") == "10/4 = 2.5"
    assert calculate("round(pi, 3)") == "round(pi, 3) = 3.142"


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('ls')", "open('/etc/passwd')", "1/0", "1;2", "lambda: 1"],
)
def test_calculate_rejects_unsafe_or_invalid(expression):
    with pytest.raises(ToolError):
        calculate(expression)


def test_html_to_text_strips_scripts():
    html = "<html><head><style>body{color:red}</style></head><body><h1>Hi</h1><script>alert(1)</script><p>World <b>now</b></p></body></html>"
    text = html_to_text(html)
    assert "Hi" in text and "World" in text
    assert "alert" not in text and "color:red" not in text


def test_registry_returns_errors_as_values(tmp_path):
    registry = build_registry(MemoryStore(tmp_path / "memory.json"))
    bad_json = registry.run("calculate", "{not json")
    assert "error" in json.loads(bad_json)
    unknown = json.loads(registry.run("nope", {}))
    assert unknown["error"].startswith("unknown tool")
    assert "calculate" in unknown["available"]


def test_registry_remember_and_recall(tmp_path):
    registry = build_registry(MemoryStore(tmp_path / "memory.json"))
    registry.run("remember", {"fact": "Likes dark mode", "key": "theme"})
    result = json.loads(registry.run("recall", {"query": "theme"}))
    assert result["matches"][0]["key"] == "theme"
    forgotten = json.loads(registry.run("forget", {"key": "theme"}))
    assert forgotten["removed"] is True


def test_get_current_time_accepts_offsets_and_aliases(tmp_path):
    registry = build_registry(MemoryStore(tmp_path / "memory.json"))
    for zone in ["UTC", "IST", "+05:30", "-08:00"]:
        out = registry.run("get_current_time", {"timezone": zone})
        assert "UTC" in out
    assert "error" in json.loads(registry.run("get_current_time", {"timezone": "Mars/Base"}))
