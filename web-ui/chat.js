(function () {
  function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function formatTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN');
  }

  async function loadModels() {
    var select = document.getElementById('chat-model-select');
    if (!select) return;
    var apiBase = (document.documentElement.getAttribute('data-api-base') || '').replace(/\/$/, '');
    var url = apiBase ? apiBase + '/api/models' : '/api/models';
    var r = await fetch(url);
    select.innerHTML = '';
    if (!r.ok) {
      var errOpt = document.createElement('option');
      errOpt.value = '';
      errOpt.textContent = '加载失败（' + r.status + '）';
      select.appendChild(errOpt);
      return;
    }
    var data = await r.json().catch(function () { return {}; });
    var models = (data && data.models) ? data.models : [];
    var defaultId = (data && data.default) ? data.default : (models[0] || '');
    models.forEach(function (m) {
      var opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      if (m === defaultId) opt.selected = true;
      select.appendChild(opt);
    });
    if (models.length === 0) {
      var emptyOpt = document.createElement('option');
      emptyOpt.value = '';
      emptyOpt.textContent = '暂无模型';
      select.appendChild(emptyOpt);
    }
  }

  function setChatInputVisible(visible) {
    var toolbar = document.getElementById('chat-toolbar');
    var area = document.getElementById('chat-input-area');
    var input = document.getElementById('chat-user-input');
    var btn = document.getElementById('chat-send-btn');
    if (toolbar) toolbar.setAttribute('aria-hidden', visible ? 'false' : 'true');
    if (area) area.setAttribute('aria-hidden', visible ? 'false' : 'true');
    if (input) input.disabled = !visible;
    if (btn) btn.disabled = !visible;
  }

  async function loadChatList() {
    var list = document.getElementById('chat-session-list');
    var loading = document.getElementById('chat-list-loading');
    if (loading) loading.remove();
    if (list) list.innerHTML = '';
    var r = await fetch('/sessions?scope=chat&limit=50');
    if (!r.ok) { if (list) list.innerHTML = '<div class="empty-state"><p>加载失败</p></div>'; return; }
    var sessions = await r.json();
    if (!sessions.length) { if (list) list.innerHTML = '<div class="empty-state"><p>暂无对话</p><p class="empty-state-hint">点击「新建对话」开始</p></div>'; return; }
    sessions.forEach(function (s) {
      var card = document.createElement('div');
      card.className = 'task-card chat-session-card';
      card.setAttribute('data-session-id', s.session_id);
      var title = (s.title && s.title.trim()) ? s.title : '未命名对话';
      card.innerHTML = '<div class="task-card-inner"><div class="task-title">' + escapeHtml(title) + '</div><div class="task-time">' + formatTime(s.updated_at) + '</div></div><button type="button" class="chat-convert-to-task-btn btn-outline-sm" data-session-id="' + escapeHtml(s.session_id) + '">转为任务</button>';
      card.addEventListener('click', function (e) {
        if (e.target.closest('.chat-convert-to-task-btn')) return;
        selectSession(s.session_id, title);
      });
      card.querySelector('.chat-convert-to-task-btn').addEventListener('click', function (e) { e.stopPropagation(); convertToTask(s.session_id); });
      list.appendChild(card);
    });
  }
  function selectSession(sessionId, title) {
    window._currentChatSessionId = sessionId;
    setChatInputVisible(true);
    document.querySelectorAll('.chat-session-card').forEach(function (el) { el.classList.toggle('active', el.getAttribute('data-session-id') === sessionId); });
    var h1 = document.querySelector('.chat-intro-header h1');
    if (h1) h1.textContent = title || '未命名对话';
    loadMessages(sessionId);
  }
  async function loadMessages(sessionId) {
    var container = document.getElementById('chat-main');
    if (!container) return;
    container.innerHTML = '<div class="empty-state"><p>加载中…</p></div>';
    var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/messages');
    if (!r.ok) { container.innerHTML = '<div class="empty-state"><p>加载失败</p></div>'; return; }
    var messages = await r.json();
    var html = '<div class="chat-messages-inner">';
    messages.forEach(function (m) {
      var roleLabel = m.role === 'user' ? '用户' : (m.role === 'assistant' ? 'Assistant' : escapeHtml(m.role));
      var modelLabel = (m.role === 'assistant' && m.model) ? '<span class="chat-msg-model" title="使用的模型">' + escapeHtml(m.model) + '</span>' : '';
      html += '<div class="chat-msg chat-msg-' + escapeHtml(m.role) + '"><span class="chat-msg-role">' + roleLabel + '</span>' + modelLabel + '<div class="chat-msg-content">' + escapeHtml(m.content || '') + '</div></div>';
    });
    html += '</div><div class="chat-convert-bar"><button type="button" id="btn-convert-current" class="btn-outline-sm">将此对话转为任务</button></div>';
    container.innerHTML = html;
    document.getElementById('btn-convert-current').addEventListener('click', function () { convertToTask(sessionId); });
  }
  async function convertToTask(sessionId) {
    var r = await fetch('/api/tasks/from-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) });
    if (!r.ok) { alert('转为任务失败'); return; }
    var task = await r.json();
    window.location.href = '/team/#task=' + task.id;
  }
  async function createChat() {
    var r = await fetch('/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '未命名对话' }) });
    if (!r.ok) { alert('新建对话失败'); return; }
    var data = await r.json();
    await loadChatList();
    if (data.session_id) selectSession(data.session_id, '未命名对话');
  }
  function showThinkingPlaceholder(container) {
    var inner = container.querySelector('.chat-messages-inner');
    if (inner) {
      var wrap = document.createElement('div');
      wrap.className = 'chat-msg chat-msg-thinking';
      wrap.innerHTML = '<span class="chat-msg-role">Assistant</span><div class="chat-msg-content">正在思考…</div>';
      inner.appendChild(wrap);
    } else {
      container.innerHTML = '<div class="chat-messages-inner"><div class="chat-msg chat-msg-thinking"><span class="chat-msg-role">Assistant</span><div class="chat-msg-content">正在思考…</div></div></div>';
    }
  }

  async function sendMessage() {
    var sessionId = window._currentChatSessionId;
    var input = document.getElementById('chat-user-input');
    var select = document.getElementById('chat-model-select');
    if (!sessionId || !input || !select) return;
    var text = (input.value && input.value.trim()) || '';
    if (!text) return;
    var messages = (window._currentChatMessages || []).slice();
    messages.push({ role: 'user', content: text });
    var model = select.value || undefined;
    var btn = document.getElementById('chat-send-btn');
    var container = document.getElementById('chat-main');
    if (btn) btn.disabled = true;
    showThinkingPlaceholder(container);
    input.value = '';
    var r = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        messages: messages,
        model: model || undefined,
        stream: false
      })
    });
    if (btn) btn.disabled = false;
    if (!r.ok) {
      await loadMessages(sessionId);
      var err = await r.json().catch(function () { return { detail: r.statusText }; });
      alert('发送失败：' + (err.detail || err.message || r.status));
      return;
    }
    await loadMessages(sessionId);
  }

  document.getElementById('btn-new-chat').addEventListener('click', createChat);
  var sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn) sendBtn.addEventListener('click', sendMessage);
  var chatInput = document.getElementById('chat-user-input');
  if (chatInput) {
    chatInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendMessage(); }
    });
  }
  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('chat-list-loading') && document.getElementById('chat-list-loading').remove();
    loadModels();
    loadChatList();
  });
})();
