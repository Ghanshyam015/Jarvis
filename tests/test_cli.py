import json

from jarvis.cli import Console, export_markdown, main, run_ask


def test_ask_prints_answer(capsys):
    code = run_ask("hello", as_json=False, console=Console(enabled=False))
    out = capsys.readouterr().out
    assert code == 0
    assert "offline" in out.lower()


def test_ask_json_stream_is_valid(capsys):
    code = run_ask("(2+2)", as_json=True, console=Console(enabled=False))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    assert code == 0
    assert events[0]["type"] in {"text", "tool_start"}
    assert events[-1]["type"] == "done"


def test_tools_subcommand(capsys):
    assert main(["tools"]) == 0
    assert "calculate" in capsys.readouterr().out


def test_memory_subcommand_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_OFFLINE", "1")
    assert main(["memory", "--json", "nothing"]) == 0
    capsys.readouterr()


def test_help_without_command(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_export_markdown(tmp_path):
    path = export_markdown(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        tmp_path / "chat.md",
    )
    body = path.read_text()
    assert "**You:** hi" in body and "**Jarvis:** hello" in body
