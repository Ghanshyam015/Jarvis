"""Web entry point: a JSON/SSE API plus a single-page chat UI.

Uses only the standard library (``http.server``), so ``python -m jarvis serve``
runs anywhere Python runs — no pip install required.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import __version__
from .agent import Jarvis, build_jarvis
from .config import PACKAGE_DIR, Settings
from .conversations import ConversationStore

WEB_DIR = PACKAGE_DIR / "web"
MAX_BODY = 1_000_000


def _log(message: str) -> None:
    sys.stderr.write(f"[jarvis] {message}\n")
    sys.stderr.flush()


class App:
    """Shared, thread-safe application state."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jarvis: Jarvis = build_jarvis(settings)
        self.conversations = ConversationStore(settings.data_dir / "conversations")
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = f"Jarvis/{__version__}"
    protocol_version = "HTTP/1.1"

    # injected by create_server
    app: App

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter, single line
        _log(f"{self.address_string()} {fmt % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    # -- HTTP verbs ------------------------------------------------------
    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in {"/", "/index.html"}:
                return self._serve_index()
            if path == "/api/health":
                payload = {"version": __version__, **self.app.jarvis.health()}
                payload["conversations"] = len(self.app.conversations.list())
                return self._send_json(payload)
            if path == "/api/tools":
                return self._send_json({"tools": self.app.jarvis.tools.specs()})
            if path == "/api/conversations":
                return self._send_json({"conversations": self.app.conversations.list()})
            if path == "/api/memory":
                return self._send_json(
                    {"entries": [entry.to_dict() for entry in self.app.jarvis.memory.all()]}
                )
            if path.startswith("/api/conversations/"):
                conversation_id = path.split("/")[3]
                conversation = self.app.conversations.get(conversation_id)
                if conversation is None:
                    return self._error(HTTPStatus.NOT_FOUND, "conversation not found")
                return self._send_json(conversation)
            if path == "/favicon.ico":
                return self._send_text(_FAVICON, "image/svg+xml")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/") :])
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except BrokenPipeError:
            return
        except Exception:
            _log(traceback.format_exc())
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/conversations/"):
                conversation_id = path.split("/")[3]
                with self.app.lock:
                    removed = self.app.conversations.delete(conversation_id)
                if not removed:
                    return self._error(HTTPStatus.NOT_FOUND, "conversation not found")
                return self._send_json({"deleted": True, "id": conversation_id})
            if path.startswith("/api/memory/"):
                key = unquote(path.split("/", 3)[3])
                with self.app.lock:
                    removed = self.app.jarvis.memory.forget(key)
                return self._send_json({"deleted": removed, "key": key})
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except Exception:
            _log(traceback.format_exc())
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/chat":
                return self._chat()
            if path == "/api/conversations":
                body = self._read_json()
                with self.app.lock:
                    conversation = self.app.conversations.create(str(body.get("title") or "New chat"))
                return self._send_json(conversation, status=HTTPStatus.CREATED)
            if path == "/api/memory":
                body = self._read_json()
                fact = str(body.get("fact") or "").strip()
                if not fact:
                    return self._error(HTTPStatus.BAD_REQUEST, "fact is required")
                entry = self.app.jarvis.memory.remember(fact, key=str(body.get("key") or ""))
                return self._send_json(entry.to_dict(), status=HTTPStatus.CREATED)
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except BrokenPipeError:
            return
        except Exception:
            _log(traceback.format_exc())
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    # -- static ----------------------------------------------------------
    def _serve_index(self) -> None:
        index = WEB_DIR / "index.html"
        if not index.is_file():
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "ui assets missing")
        self._send_text(index.read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, relative: str) -> None:
        target = (WEB_DIR / unquote(relative)).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_text(target.read_bytes(), content_type)

    # -- chat (SSE) ------------------------------------------------------
    def _sse(self, event: str, data: dict[str, Any]) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()

    def _chat(self) -> None:
        body = self._read_json()
        message = str(body.get("message") or "").strip()
        if not message:
            return self._error(HTTPStatus.BAD_REQUEST, "message is required")

        conversation_id = str(body.get("conversation_id") or "").strip()
        with self.app.lock:
            conversation = (
                self.app.conversations.get(conversation_id) if conversation_id else None
            )
            if conversation is None:
                conversation = self.app.conversations.create()
            conversation_id = conversation["id"]
            self.app.conversations.append_message(conversation, "user", message)
            # persist immediately so the user turn survives a dropped stream
            self.app.conversations.save(conversation)
            history = self.app.conversations.history(conversation)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        self.close_connection = True

        self._sse("start", {"conversation_id": conversation_id, "message": message})
        collected: list[str] = []
        try:
            for event in self.app.jarvis.stream_reply(history):
                if event.get("type") == "text":
                    collected.append(event.get("text") or "")
                self._sse(event.get("type") or "message", event)
        except (BrokenPipeError, ConnectionResetError):
            _log("client disconnected mid-stream")
            return
        except Exception as exc:
            _log(traceback.format_exc())
            try:
                self._sse("error", {"message": f"{type(exc).__name__}: {exc}"})
            except OSError:
                return

        reply = "".join(collected)
        if reply.strip():
            with self.app.lock:
                fresh = self.app.conversations.get(conversation_id) or conversation
                self.app.conversations.append_message(fresh, "assistant", reply)
                self.app.conversations.save(fresh)
        try:
            self._sse("end", {"conversation_id": conversation_id})
        except OSError:
            pass


_FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<rect width="64" height="64" rx="16" fill="#0f172a"/>'
    b'<circle cx="32" cy="32" r="14" fill="none" stroke="#38bdf8" stroke-width="4"/>'
    b'<circle cx="32" cy="32" r="4" fill="#38bdf8"/></svg>'
)


# --------------------------------------------------------------------------
# server bootstrap
# --------------------------------------------------------------------------
class JarvisHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    app: App


def create_server(settings: Settings) -> JarvisHTTPServer:
    app = App(settings)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = JarvisHTTPServer((settings.host, settings.port), handler)
    server.app = app
    return server


def _watch_for_changes(root: Path, interval: float = 1.0) -> None:
    """Re-exec the process when a Python file changes (``--reload``)."""
    baseline = {path: path.stat().st_mtime for path in root.rglob("*.py")}
    while True:
        time.sleep(interval)
        try:
            current = {path: path.stat().st_mtime for path in root.rglob("*.py")}
        except OSError:
            continue
        if current != baseline:
            _log("change detected — restarting")
            os.execv(sys.executable, [sys.executable, *sys.argv])


def serve(settings: Settings | None = None, reload: bool = False) -> int:
    settings = settings or Settings.from_env()
    try:
        server = create_server(settings)
    except OSError as exc:
        _log(f"could not bind {settings.host}:{settings.port} — {exc}")
        return 1

    info = server.app.jarvis.health()
    _log(f"jarvis {__version__} on http://{settings.host}:{settings.port}")
    _log(f"provider={info['provider']} model={info['model']} tools={len(info['tools'])}")
    if settings.uses_stub:
        _log("offline mode: set OPENAI_API_KEY to use a real model")

    if reload:
        threading.Thread(
            target=_watch_for_changes, args=(PACKAGE_DIR,), daemon=True
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down")
    finally:
        server.server_close()
    return 0


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(serve())
