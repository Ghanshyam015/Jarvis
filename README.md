# Jarvis

A small, hackable personal assistant with **tool calling**, **long-term memory**,
a **terminal chat UI** and a **web chat UI** — all in pure Python, zero
dependencies, one shared core.

```
python -m jarvis chat      # terminal assistant (streaming, tool calls, commands)
python -m jarvis ask "..." # one-shot answer
python -m jarvis serve     # web UI + JSON/SSE API on :8000
```

Works immediately in **offline mode** (deterministic stub model). Add an
`OPENAI_API_KEY` to switch to a real model — any OpenAI-compatible endpoint
works (OpenAI, Groq, Together, OpenRouter, Ollama, vLLM…).

## Quick start

```bash
cp .env.example .env        # optional; then set OPENAI_API_KEY
python -m jarvis chat       # or: python -m jarvis serve
```

Requirements: Python **3.10+**. Nothing to `pip install` for the app itself.

## Entries

| Entry | Command | What you get |
| --- | --- | --- |
| CLI | `python -m jarvis chat` | streaming replies, tool activity, `/help` commands |
| One-shot | `python -m jarvis ask "17*23+sqrt(81)"` | prints the answer, exits (`--json` for events) |
| Web | `python -m jarvis serve` | chat UI at `/`, JSON + SSE API under `/api/*` |
| Tools | `python -m jarvis tools` | list tool schemas (`--json` for machines) |
| Memory | `python -m jarvis memory [query]` | inspect/`--forget KEY` stored facts |
| Doctor | `python -m jarvis doctor` | config + model connectivity check |

### CLI commands (inside `chat`)

`/help` `/new` `/tools` `/memory [query]` `/forget KEY` `/model` `/config`
`/save file.md` `/clear` `/exit`

```bash
python -m jarvis chat -m "what time is it in IST?" -m "remember I like dark mode"
```

### Web API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | provider, model, tools, memory count |
| `GET` | `/api/tools` | tool JSON schemas |
| `GET` | `/api/conversations` | list saved conversations |
| `POST` | `/api/conversations` | create one (`{"title": "…"}`) |
| `GET` | `/api/conversations/{id}` | fetch messages |
| `DELETE` | `/api/conversations/{id}` | delete |
| `POST` | `/api/chat` | **SSE stream**: `start`, `text`, `tool_start`, `tool_result`, `error`, `done`, `end` |
| `GET`/`POST`/`DELETE` | `/api/memory[/key]` | inspect, store, forget facts |

```bash
curl -N -X POST localhost:8000/api/chat \
  -H 'content-type: application/json' \
  -d '{"message": "What is (17*23)+sqrt(81)?"}'
```

## Configuration

Everything is env-driven (a `.env` file at the repo root is loaded automatically):

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | – | unset ⇒ offline stub mode |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | point at any compatible server |
| `JARVIS_MODEL` | `gpt-4o-mini` | model name |
| `JARVIS_TEMPERATURE` | `0.3` | |
| `JARVIS_MAX_STEPS` | `6` | max tool round-trips per turn |
| `JARVIS_TIMEOUT` | `120` | upstream request timeout (s) |
| `JARVIS_SYSTEM_PROMPT` | built-in | overrides the persona |
| `JARVIS_OFFLINE` | `0` | `1` forces the stub even with a key |
| `JARVIS_DATA_DIR` | `./data` | memory + conversations |
| `JARVIS_HOST` / `JARVIS_PORT` | `0.0.0.0` / `8000` | `PORT` is honoured too |

## Built-in tools

| Tool | Description |
| --- | --- |
| `get_current_time` | current date/time for a timezone or UTC offset |
| `calculate` | exact arithmetic via a whitelisted AST (no `eval`) |
| `remember` / `recall` / `forget` | durable facts injected into the system prompt |
| `fetch_url` | fetch a public page and return readable text |

Adding a tool is one object in `jarvis/tools.py`:

```python
Tool(
    name="word_count",
    description="Count words in a string.",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    handler=lambda text: str(len(text.split())),
)
```

## Layout

```
jarvis/
  __main__.py      python -m jarvis
  cli.py           argparse entry, streaming renderer, slash commands
  server.py        stdlib HTTP server: UI, JSON API, SSE streaming
  agent.py         the agent loop (model → tools → model)
  llm.py           OpenAI-compatible streaming client + offline stub
  tools.py         tool registry and built-ins
  memory.py        JSON-backed long-term memory
  conversations.py web conversation persistence
  web/             index.html, styles.css, app.js (no build step)
tests/             pytest suite (tools, agent, llm, cli, server)
Makefile           make chat / web / ask / test / lint / doctor
```

## Tests

```bash
python -m pytest -q
```

The suite is hermetic: `tests/conftest.py` forces offline mode and a temp data
dir, the server tests spin up a real HTTP server on an ephemeral port, and
`tests/test_llm.py` points the OpenAI client at a fake SSE endpoint to exercise
streaming, fragmented tool-call assembly, and error mapping without a network.

```bash
make test     # 41 tests
make lint     # ruff + compileall
make doctor   # config / connectivity check
```

## Notes

- No framework, no build step: `http.server` + vanilla JS, so the whole thing is
  easy to read end to end.
- Tool failures are returned to the model as values, never raised — one bad call
  can't kill a conversation.
- The step limit (`JARVIS_MAX_STEPS`) prevents runaway tool loops.
