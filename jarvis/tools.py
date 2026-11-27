"""Built-in tools the agent can call.

Each tool exposes a JSON schema (sent to the model) and a Python callable. The
registry keeps tool failures as *values* instead of exceptions so a broken tool
never kills the conversation — the model sees the error text and can recover.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as _timezone_tz
from html.parser import HTMLParser
from typing import Any

from .memory import MemoryStore


class ToolError(Exception):
    """Raised by a tool when the input is unusable."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# --------------------------------------------------------------------------
# safe arithmetic
# --------------------------------------------------------------------------
_BINARY_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_EXPONENT = 1000


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ToolError("only numbers are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError("exponent too large")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise ToolError(f"unknown function: {node.func.id}")
        if node.keywords:
            raise ToolError("keyword arguments are not supported")
        return fn(*[_evaluate(arg) for arg in node.args])
    raise ToolError("unsupported expression")


def calculate(expression: str) -> str:
    """Evaluate an arithmetic expression safely (no ``eval``)."""
    original = expression.strip()
    cleaned = original.replace("^", "**")
    if not cleaned:
        raise ToolError("expression is empty")
    if len(cleaned) > 500:
        raise ToolError("expression is too long")
    if not re.match(r"^[0-9a-zA-Z_+\-*/%().,\s]+$", cleaned):
        raise ToolError("expression contains unsupported characters")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"could not parse expression: {exc.msg}") from exc
    try:
        result = _evaluate(tree)
    except ZeroDivisionError as exc:
        raise ToolError("division by zero") from exc
    except (OverflowError, ValueError) as exc:
        raise ToolError(f"math error: {exc}") from exc
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    if isinstance(result, float):
        result = round(result, 12)
    return f"{original} = {result}"


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------
_TIMEZONE_ALIASES = {
    "": 0,
    "utc": 0,
    "gmt": 0,
    "z": 0,
    "ist": 5.5,
    "india": 5.5,
    "kolkata": 5.5,
    "est": -5,
    "edt": -4,
    "cst": -6,
    "cdt": -5,
    "mst": -7,
    "mdt": -6,
    "pst": -8,
    "pdt": -7,
    "cet": 1,
    "cest": 2,
    "bst": 1,
    "jst": 9,
    "aest": 10,
    "aedt": 11,
}


def get_current_time(timezone: str = "UTC") -> str:
    """Return the current date and time for a timezone name or UTC offset."""
    key = (timezone or "UTC").strip()
    offset: float | None = _TIMEZONE_ALIASES.get(key.lower())
    if offset is None:
        match = re.fullmatch(r"(?:utc|gmt)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", key.lower())
        if match:
            sign = -1 if match.group(1) == "-" else 1
            offset = sign * (int(match.group(2)) + int(match.group(3) or 0) / 60)
    if offset is None:
        raise ToolError(f"unknown timezone {timezone!r}; use a UTC offset like '+05:30'")
    tz = _timezone_tz(timedelta(hours=offset))
    now = datetime.now(tz)
    return f"{now.strftime('%A, %d %B %Y at %H:%M:%S')} (UTC{offset:+g}, {key})"


# --------------------------------------------------------------------------
# web page fetching
# --------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text + " ")


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_url(url: str, max_chars: int = 4000) -> str:
    """Fetch a public web page and return its readable text."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ToolError("url must start with http:// or https://")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; jarvis-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ToolError(f"request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"could not reach {url}: {exc.reason}") from exc
    body = raw.decode(charset, "replace")
    text = html_to_text(body) if "html" in (charset or "") or "<html" in body[:2000].lower() else body
    text = " ".join(text.split()) if "\n" not in text else text
    limit = max(200, min(int(max_chars or 4000), 20000))
    if len(text) > limit:
        text = text[:limit] + f"\n\n[... truncated at {limit} characters]"
    return text or "(page had no readable text)"


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def run(self, name: str, arguments: dict[str, Any] | str | None) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps(
                {"error": f"unknown tool {name!r}", "available": self.names()}
            )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return json.dumps({"error": f"invalid JSON arguments: {exc.msg}"})
        if not isinstance(arguments, dict):
            return json.dumps({"error": "arguments must be a JSON object"})
        try:
            return str(tool.handler(**arguments))
        except ToolError as exc:
            return json.dumps({"error": str(exc)})
        except TypeError as exc:
            return json.dumps({"error": f"bad arguments for {name}: {exc}"})
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def build_registry(memory: MemoryStore) -> ToolRegistry:
    """Create the default registry, wiring in the memory store."""

    def remember(fact: str, key: str = "") -> str:
        entry = memory.remember(fact, key=key)
        return json.dumps({"saved": entry.to_dict()}, ensure_ascii=False)

    def recall(query: str = "", limit: int = 5) -> str:
        entries = memory.search(query, limit=max(1, min(int(limit or 5), 20)))
        if not entries:
            return json.dumps({"matches": [], "note": "nothing stored yet"})
        return json.dumps(
            {"matches": [entry.to_dict() for entry in entries]}, ensure_ascii=False
        )

    def forget(key: str) -> str:
        removed = memory.forget(key)
        return json.dumps({"removed": removed, "key": key})

    return ToolRegistry(
        [
            Tool(
                name="get_current_time",
                description="Get the current date and time. Use this whenever the user asks about 'now', today, or dates.",
                parameters={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "UTC offset like '+05:30', or an abbreviation such as 'UTC', 'IST', 'EST', 'CET'. Defaults to UTC.",
                        }
                    },
                },
                handler=get_current_time,
            ),
            Tool(
                name="calculate",
                description="Evaluate an arithmetic expression exactly. Supports + - * / % ** and functions like sqrt, log, sin, round.",
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Expression to evaluate, e.g. '(17*23)+sqrt(81)'",
                        }
                    },
                    "required": ["expression"],
                },
                handler=calculate,
            ),
            Tool(
                name="remember",
                description="Store a durable fact or preference about the user for later conversations.",
                parameters={
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": "The fact to store."},
                        "key": {
                            "type": "string",
                            "description": "Short stable key (e.g. 'timezone'); reusing a key overwrites it.",
                        },
                    },
                    "required": ["fact"],
                },
                handler=remember,
            ),
            Tool(
                name="recall",
                description="Search stored facts and preferences about the user.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords to search for."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."},
                    },
                },
                handler=recall,
            ),
            Tool(
                name="forget",
                description="Delete a stored fact by its key.",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "Key to delete."}},
                    "required": ["key"],
                },
                handler=forget,
            ),
            Tool(
                name="fetch_url",
                description="Fetch a public web page and return its readable text. Useful for reading docs or articles.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http(s) URL to fetch."},
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to return (default 4000).",
                        },
                    },
                    "required": ["url"],
                },
                handler=fetch_url,
            ),
        ]
    )
