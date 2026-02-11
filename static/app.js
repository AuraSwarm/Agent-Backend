(function () {
  "use strict";

  const messagesEl = document.getElementById("messages");
  const emptyStateEl = document.getElementById("emptyState");
  const modelSelect = document.getElementById("modelSelect");
  const newChatBtn = document.getElementById("newChatBtn");
  const inputEl = document.getElementById("input");
  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  const fileInput = document.getElementById("fileInput");
  const folderInput = document.getElementById("folderInput");
  const attachFilesBtn = document.getElementById("attachFilesBtn");
  const attachFolderBtn = document.getElementById("attachFolderBtn");
  const attachedFilesEl = document.getElementById("attachedFiles");
  const conversationListEl = document.getElementById("conversationList");
  const conversationLoadingEl = document.getElementById("conversationLoading");

  let sessionId = null;
  let messageHistory = [];
  let attachedFiles = [];
  let recentSessions = [];
  let streamAbortController = null;

  function emptyState() {
    return document.getElementById("emptyState");
  }
  function hideEmpty() {
    const el = emptyState();
    if (el) el.hidden = true;
  }
  function showEmpty() {
    const el = emptyState();
    if (el) el.hidden = false;
  }

  /** Main title for session: first conversation topic (第一次对话主题). */
  function displayTitle(session) {
    const first = session.first_message_preview && session.first_message_preview.trim();
    if (first) return first;
    if (session.title && session.title.trim()) return session.title.trim();
    return "新对话";
  }

  /**
   * Build DOM: 主标题 = 第一次对话主题，副标题 = 最近一次对话内容，浅色注释 = 一句话归纳.
   */
  function buildConversationPreview(session) {
    const first = session.first_message_preview && session.first_message_preview.trim();
    const last = session.last_message_preview && session.last_message_preview.trim();
    const summary = session.topic_summary && session.topic_summary.trim();
    const wrap = document.createElement("div");
    wrap.className = "conversation-preview";
    // 主标题：第一次对话主题
    const titleEl = document.createElement("div");
    titleEl.className = "conversation-preview-title";
    titleEl.textContent = first || (session.title && session.title.trim()) || "新对话";
    titleEl.title = titleEl.textContent;
    wrap.appendChild(titleEl);
    // 副标题：最近一次对话内容
    if (last) {
      const subEl = document.createElement("div");
      subEl.className = "conversation-preview-subtitle";
      subEl.textContent = last;
      subEl.title = last;
      wrap.appendChild(subEl);
    }
    // 浅色注释：一句话归纳
    if (summary) {
      const noteEl = document.createElement("div");
      noteEl.className = "conversation-preview-annotation";
      noteEl.textContent = summary;
      noteEl.title = summary;
      wrap.appendChild(noteEl);
    }
    return wrap;
  }

  function formatRelativeTime(isoDate) {
    if (!isoDate) return "";
    const d = new Date(isoDate);
    const now = new Date();
    const diffMs = now - d;
    const diffM = Math.floor(diffMs / 60000);
    const diffH = Math.floor(diffMs / 3600000);
    const diffD = Math.floor(diffMs / 86400000);
    if (diffM < 1) return "刚刚";
    if (diffM < 60) return diffM + " 分钟前";
    if (diffH < 24) return diffH + " 小时前";
    if (diffD < 7) return diffD + " 天前";
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }

  async function loadSessions() {
    try {
      const r = await fetch("/sessions?limit=50");
      if (!r.ok) return;
      recentSessions = await r.json();
      renderConversationList();
    } catch (e) {
      console.error(e);
      conversationListEl.innerHTML = "<li class=\"conversation-empty\">加载失败</li>";
    }
  }

  /** Delete a conversation (cleanup). Calls DELETE /sessions/{id}, then refreshes list. */
  async function deleteConversation(id) {
    if (!id) return;
    if (!window.confirm("确定删除该对话？删除后无法恢复。")) return;
    try {
      const r = await fetch("/sessions/" + encodeURIComponent(id), { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      const wasCurrent = sessionId === id;
      await loadSessions();
      if (wasCurrent) {
        sessionId = null;
        messageHistory = [];
        while (messagesEl.lastChild) messagesEl.removeChild(messagesEl.lastChild);
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.id = "emptyState";
        empty.innerHTML = "<p>选择模型并开始新对话，或从左侧恢复历史对话。</p><p>可附加文件或文件夹作为上下文。</p>";
        messagesEl.appendChild(empty);
        showEmpty();
      }
    } catch (e) {
      console.error(e);
      window.alert("删除失败: " + e.message);
    }
  }

  function renderConversationList() {
    conversationListEl.innerHTML = "";
    if (recentSessions.length === 0) {
      const li = document.createElement("li");
      li.className = "conversation-empty";
      li.textContent = "暂无对话";
      conversationListEl.appendChild(li);
      return;
    }
    recentSessions.forEach(function (s) {
      const li = document.createElement("li");
      li.className = "conversation-item";
      li.dataset.sessionId = s.session_id;
      if (sessionId && s.session_id === sessionId) li.classList.add("active");
      const left = document.createElement("div");
      left.className = "conversation-item-left";
      left.appendChild(buildConversationPreview(s));
      const timeEl = document.createElement("span");
      timeEl.className = "conversation-time";
      timeEl.textContent = formatRelativeTime(s.updated_at);
      left.appendChild(timeEl);
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "conversation-delete";
      deleteBtn.setAttribute("aria-label", "删除对话");
      deleteBtn.textContent = "×";
      deleteBtn.title = "删除此对话（清理）";
      deleteBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        deleteConversation(s.session_id);
      });
      li.appendChild(left);
      li.appendChild(deleteBtn);
      li.addEventListener("click", function () {
        loadConversation(s.session_id);
      });
      conversationListEl.appendChild(li);
    });
  }

  function setActiveInSidebar(id) {
    conversationListEl.querySelectorAll("li.conversation-item").forEach(function (li) {
      li.classList.toggle("active", li.dataset.sessionId === id);
    });
  }

  async function loadConversation(id) {
    if (sessionId === id) return;
    try {
      const r = await fetch("/sessions/" + encodeURIComponent(id) + "/messages");
      if (!r.ok) throw new Error("Failed to load");
      const messages = await r.json();
      sessionId = id;
      messageHistory = messages.map(function (m) { return { role: m.role, content: m.content }; });

      while (messagesEl.lastChild) messagesEl.removeChild(messagesEl.lastChild);
      if (messageHistory.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.id = "emptyState";
        empty.innerHTML = "<p>此对话暂无消息。</p>";
        messagesEl.appendChild(empty);
        showEmpty();
      } else {
        hideEmpty();
        messageHistory.forEach(function (m) {
          addMessage(m.role, m.content, false);
        });
      }
      setActiveInSidebar(id);
    } catch (e) {
      console.error(e);
    }
  }

  async function loadModels() {
    try {
      const r = await fetch("/models");
      const data = await r.json();
      const models = data.models || [];
      const defaultModel = data.default || (models[0] || "");
      modelSelect.innerHTML = "";
      models.forEach(function (id) {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        if (id === defaultModel) opt.selected = true;
        modelSelect.appendChild(opt);
      });
      if (models.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No models";
        modelSelect.appendChild(opt);
      }
    } catch (e) {
      modelSelect.innerHTML = '<option value="">Failed to load models</option>';
      console.error(e);
    }
  }

  function renderMarkdown(text) {
    if (text == null || text === "") return "";
    var str = String(text);
    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      try {
        marked.setOptions && marked.setOptions({ breaks: true });
        var out = marked.parse(str);
        return typeof out === "string" ? out : (out && out.toString ? out.toString() : str);
      } catch (e) {
        return simpleMarkdown(str);
      }
    }
    return simpleMarkdown(str);
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /** 内联 Markdown 渲染（不依赖 CDN），保证始终按 Markdown 显示 */
  function simpleMarkdown(text) {
    if (!text) return "";
    var s = String(text);
    var out = [];
    var i = 0;
    var len = s.length;
    var inCodeBlock = false;
    var codeBlockLang = "";
    var codeBlockBuf = [];
    var lineStart = true;
    var listKind = null;
    var listBuf = [];

    function flushList() {
      if (listBuf.length) {
        out.push(listKind === "ul" ? "<ul>" : "<ol>");
        listBuf.forEach(function (line) {
          out.push("<li>");
          out.push(line);
          out.push("</li>");
        });
        out.push(listKind === "ul" ? "</ul>" : "</ol>");
        listBuf = [];
      }
      listKind = null;
    }

    function flushCodeBlock() {
      if (codeBlockBuf.length) {
        out.push("<pre><code>");
        out.push(escapeHtml(codeBlockBuf.join("\n")));
        out.push("</code></pre>");
        codeBlockBuf = [];
      }
      inCodeBlock = false;
    }

    var lines = s.split("\n");
    for (var li = 0; li < lines.length; li++) {
      var line = lines[li];
      var rest = line;
      if (inCodeBlock) {
        if (rest.indexOf("```") === 0) {
          flushCodeBlock();
          rest = rest.slice(3).trim();
        } else {
          codeBlockBuf.push(rest);
          continue;
        }
      }
      if (!inCodeBlock && rest.indexOf("```") === 0) {
        flushList();
        var after = rest.slice(3).trim();
        inCodeBlock = true;
        if (after) codeBlockBuf.push(after);
        continue;
      }
      var trimmed = rest.trim();
      if (trimmed === "") {
        flushList();
        flushCodeBlock();
        out.push("<br>");
        continue;
      }
      var headerMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (headerMatch) {
        flushList();
        var hLevel = headerMatch[1].length;
        var hContent = headerMatch[2];
        hContent = hContent.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>").replace(/`([^`]+)`/g, "<code>$1</code>");
        out.push("<h" + hLevel + ">");
        out.push(hContent);
        out.push("</h" + hLevel + ">");
        continue;
      }
      if (trimmed.indexOf("> ") === 0) {
        flushList();
        var qContent = trimmed.slice(2);
        qContent = inlineMarkdown(qContent);
        out.push("<blockquote>");
        out.push(qContent);
        out.push("</blockquote>");
        continue;
      }
      var ulMatch = trimmed.match(/^[\-\*]\s+(.+)$/);
      var olMatch = trimmed.match(/^\d+\.\s+(.+)$/);
      if (ulMatch) {
        if (listKind !== "ul") {
          flushList();
          listKind = "ul";
        }
        listBuf.push(inlineMarkdown(ulMatch[1]));
        continue;
      }
      if (olMatch) {
        if (listKind !== "ol") {
          flushList();
          listKind = "ol";
        }
        listBuf.push(inlineMarkdown(olMatch[1]));
        continue;
      }
      flushList();
      out.push("<p>");
      out.push(inlineMarkdown(trimmed));
      out.push("</p>");
    }
    flushList();
    flushCodeBlock();
    return out.join("");
  }

  function inlineMarkdown(seg) {
    var t = escapeHtml(seg);
    return t
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/_(.+?)_/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function addMessage(role, content, isError) {
    hideEmpty();
    const wrap = document.createElement("div");
    wrap.className = "msg " + role + (isError ? " error" : "");
    const header = document.createElement("div");
    header.className = "msg-header";
    const roleSpan = document.createElement("div");
    roleSpan.className = "role";
    roleSpan.textContent = role === "user" ? "You" : "Assistant";
    header.appendChild(roleSpan);
    wrap.appendChild(header);
    const contentEl = document.createElement("div");
    contentEl.className = "content markdown-body";
    contentEl.innerHTML = renderMarkdown(content);
    wrap.appendChild(contentEl);
    const copyPlainBtn = document.createElement("button");
    copyPlainBtn.type = "button";
    copyPlainBtn.className = "msg-copy";
    copyPlainBtn.setAttribute("aria-label", "复制纯文本");
    copyPlainBtn.textContent = "复制";
    copyPlainBtn.title = "复制为纯文本";
    copyPlainBtn.addEventListener("click", function () {
      var plainText = contentEl.innerText || contentEl.textContent || content;
      copyMessageContent(plainText, copyPlainBtn);
    });
    const copyMdBtn = document.createElement("button");
    copyMdBtn.type = "button";
    copyMdBtn.className = "msg-copy msg-copy-md";
    copyMdBtn.setAttribute("aria-label", "复制为 Markdown");
    copyMdBtn.textContent = "Markdown";
    copyMdBtn.title = "复制为 Markdown 原文";
    copyMdBtn.addEventListener("click", function () {
      copyMessageContent(content, copyMdBtn);
    });
    const footer = document.createElement("div");
    footer.className = "msg-footer";
    const actions = document.createElement("div");
    actions.className = "msg-copy-actions";
    actions.appendChild(copyPlainBtn);
    actions.appendChild(copyMdBtn);
    footer.appendChild(actions);
    wrap.appendChild(footer);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return contentEl;
  }

  function copyMessageContent(text, buttonEl) {
    if (typeof navigator.clipboard !== "undefined" && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        var orig = buttonEl.textContent;
        buttonEl.textContent = "已复制";
        buttonEl.disabled = true;
        setTimeout(function () {
          buttonEl.textContent = orig;
          buttonEl.disabled = false;
        }, 1500);
      }).catch(function () {
        fallbackCopyToClipboard(text, buttonEl);
      });
    } else {
      fallbackCopyToClipboard(text, buttonEl);
    }
  }

  function fallbackCopyToClipboard(text, buttonEl) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      buttonEl.textContent = "已复制";
      buttonEl.disabled = true;
      setTimeout(function () {
        buttonEl.textContent = "复制";
        buttonEl.disabled = false;
      }, 1500);
    } catch (e) {
      window.alert("复制失败");
    }
    document.body.removeChild(ta);
  }

  async function newChat() {
    try {
      const r = await fetch("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      sessionId = data.session_id;
      messageHistory = [];
      while (messagesEl.lastChild) messagesEl.removeChild(messagesEl.lastChild);
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.id = "emptyState";
      empty.innerHTML = "<p>选择模型并开始新对话，或从左侧恢复历史对话。</p><p>可附加文件或文件夹作为上下文。</p>";
      messagesEl.appendChild(empty);
      showEmpty();
      setActiveInSidebar(sessionId);
      await loadSessions();
    } catch (e) {
      addMessage("assistant", "Failed to create session: " + e.message, true);
    }
  }

  function buildUserContent() {
    let text = inputEl.value.trim();
    if (attachedFiles.length) {
      const parts = ["Attached files:\n"];
      attachedFiles.forEach(function (f) {
        parts.push("\n--- " + f.name + " ---\n");
        parts.push(f.content);
        parts.push("\n");
      });
      parts.push("\n--- Message ---\n");
      parts.push(text || "(no additional message)");
      text = parts.join("");
    }
    return text;
  }

  function clearAttached() {
    attachedFiles = [];
    attachedFilesEl.innerHTML = "";
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reject;
      reader.readAsText(file, "UTF-8");
    });
  }

  function addAttachedFiles(files) {
    const list = Array.from(files || []);
    Promise.all(
      list.map(function (file) {
        return readFile(file).then(
          function (content) {
            return { name: file.webkitRelativePath || file.name, content };
          },
          function () {
            return { name: file.name, content: "[Could not read as text]" };
          }
        );
      })
    ).then(function (results) {
      results.forEach(function (r) {
        attachedFiles.push(r);
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = r.name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", "Remove");
        remove.onclick = function () {
          attachedFiles = attachedFiles.filter(function (x) { return x !== r; });
          chip.remove();
        };
        chip.appendChild(remove);
        attachedFilesEl.appendChild(chip);
      });
    });
  }

  attachFilesBtn.addEventListener("click", function () {
    fileInput.click();
  });
  fileInput.addEventListener("change", function () {
    addAttachedFiles(fileInput.files);
    fileInput.value = "";
  });
  attachFolderBtn.addEventListener("click", function () {
    folderInput.click();
  });
  folderInput.addEventListener("change", function () {
    addAttachedFiles(folderInput.files);
    folderInput.value = "";
  });

  function setSendEnabled(enabled) {
    sendBtn.disabled = !enabled;
  }

  async function sendStream() {
    const content = buildUserContent();
    if (!content.trim() && !attachedFiles.length) return;
    const model = modelSelect.value || null;
    if (!sessionId) await newChat();
    if (!sessionId) return;

    messageHistory.push({ role: "user", content: buildUserContent() });
    addMessage("user", inputEl.value.trim() || "(sent attached files only)");
    inputEl.value = "";
    clearAttached();
    setSendEnabled(false);
    if (stopBtn) {
      stopBtn.style.display = "inline-flex";
      stopBtn.disabled = false;
    }

    const assistantWrap = document.createElement("div");
    assistantWrap.className = "msg assistant";
    const roleSpan = document.createElement("div");
    roleSpan.className = "role";
    roleSpan.textContent = "Assistant";
    const contentEl = document.createElement("div");
    contentEl.className = "content markdown-body";
    contentEl.innerHTML = "";
    const statsEl = document.createElement("div");
    statsEl.className = "msg-stats";
    const copyPlainBtn = document.createElement("button");
    copyPlainBtn.type = "button";
    copyPlainBtn.className = "msg-copy";
    copyPlainBtn.setAttribute("aria-label", "复制纯文本");
    copyPlainBtn.textContent = "复制";
    copyPlainBtn.title = "复制为纯文本";
    const copyMdBtn = document.createElement("button");
    copyMdBtn.type = "button";
    copyMdBtn.className = "msg-copy msg-copy-md";
    copyMdBtn.setAttribute("aria-label", "复制为 Markdown");
    copyMdBtn.textContent = "Markdown";
    copyMdBtn.title = "复制为 Markdown 原文";
    const footer = document.createElement("div");
    footer.className = "msg-footer";
    const actions = document.createElement("div");
    actions.className = "msg-copy-actions";
    actions.appendChild(copyPlainBtn);
    actions.appendChild(copyMdBtn);
    footer.appendChild(actions);
    assistantWrap.appendChild(roleSpan);
    assistantWrap.appendChild(contentEl);
    assistantWrap.appendChild(statsEl);
    assistantWrap.appendChild(footer);
    messagesEl.appendChild(assistantWrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    var deepThinkingEl = document.getElementById("deepThinkingCheckbox");
    var deepResearchEl = document.getElementById("deepResearchCheckbox");
    const deepThinking = deepThinkingEl ? deepThinkingEl.checked : false;
    const deepResearch = deepResearchEl ? deepResearchEl.checked : false;
    const body = {
      session_id: sessionId,
      messages: messageHistory.map(function (m) { return { role: m.role, content: m.content }; }),
      model: model,
      stream: true,
      deep_thinking: deepThinking,
      deep_research: deepResearch,
    };

    var streamStartMs = Date.now();
    function ensureStatsVisible() {
      var clientMs = Date.now() - streamStartMs;
      var current = (statsEl.textContent || "").trim();
      if (!current || current.indexOf("耗时") === -1) {
        statsEl.textContent = "总耗时: " + clientMs + " ms";
      }
      statsEl.style.display = "block";
    }
    streamAbortController = new AbortController();
    try {
      const r = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: streamAbortController.signal,
      });
      if (!r.ok) {
        const err = await r.text();
        contentEl.textContent = "Error: " + err;
        assistantWrap.classList.add("error");
        messageHistory.pop();
        setSendEnabled(true);
        if (stopBtn) stopBtn.style.display = "none";
        return;
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let full = "";
      var sseBuffer = "";
      function processSSEEvent(payload) {
        if (!payload || payload === "[DONE]") return;
        try {
          const j = JSON.parse(payload);
          if (j.usage != null || j.duration_ms != null) {
            var u = j.usage || {};
            var total = u.total_tokens != null ? u.total_tokens : (u.prompt_tokens || 0) + (u.completion_tokens || 0);
            var parts = [];
            if (total) parts.push("消耗 Token: " + total + (u.prompt_tokens != null ? " (输入 " + u.prompt_tokens + " / 输出 " + (u.completion_tokens || 0) + ")" : ""));
            if (j.duration_ms != null) parts.push("总耗时: " + j.duration_ms + " ms");
            statsEl.textContent = parts.join(" · ");
            statsEl.style.display = "block";
            messagesEl.scrollTop = messagesEl.scrollHeight;
            return;
          }
          const delta = (j.choices && j.choices[0] && j.choices[0].delta) ? j.choices[0].delta : {};
          if (delta.content) {
            full += delta.content;
            contentEl.innerHTML = renderMarkdown(full);
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
        } catch (_) {}
      }
      while (true) {
        const { value, done } = await reader.read();
        if (value && value.length) sseBuffer += dec.decode(value, { stream: true });
        var events = sseBuffer.split("\n\n");
        sseBuffer = events.pop() || "";
        for (var ei = 0; ei < events.length; ei++) {
          var line = events[ei].split("\n").find(function (l) { return l.startsWith("data: "); });
          if (line) processSSEEvent(line.slice(6));
        }
        if (done) break;
      }
      if (sseBuffer.trim()) {
        var line = sseBuffer.split("\n").find(function (l) { return l.startsWith("data: "); });
        if (line) processSSEEvent(line.slice(6));
      }
      ensureStatsVisible();
      copyPlainBtn.addEventListener("click", function () {
        copyMessageContent(contentEl.innerText || contentEl.textContent || full, copyPlainBtn);
      });
      copyMdBtn.addEventListener("click", function () {
        copyMessageContent(full, copyMdBtn);
      });
      messageHistory.push({ role: "assistant", content: full });
      loadSessions();
    } catch (e) {
      var isAbort = e.name === "AbortError";
      if (isAbort) {
        ensureStatsVisible();
        if (full) {
          contentEl.innerHTML = renderMarkdown(full) + ' <span class="stream-stopped-hint">(已停止)</span>';
          copyPlainBtn.addEventListener("click", function () {
            copyMessageContent(contentEl.innerText || contentEl.textContent || full, copyPlainBtn);
          });
          copyMdBtn.addEventListener("click", function () {
            copyMessageContent(full, copyMdBtn);
          });
          messageHistory.push({ role: "assistant", content: full });
          loadSessions();
        } else {
          contentEl.textContent = "(已停止)";
        }
      } else {
        contentEl.textContent = "Request failed: " + e.message;
        assistantWrap.classList.add("error");
        messageHistory.pop();
      }
    }
    streamAbortController = null;
    setSendEnabled(true);
    if (stopBtn) stopBtn.style.display = "none";
  }

  sendBtn.addEventListener("click", sendStream);
  if (stopBtn) {
    stopBtn.addEventListener("click", function () {
      if (streamAbortController) streamAbortController.abort();
    });
  }
  var newlineBtn = document.getElementById("newlineBtn");
  if (newlineBtn) {
    newlineBtn.addEventListener("click", function () {
      var start = inputEl.selectionStart;
      var end = inputEl.selectionEnd;
      var val = inputEl.value;
      inputEl.value = val.slice(0, start) + "\n" + val.slice(end);
      inputEl.selectionStart = inputEl.selectionEnd = start + 1;
      inputEl.focus();
    });
  }
  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendStream();
    }
  });

  newChatBtn.addEventListener("click", newChat);

  var newGroupBtn = document.getElementById("newGroupBtn");
  if (newGroupBtn) {
    newGroupBtn.addEventListener("click", function () {
      window.alert("分组功能即将推出，当前对话均在「全部」中。");
    });
  }

  inputEl.addEventListener("input", function () {
    setSendEnabled(true);
  });

  loadModels().then(function () {
    setSendEnabled(true);
  });
  loadSessions();
})();
