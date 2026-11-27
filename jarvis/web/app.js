/* Jarvis web client — vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  const els = {
    messages: $("#messages"),
    empty: $("#empty"),
    list: $("#session-list"),
    input: $("#input"),
    composer: $("#composer"),
    send: $("#send"),
    stop: $("#stop"),
    title: $("#chat-title"),
    badge: $("#provider-badge"),
    toolCount: $("#tool-count"),
    status: $("#status-dot"),
    newChat: $("#new-chat"),
    sidebar: $("#sidebar"),
    toggle: $("#toggle-sidebar"),
  };

  const state = {
    conversationId: null,
    title: "New chat",
    busy: false,
    controller: null,
    thread: null,
  };

  /* ------------------------------------------------------------------ */
  /* markdown (small, safe: escape first, then apply a few constructs)   */
  /* ------------------------------------------------------------------ */
  const escapeHtml = (text) =>
    String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function inline(text) {
    // protect code spans first: emphasis markers inside `code` must stay literal
    const codes = [];
    let out = String(text).replace(/\u0000/g, "").replace(/`([^`]+)`/g, (_, code) => {
      codes.push(`<code>${code}</code>`);
      return `\u0000${codes.length - 1}\u0000`;
    });

    out = out
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;:!?])/g, "$1<em>$2</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>'
      )
      .replace(
        /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
        '$1<a href="$2" target="_blank" rel="noreferrer noopener">$2</a>'
      );

    return out.replace(/\u0000(\d+)\u0000/g, (_, index) => codes[Number(index)] || "");
  }

  function renderMarkdown(source) {
    const text = escapeHtml(source || "");
    const out = [];
    const lines = text.split("\n");
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (/^\s*```/.test(line)) {
        const lang = line.replace(/^\s*```/, "").trim();
        const body = [];
        i += 1;
        while (i < lines.length && !/^\s*```/.test(lines[i])) {
          body.push(lines[i]);
          i += 1;
        }
        i += 1;
        out.push(
          `<pre><code${lang ? ` class="lang-${lang}"` : ""}>${body.join("\n")}</code></pre>`
        );
        continue;
      }

      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length + 2, 6);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }

      if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
        const ordered = /^\s*\d+\./.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""))}</li>`);
          i += 1;
        }
        out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
        continue;
      }

      if (/^\s*&gt;\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*&gt;\s?/, ""));
          i += 1;
        }
        out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
        continue;
      }

      if (!line.trim()) {
        i += 1;
        continue;
      }

      const para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^\s*```/.test(lines[i]) &&
        !/^(#{1,4})\s+/.test(lines[i]) &&
        !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) &&
        !/^\s*&gt;\s?/.test(lines[i])
      ) {
        para.push(lines[i]);
        i += 1;
      }
      out.push(`<p>${inline(para.join("<br />"))}</p>`);
    }
    return out.join("");
  }

  /* ------------------------------------------------------------------ */
  /* DOM helpers                                                         */
  /* ------------------------------------------------------------------ */
  function clearThread() {
    els.messages.innerHTML = "";
    state.thread = document.createElement("div");
    state.thread.className = "thread";
    els.messages.appendChild(state.thread);
  }

  function scrollToBottom() {
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function setBusy(busy) {
    state.busy = busy;
    els.send.disabled = busy;
    els.stop.classList.toggle("hidden", !busy);
    els.status.classList.toggle("busy", busy);
    els.status.title = busy ? "thinking…" : "idle";
  }

  function addMessage(role, content) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "You" : "J";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = role === "user" ? "You" : "Jarvis";
    const body = document.createElement("div");
    body.className = "md";
    body.innerHTML = role === "user" ? escapeHtml(content) : renderMarkdown(content);
    bubble.append(who, body);
    wrap.append(avatar, bubble);
    state.thread.appendChild(wrap);
    scrollToBottom();
    return { wrap, bubble, body };
  }

  function addToolPanel(bubble, name, args) {
    const details = document.createElement("details");
    details.className = "tool";
    const summary = document.createElement("summary");
    summary.textContent = `${name}(${Object.entries(args || {})
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(", ")})`;
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = "<b>running…</b>";
    details.append(summary, body);
    let container = bubble.querySelector(".tools");
    if (!container) {
      container = document.createElement("div");
      container.className = "tools";
      bubble.insertBefore(container, bubble.querySelector(".md"));
    }
    container.appendChild(details);
    scrollToBottom();
    return { details, body };
  }

  function setTitle(title) {
    state.title = title || "New chat";
    els.title.textContent = state.title;
    document.title = `${state.title} · Jarvis`;
  }

  function showEmpty(show) {
    if (!show) {
      if (els.empty && els.empty.parentNode) els.empty.remove();
      return;
    }
    if (!document.getElementById("empty")) {
      els.messages.appendChild(els.empty);
    }
  }

  /* ------------------------------------------------------------------ */
  /* API                                                                 */
  /* ------------------------------------------------------------------ */
  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `HTTP ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      const live = health.provider !== "offline";
      els.badge.textContent = `${health.provider} · ${health.model}`;
      els.badge.className = `badge ${live ? "live" : "offline"}`;
      els.badge.title = live
        ? "Connected to a model provider"
        : "No OPENAI_API_KEY — offline stub replies";
      els.toolCount.textContent = `tools: ${health.tools.length}`;
      els.toolCount.title = health.tools.join(", ");
    } catch (error) {
      els.badge.textContent = "backend unreachable";
      els.badge.className = "badge offline";
      console.error(error);
    }
  }

  async function loadSessions() {
    try {
      const data = await api("/api/conversations");
      renderSessions(data.conversations || []);
    } catch (error) {
      console.error(error);
    }
  }

  function renderSessions(sessions) {
    els.list.innerHTML = "";
    if (!sessions.length) {
      const li = document.createElement("li");
      li.className = "sessions-empty";
      li.textContent = "No conversations yet";
      els.list.appendChild(li);
      return;
    }
    for (const session of sessions) {
      const li = document.createElement("li");
      li.dataset.id = session.id;
      if (session.id === state.conversationId) {
        li.classList.add("active");
        // the server derives the title from the first message — adopt it
        if (state.title === "New chat" && session.title && session.title !== "New chat") {
          setTitle(session.title);
        }
      }
      const label = document.createElement("span");
      label.textContent = session.title || "New chat";
      const del = document.createElement("button");
      del.className = "del";
      del.type = "button";
      del.title = "Delete conversation";
      del.textContent = "×";
      del.addEventListener("click", async (event) => {
        event.stopPropagation();
        await api(`/api/conversations/${session.id}`, { method: "DELETE" }).catch(() => {});
        if (state.conversationId === session.id) startNewChat();
        loadSessions();
      });
      li.append(label, del);
      li.addEventListener("click", () => openConversation(session.id));
      els.list.appendChild(li);
    }
  }

  async function openConversation(id) {
    if (state.busy) return;
    try {
      const conversation = await api(`/api/conversations/${id}`);
      state.conversationId = conversation.id;
      setTitle(conversation.title);
      clearThread();
      showEmpty(false);
      for (const message of conversation.messages || []) {
        if (message.role === "user" || message.role === "assistant") {
          addMessage(message.role, message.content);
        }
      }
      loadSessions();
      els.sidebar.classList.remove("open");
      scrollToBottom();
    } catch (error) {
      console.error(error);
    }
  }

  function startNewChat() {
    if (state.busy) return;
    state.conversationId = null;
    setTitle("New chat");
    clearThread();
    els.messages.innerHTML = "";
    els.messages.appendChild(els.empty);
    els.input.focus();
    loadSessions();
  }

  /* ------------------------------------------------------------------ */
  /* chat streaming                                                      */
  /* ------------------------------------------------------------------ */
  function handleEvent(eventName, payload, ctx) {
    switch (eventName) {
      case "start":
        if (payload.conversation_id && payload.conversation_id !== state.conversationId) {
          state.conversationId = payload.conversation_id;
          loadSessions();
        }
        break;
      case "text": {
        if (ctx.pending) {
          ctx.body.innerHTML = "";
          ctx.pending.remove();
          ctx.pending = null;
        }
        ctx.raw += payload.text || "";
        ctx.body.innerHTML = renderMarkdown(ctx.raw) + '<span class="caret"></span>';
        scrollToBottom();
        break;
      }
      case "tool_start": {
        const panel = addToolPanel(ctx.bubble, payload.name, payload.arguments);
        ctx.tools.set(payload.name, panel);
        break;
      }
      case "tool_result": {
        const panel = ctx.tools.get(payload.name);
        if (panel) {
          panel.body.innerHTML = `<b>arguments</b>\n${escapeHtml(
            JSON.stringify(payload.arguments, null, 2)
          )}\n\n<b>result</b>\n${escapeHtml(String(payload.result ?? ""))}`;
        }
        break;
      }
      case "error": {
        if (ctx.pending) {
          ctx.pending.remove();
          ctx.pending = null;
        }
        ctx.error = payload.message || "unknown error";
        break;
      }
      case "done":
        ctx.finishReason = payload.finish_reason || "stop";
        break;
      case "end":
        ctx.ended = true;
        break;
      default:
        break;
    }
  }

  async function sendMessage(text) {
    const message = text.trim();
    if (!message || state.busy) return;

    showEmpty(false);
    addMessage("user", message);
    els.input.value = "";
    autosize();

    const { bubble, body } = addMessage("assistant", "");
    const pending = document.createElement("div");
    pending.className = "dots";
    pending.innerHTML = "<span></span><span></span><span></span>";
    body.appendChild(pending);

    const ctx = {
      bubble,
      body,
      raw: "",
      pending,
      tools: new Map(),
      error: null,
      finishReason: null,
      ended: false,
    };

    setBusy(true);
    state.controller = new AbortController();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message,
          conversation_id: state.conversationId,
        }),
        signal: state.controller.signal,
      });

      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.error || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let split;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const chunk = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          let eventName = "message";
          const dataLines = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          let payload = {};
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch (err) {
            console.warn("bad SSE payload", err, dataLines);
            continue;
          }
          handleEvent(eventName, payload, ctx);
        }
      }

      if (ctx.pending) {
        ctx.pending.remove();
        ctx.pending = null;
      }
      ctx.body.innerHTML = renderMarkdown(ctx.raw);

      if (ctx.error) {
        bubble.parentElement.classList.add("error");
        bubble.querySelector(".who").textContent = "Jarvis · error";
        bubble.appendChild(
          Object.assign(document.createElement("div"), {
            className: "md",
            innerHTML: renderMarkdown(`⚠ ${ctx.error}`),
          })
        );
      }
      if (ctx.finishReason === "max_steps" && !ctx.raw.trim()) {
        ctx.body.innerHTML = renderMarkdown("_Stopped at the step limit._");
      }
    } catch (error) {
      if (ctx.pending) ctx.pending.remove();
      if (error.name === "AbortError") {
        ctx.body.innerHTML = renderMarkdown((ctx.raw || "") + "\n\n_(stopped)_");
      } else {
        bubble.parentElement.classList.add("error");
        bubble.querySelector(".who").textContent = "Jarvis · error";
        ctx.body.innerHTML = renderMarkdown(`⚠ ${error.message}`);
      }
    } finally {
      setBusy(false);
      state.controller = null;
      loadSessions();
      els.input.focus();
      scrollToBottom();
    }
  }

  /* ------------------------------------------------------------------ */
  /* composer                                                            */
  /* ------------------------------------------------------------------ */
  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 190)}px`;
  }

  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(els.input.value);
    }
  });
  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(els.input.value);
  });
  els.stop.addEventListener("click", () => {
    if (state.controller) state.controller.abort();
  });
  els.newChat.addEventListener("click", startNewChat);
  els.toggle.addEventListener("click", () => els.sidebar.classList.toggle("open"));

  els.empty.addEventListener("click", (event) => {
    const button = event.target.closest("[data-prompt]");
    if (button) sendMessage(button.dataset.prompt);
  });

  /* ------------------------------------------------------------------ */
  /* boot                                                                */
  /* ------------------------------------------------------------------ */
  clearThread();
  els.messages.appendChild(els.empty);
  loadHealth();
  loadSessions();
  autosize();
  els.input.focus();
})();
