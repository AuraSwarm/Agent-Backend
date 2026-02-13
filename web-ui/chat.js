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
      html += '<div class="chat-msg"><span class="chat-msg-role">' + escapeHtml(m.role) + '</span><div class="chat-msg-content">' + escapeHtml(m.content || '') + '</div></div>';
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
  document.getElementById('btn-new-chat').addEventListener('click', createChat);
  document.addEventListener('DOMContentLoaded', function () { document.getElementById('chat-list-loading') && document.getElementById('chat-list-loading').remove(); loadChatList(); });
})();
