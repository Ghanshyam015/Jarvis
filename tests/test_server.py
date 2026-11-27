import json
import threading
import urllib.request

import pytest

from jarvis.config import Settings
from jarvis.conversations import ConversationStore
from jarvis.server import create_server


@pytest.fixture()
def server(tmp_path):
    settings = Settings.from_env(api_key="", data_dir=tmp_path, offline=True, host="127.0.0.1", port=0)
    httpd = create_server(settings)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read()


def post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read()


def delete(url):
    request = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read()


def test_index_served(server):
    status, body = get(server + "/")
    assert status == 200
    assert b"<title>Jarvis</title>" in body


def test_static_and_favicon(server):
    status, body = get(server + "/static/app.js")
    assert status == 200 and b"sendMessage" in body
    status, body = get(server + "/favicon.ico")
    assert status == 200 and body.startswith(b"<svg")


def test_health(server):
    status, body = get(server + "/api/health")
    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["provider"] == "offline"
    assert "calculate" in payload["tools"]


def test_tools_endpoint(server):
    status, body = get(server + "/api/tools")
    tools = json.loads(body)["tools"]
    assert any(tool["name"] == "fetch_url" for tool in tools)


def test_chat_streams_and_persists(server):
    status, body = post(server + "/api/chat", {"message": "hello there"})
    text = body.decode()
    assert status == 200
    assert "event: start" in text and "event: end" in text
    assert "event: text" in text

    conversation_id = json.loads(
        text.split("event: start\ndata: ")[1].split("\n\n")[0]
    )["conversation_id"]
    status, body = get(server + f"/api/conversations/{conversation_id}")
    conversation = json.loads(body)
    roles = [message["role"] for message in conversation["messages"]]
    assert roles == ["user", "assistant"]
    assert conversation["title"].startswith("hello")


def test_chat_uses_tools(server):
    status, body = post(server + "/api/chat", {"message": "what is 12*12?"})
    text = body.decode()
    assert "event: tool_start" in text
    assert "event: tool_result" in text


def test_chat_rejects_empty_message(server):
    request = urllib.request.Request(
        server + "/api/chat",
        data=json.dumps({"message": "  "}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 400


def test_conversation_crud(server):
    status, body = post(server + "/api/conversations", {"title": "Errands"})
    conversation = json.loads(body)
    assert status == 201 and conversation["title"] == "Errands"

    status, body = get(server + "/api/conversations")
    listing = json.loads(body)["conversations"]
    assert any(item["id"] == conversation["id"] for item in listing)

    status, body = delete(server + f"/api/conversations/{conversation['id']}")
    assert json.loads(body)["deleted"] is True

    status, body = get(server + "/api/conversations")
    assert all(item["id"] != conversation["id"] for item in json.loads(body)["conversations"])


def test_memory_endpoints(server):
    status, body = post(server + "/api/memory", {"fact": "Uses vim", "key": "editor"})
    assert status == 201
    status, body = get(server + "/api/memory")
    assert any(entry["key"] == "editor" for entry in json.loads(body)["entries"])
    status, body = delete(server + "/api/memory/editor")
    assert json.loads(body)["deleted"] is True


def test_unknown_route_returns_404(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server + "/api/nope")
    assert excinfo.value.code == 404


def test_path_traversal_blocked(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server + "/static/../../jarvis/config.py")
    assert excinfo.value.code in (403, 404)


def test_conversation_store_roundtrip(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    conversation = store.create()
    store.append_message(conversation, "user", "first message here")
    store.append_message(conversation, "assistant", "reply")
    store.save(conversation)

    loaded = store.get(conversation["id"])
    assert loaded["title"] == "first message here"
    assert store.history(loaded) == [
        {"role": "user", "content": "first message here"},
        {"role": "assistant", "content": "reply"},
    ]
    assert store.get("../../etc/passwd") is None
