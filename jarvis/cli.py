"""Interactive terminal entry point for Jarvis."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import __version__
from .agent import Jarvis, build_jarvis
from .config import Settings

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"

BANNER = f"""
  {CYAN}    _   __   ____   __   __  ____
       / | / /  / __/  / /  / / / ___/
      /  |/ /  / /_   / /__/ /  \\__ \\
     /_/|_/   \\___/   \\____/  /____/{RESET}

  {DIM}a small assistant with tools and memory · /help for commands{RESET}
"""

HELP = f"""{BOLD}Commands{RESET}
  /help               show this help
  /new                start a fresh conversation
  /tools              list available tools
  /memory [query]     inspect long-term memory
  /forget <key>       delete a memory entry
  /model              show provider/model info
  /config             show effective settings
  /save <file.md>     export this conversation as Markdown
  /clear              clear the screen
  /exit               quit (also Ctrl-D)
"""


class Console:
    """Tiny ANSI helper that degrades gracefully when colour is unavailable."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and sys.stdout.isatty()

    def paint(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.enabled else text

    def write(self, text: str = "") -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def stream(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis — a small assistant with tools and memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m jarvis chat\n"
            "  python -m jarvis ask 'what is (17*23)+sqrt(81)?'\n"
            "  python -m jarvis serve --port 8000\n"
            "  python -m jarvis tools\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="interactive terminal session")
    chat.add_argument("--message", "-m", action="append", help="answer this prompt and exit")
    chat.add_argument("--no-color", action="store_true", help="disable ANSI colours")

    ask = sub.add_parser("ask", help="one-shot question, prints the answer")
    ask.add_argument("prompt", nargs="+", help="the question to ask")
    ask.add_argument("--json", action="store_true", help="emit the raw event stream as JSON")
    ask.add_argument("--no-color", action="store_true", help="disable ANSI colours")

    serve = sub.add_parser("serve", help="start the web UI + API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", "-p", type=int, default=None)
    serve.add_argument("--reload", action="store_true", help="restart on file change (dev)")

    tools = sub.add_parser("tools", help="list built-in tools")
    tools.add_argument("--json", action="store_true", help="machine-readable output")

    mem = sub.add_parser("memory", help="inspect or edit long-term memory")
    mem.add_argument("query", nargs="*", help="optional search terms")
    mem.add_argument("--forget", metavar="KEY", help="delete an entry by key")
    mem.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("doctor", help="check configuration and connectivity")
    return parser


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render_events(
    events: Iterator[dict[str, Any]], console: Console, show_tools: bool = True
) -> str:
    """Print a stream of agent events; returns the collected assistant text."""
    buffer: list[str] = []
    printed_header = False
    in_text = False

    for event in events:
        kind = event.get("type")
        if kind == "text":
            if not in_text:
                if printed_header:
                    console.write()
                console.stream(console.paint(CYAN, "jarvis › "))
                in_text = True
            console.stream(event.get("text", ""))
            buffer.append(event.get("text", ""))
        elif kind == "tool_start" and show_tools:
            if in_text:
                console.write()
                in_text = False
            args = event.get("arguments") or {}
            rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
            console.write(
                console.paint(DIM, f"  ⚙ {event.get('name')}({rendered})")
            )
        elif kind == "tool_result" and show_tools:
            result = str(event.get("result") or "")
            preview = result if len(result) <= 240 else result[:240] + "…"
            console.write(console.paint(DIM, f"  ↳ {preview}"))
            printed_header = True
        elif kind == "error":
            if in_text:
                console.write()
                in_text = False
            console.write(console.paint(RED, f"  ✖ {event.get('message')}"))
        elif kind == "done":
            if in_text:
                console.write()
                in_text = False
            if event.get("finish_reason") == "max_steps":
                console.write(console.paint(YELLOW, "  (step limit reached)"))
            console.write()
    return "".join(buffer)


def show_tools(jarvis: Jarvis, console: Console, as_json: bool = False) -> None:
    specs = jarvis.tools.specs()
    if as_json:
        console.write(json.dumps(specs, indent=2))
        return
    console.write(console.paint(BOLD, "Tools"))
    for spec in specs:
        console.write(f"  {console.paint(GREEN, spec['name']):<28} {spec['description']}")
    console.write()


def show_memory(jarvis: Jarvis, console: Console, query: str = "", as_json: bool = False) -> None:
    entries = jarvis.memory.search(query, limit=50) if query else jarvis.memory.all()
    if as_json:
        console.write(json.dumps([entry.to_dict() for entry in entries], indent=2))
        return
    if not entries:
        console.write(console.paint(DIM, "  memory is empty"))
        console.write()
        return
    console.write(console.paint(BOLD, f"Memory ({len(entries)})"))
    for entry in entries:
        console.write(f"  {console.paint(GREEN, entry.key):<24} {entry.fact}")
    console.write()


def print_config(jarvis: Jarvis, console: Console) -> None:
    settings = jarvis.settings
    rows = [
        ("provider", settings.provider_name),
        ("model", "offline-stub" if settings.uses_stub else settings.model),
        ("base_url", "—" if settings.uses_stub else settings.base_url),
        ("max_steps", str(settings.max_steps)),
        ("temperature", str(settings.temperature)),
        ("data_dir", str(settings.data_dir)),
        ("memory entries", str(len(jarvis.memory))),
    ]
    console.write(console.paint(BOLD, "Configuration"))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        console.write(f"  {label:<{width}}  {value}")
    console.write()
    if settings.uses_stub:
        console.write(
            console.paint(
                YELLOW,
                "  offline mode: set OPENAI_API_KEY (and optionally OPENAI_BASE_URL) "
                "to use a real model",
            )
        )
        console.write()


def export_markdown(history: list[dict[str, str]], path: Path) -> Path:
    lines = ["# Jarvis conversation", ""]
    for message in history:
        role = "You" if message["role"] == "user" else "Jarvis"
        lines.extend([f"**{role}:** {message['content'].strip()}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------
def run_ask(prompt: str, as_json: bool, console: Console) -> int:
    jarvis = build_jarvis()
    history = [{"role": "user", "content": prompt}]
    if as_json:
        for event in jarvis.stream_reply(history):
            console.write(json.dumps(event, ensure_ascii=False))
        return 0
    text = render_events(jarvis.stream_reply(history), console)
    return 0 if text.strip() else 1


def run_chat(messages: list[str], console: Console) -> int:
    jarvis = build_jarvis()
    history: list[dict[str, str]] = []

    if messages:
        for prompt in messages:
            console.write(console.paint(BOLD, f"you › {prompt}"))
            history.append({"role": "user", "content": prompt})
            text = render_events(jarvis.stream_reply(history), console)
            history.append({"role": "assistant", "content": text})
        return 0

    console.write(BANNER)
    info = jarvis.health()
    console.write(
        console.paint(
            DIM,
            f"  {info['provider']} · {info['model']} · {len(info['tools'])} tools · "
            f"{info['memory_entries']} memories   (/help for commands)",
        )
    )
    console.write()

    while True:
        try:
            prompt = input(console.paint(MAGENTA, "you › ") if console.enabled else "you › ")
        except (EOFError, KeyboardInterrupt):
            console.write()
            console.write(console.paint(DIM, "bye"))
            return 0
        prompt = prompt.strip()
        if not prompt:
            continue

        if prompt.startswith("/"):
            command, _, rest = prompt.partition(" ")
            rest = rest.strip()
            if command in {"/exit", "/quit", "/q"}:
                console.write(console.paint(DIM, "bye"))
                return 0
            if command == "/help":
                console.write(HELP)
            elif command == "/new":
                history.clear()
                console.write(console.paint(DIM, "started a new conversation"))
                console.write()
            elif command == "/tools":
                show_tools(jarvis, console)
            elif command == "/memory":
                show_memory(jarvis, console, rest)
            elif command == "/forget" and rest:
                ok = jarvis.memory.forget(rest)
                console.write(
                    console.paint(GREEN if ok else YELLOW, f"{'removed' if ok else 'no entry for'} {rest}")
                )
                console.write()
            elif command == "/model":
                print_config(jarvis, console)
            elif command == "/config":
                print_config(jarvis, console)
            elif command == "/clear":
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
            elif command == "/save" and rest:
                path = export_markdown(history, Path(rest).expanduser())
                console.write(console.paint(GREEN, f"saved {len(history)} messages to {path}"))
                console.write()
            else:
                console.write(console.paint(YELLOW, f"unknown command {command!r} — try /help"))
                console.write()
            continue

        history.append({"role": "user", "content": prompt})
        text = render_events(jarvis.stream_reply(history), console)
        history.append({"role": "assistant", "content": text})


def run_tools(as_json: bool, console: Console) -> int:
    show_tools(build_jarvis(), console, as_json=as_json)
    return 0


def run_memory(query: list[str], forget: str | None, as_json: bool, console: Console) -> int:
    jarvis = build_jarvis()
    if forget:
        ok = jarvis.memory.forget(forget)
        if as_json:
            console.write(json.dumps({"removed": ok, "key": forget}))
        else:
            console.write(console.paint(GREEN if ok else YELLOW, f"{'removed' if ok else 'no entry for'} {forget}"))
        return 0 if ok else 1
    show_memory(jarvis, console, " ".join(query), as_json=as_json)
    return 0


def run_doctor(console: Console) -> int:
    settings = Settings.from_env()
    jarvis = build_jarvis(settings)
    ok = True
    console.write(console.paint(BOLD, "Jarvis doctor"))
    console.write(f"  python      {sys.version.split()[0]}")
    console.write(f"  data dir    {settings.data_dir} ({'writable' if _writable(settings.data_dir) else 'NOT writable'})")
    console.write(f"  tools       {', '.join(jarvis.tools.names())}")
    console.write(f"  provider    {settings.provider_name}")
    if settings.uses_stub:
        console.write(console.paint(YELLOW, "  ! no OPENAI_API_KEY found — offline stub in use"))
    else:
        console.write(f"  endpoint    {settings.chat_completions_url}")
        probe = list(jarvis.provider.stream_chat([{"role": "user", "content": "ping"}], []))
        errors = [event for event in probe if event.get("type") == "error"]
        if errors:
            ok = False
            console.write(console.paint(RED, f"  ✖ model unreachable: {errors[0].get('message')}"))
        else:
            console.write(console.paint(GREEN, "  ✓ model reachable"))
    console.write(f"  terminal    {shutil.get_terminal_size().columns}x{shutil.get_terminal_size().lines}")
    console.write()
    return 0 if ok else 1


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import serve

        overrides: dict[str, Any] = {}
        if args.host:
            overrides["host"] = args.host
        if args.port:
            overrides["port"] = args.port
        return serve(Settings.from_env(**overrides), reload=args.reload)
    if args.command == "tools":
        return run_tools(args.json, Console())
    if args.command == "memory":
        return run_memory(args.query, args.forget, args.json, Console())
    if args.command == "doctor":
        return run_doctor(Console())
    if args.command == "ask":
        return run_ask(" ".join(args.prompt), args.json, Console(not args.no_color))
    if args.command == "chat":
        return run_chat(args.message or [], Console(not args.no_color))

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
