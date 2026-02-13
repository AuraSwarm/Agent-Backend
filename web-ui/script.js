(function () {
  function setInputAreaEnabled(enabled) {
    document.getElementById('user-input').disabled = !enabled;
    document.getElementById('send-btn').disabled = !enabled;
  }

  async function loadTaskList() {
    const taskList = document.getElementById('task-list');
    const loadingEl = document.getElementById('task-list-loading');
    if (loadingEl) loadingEl.remove();
    taskList.innerHTML = '';
    var response;
    var tasks = [];
    response = await fetch('/api/tasks');
    if (!response.ok) {
      taskList.innerHTML = '<div class="empty-state empty-state-sidebar"><p>加载失败</p><p class="empty-state-hint">请刷新页面重试</p></div>';
      return;
    }
    tasks = await response.json();
    if (!tasks.length) {
      taskList.innerHTML = '<div class="empty-state empty-state-sidebar"><p>暂无任务</p><p class="empty-state-hint">新任务创建后将显示在此</p></div>';
      setTaskBatchActionsVisible(false);
      return;
    }
    setTaskBatchActionsVisible(true);
    tasks.forEach(function (task) {
      const card = document.createElement('div');
      card.className = 'task-card';
      card.setAttribute('role', 'button');
      card.setAttribute('data-task-id', task.id);
      card.tabIndex = 0;
      card.innerHTML = '<label class="task-card-checkbox-wrap" onclick="event.stopPropagation()">' +
        '<input type="checkbox" class="task-card-checkbox" data-task-id="' + escapeHtml(task.id) + '" aria-label="选择任务">' +
        '</label>' +
        '<div class="task-card-inner">' +
        '<div class="task-title">' + escapeHtml(task.title) + '</div>' +
        '<div class="task-status ' + task.status + '">' + (task.status === 'completed' ? '已完成' : '进行中') + '</div>' +
        '<div class="task-time">' + formatTime(task.last_updated) + '</div>' +
        '</div>' +
        '<button type="button" class="task-card-delete" data-task-id="' + escapeHtml(task.id) + '" aria-label="删除该任务" title="删除任务">×</button>';
      card.addEventListener('click', function (e) {
        if (e.target.closest('.task-card-delete') || e.target.closest('.task-card-checkbox-wrap')) return;
        openTask(task.id);
      });
      card.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          if (!e.target.closest('.task-card-delete') && !e.target.closest('.task-card-checkbox-wrap')) openTask(task.id);
        }
      });
      var delBtn = card.querySelector('.task-card-delete');
      delBtn.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); deleteTask(task.id); });
      var cb = card.querySelector('.task-card-checkbox');
      cb.addEventListener('click', function (e) { e.stopPropagation(); });
      cb.addEventListener('change', function () { updateTaskBatchDeleteButton(); });
      taskList.appendChild(card);
    });
    updateTaskCardSelection(window._currentSessionId);
    document.getElementById('task-select-all').checked = false;
    updateTaskBatchDeleteButton();
  }

  function setTaskBatchActionsVisible(visible) {
    var el = document.getElementById('task-batch-actions');
    if (el) el.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }

  function getSelectedTaskIds() {
    var ids = [];
    document.querySelectorAll('.task-card-checkbox:checked').forEach(function (cb) {
      var id = cb.getAttribute('data-task-id');
      if (id) ids.push(id);
    });
    return ids;
  }

  function updateTaskBatchDeleteButton() {
    var ids = getSelectedTaskIds();
    var btn = document.getElementById('btn-batch-delete-tasks');
    var selectAll = document.getElementById('task-select-all');
    if (btn) btn.disabled = ids.length === 0;
    if (selectAll) {
      var total = document.querySelectorAll('.task-card-checkbox').length;
      selectAll.checked = total > 0 && ids.length === total;
      selectAll.indeterminate = total > 0 && ids.length > 0 && ids.length < total;
    }
  }

  async function batchDeleteTasks() {
    var ids = getSelectedTaskIds();
    if (!ids.length) return;
    if (!window.confirm('确定删除选中的 ' + ids.length + ' 个任务？删除后无法恢复。')) return;
    var wasCurrent = window._currentSessionId && ids.indexOf(window._currentSessionId) !== -1;
    for (var i = 0; i < ids.length; i++) {
      var r = await fetch('/sessions/' + encodeURIComponent(ids[i]), { method: 'DELETE' });
      if (!r.ok) console.error('Delete failed for', ids[i]);
    }
    await loadTaskList();
    if (wasCurrent) {
      window._currentSessionId = null;
      setInputAreaEnabled(false);
      setTaskActionsVisible(false);
      document.getElementById('task-title').textContent = '请选择任务';
      document.getElementById('task-status').className = 'status-indicator status-placeholder';
      document.getElementById('task-status').textContent = '—';
      document.getElementById('task-status').setAttribute('aria-hidden', 'true');
      var container = document.getElementById('chat-container');
      container.innerHTML = '<div class="empty-state empty-state-main"><div class="empty-state-icon" aria-hidden="true">📋</div><p class="empty-state-lead">从左侧选择一条任务</p><p class="empty-state-hint">选择任务后可在此查看群聊记录并在下方发送反馈。</p></div>';
    }
  }

  function updateTaskCardSelection(sessionId) {
    document.querySelectorAll('.task-card').forEach(function (card) {
      card.classList.toggle('active', card.getAttribute('data-task-id') === sessionId);
      card.setAttribute('aria-current', card.getAttribute('data-task-id') === sessionId ? 'true' : 'false');
    });
  }

  async function fetchTask(sessionId) {
    const r = await fetch('/api/tasks');
    const tasks = await r.json();
    return tasks.find(function (t) { return t.id === sessionId; }) || { id: sessionId, title: '未命名任务', status: 'in_progress' };
  }

  function setTaskActionsVisible(visible) {
    var el = document.getElementById('task-actions');
    if (el) el.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }

  async function openTask(sessionId) {
    window.history.pushState(null, '', '/team/#task=' + sessionId);
    window._currentSessionId = sessionId;
    updateTaskCardSelection(sessionId);
    setInputAreaEnabled(true);
    setTaskActionsVisible(true);
    const task = await fetchTask(sessionId);
    document.getElementById('task-title').textContent = task.title;
    var statusEl = document.getElementById('task-status');
    statusEl.className = 'status-indicator ' + (task.status === 'completed' ? 'green' : 'blue');
    statusEl.textContent = task.status === 'completed' ? '✅ 已完成' : '🔄 进行中';
    statusEl.removeAttribute('aria-hidden');
    showChatLoading();
    var ok = await loadMessages(sessionId);
    if (!ok) return;
    document.getElementById('user-input').focus();
  }

  function showChatLoading() {
    var container = document.getElementById('chat-container');
    container.innerHTML = '<div class="empty-state empty-state-loading"><p class="loading-inline">加载消息中…</p></div>';
  }

  function showChatError(message, hint) {
    var container = document.getElementById('chat-container');
    container.innerHTML = '<div class="empty-state empty-state-chat"><p class="empty-state-lead">' + escapeHtml(message) + '</p><p class="empty-state-hint">' + escapeHtml(hint || '请刷新或重新选择任务') + '</p></div>';
  }

  async function loadMessages(sessionId) {
    var container = document.getElementById('chat-container');
    var response = await fetch('/api/chat/room/' + sessionId + '/messages');
    if (!response.ok) {
      showChatError('加载消息失败', '请刷新或重新选择任务');
      return false;
    }
    var messages = await response.json();
    container.innerHTML = '';
    if (!messages.length) {
      container.innerHTML = '<div class="empty-state empty-state-chat"><p>暂无消息</p><p class="empty-state-hint">在下方输入框发送反馈，将显示在此</p></div>';
      return true;
    }
    var roleMap = { user: '您', assistant: '助手', system: '系统' };
    messages.forEach(function (msg) {
      var el = document.createElement('div');
      el.className = 'message ' + (msg.role === 'user' ? 'user' : 'ai');
      el.innerHTML = '<div class="message-header"><span class="role-icon" aria-hidden="true">💬</span><span class="role-name">' + escapeHtml(roleMap[msg.role] || msg.role) + '</span><span class="message-time">' + formatTime(msg.timestamp) + '</span></div><div class="message-content">' + escapeHtml(msg.message) + '</div>';
      container.appendChild(el);
    });
    container.scrollTop = container.scrollHeight;
    return true;
  }

  function formatTime(timestamp) {
    if (!timestamp) return '';
    var date = new Date(timestamp);
    var now = new Date();
    var diff = now - date;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
    return date.toLocaleDateString();
  }

  function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return String(unsafe)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  async function deleteTask(taskId) {
    if (!window.confirm('确定删除该任务？删除后无法恢复。')) return;
    var wasCurrent = window._currentSessionId === taskId;
    var r = await fetch('/sessions/' + encodeURIComponent(taskId), { method: 'DELETE' });
    if (!r.ok) { alert('删除失败，请重试'); return; }
    await loadTaskList();
    if (wasCurrent) {
      window._currentSessionId = null;
      setInputAreaEnabled(false);
      setTaskActionsVisible(false);
      document.getElementById('task-title').textContent = '请选择任务';
      document.getElementById('task-status').className = 'status-indicator status-placeholder';
      document.getElementById('task-status').textContent = '—';
      document.getElementById('task-status').setAttribute('aria-hidden', 'true');
      var container = document.getElementById('chat-container');
      container.innerHTML = '<div class="empty-state empty-state-main"><div class="empty-state-icon" aria-hidden="true">📋</div><p class="empty-state-lead">从左侧选择一条任务</p><p class="empty-state-hint">选择任务后可在此查看群聊记录并在下方发送反馈。</p></div>';
    }
  }

  async function clearMessages() {
    var sessionId = window._currentSessionId;
    if (!sessionId) return;
    if (!window.confirm('确定清空该任务下的所有消息？此操作不可恢复。')) return;
    var r = await fetch('/api/chat/room/' + encodeURIComponent(sessionId) + '/messages', { method: 'DELETE' });
    if (!r.ok) { alert('清空失败，请重试'); return; }
    showChatLoading();
    await loadMessages(sessionId);
  }

  document.getElementById('send-btn').addEventListener('click', sendMessage);
  document.getElementById('user-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  document.getElementById('btn-clear-messages').addEventListener('click', clearMessages);
  document.getElementById('btn-delete-task').addEventListener('click', function () {
    if (window._currentSessionId) deleteTask(window._currentSessionId);
  });
  var taskSelectAll = document.getElementById('task-select-all');
  if (taskSelectAll) {
    taskSelectAll.addEventListener('change', function () {
      var checked = taskSelectAll.checked;
      document.querySelectorAll('.task-card-checkbox').forEach(function (cb) { cb.checked = checked; });
      updateTaskBatchDeleteButton();
    });
  }
  var btnBatchDeleteTasks = document.getElementById('btn-batch-delete-tasks');
  if (btnBatchDeleteTasks) btnBatchDeleteTasks.addEventListener('click', batchDeleteTasks);

  async function sendMessage() {
    var sessionId = window._currentSessionId;
    if (!sessionId) return;
    var input = document.getElementById('user-input');
    var message = input.value.trim();
    if (!message) return;
    var btn = document.getElementById('send-btn');
    btn.disabled = true;
    btn.textContent = '发送中…';
    var response = await fetch('/api/chat/room/' + sessionId + '/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: 'user', message: message, message_type: 'user_message' })
    });
    btn.disabled = false;
    btn.textContent = '发送';
    if (!response.ok) {
      alert('发送失败，请重试');
      return;
    }
    input.value = '';
    await loadMessages(sessionId);
  }

  document.addEventListener('DOMContentLoaded', async function () {
    setInputAreaEnabled(false);
    await loadTaskList();
    var hash = window.location.hash || '';
    var m = hash.match(/task=([a-f0-9-]+)/i);
    if (m) {
      openTask(m[1]);
    } else {
      var firstCard = document.querySelector('.task-card');
      if (firstCard) firstCard.click();
      else {
        document.getElementById('task-status').setAttribute('aria-hidden', 'true');
        setTaskActionsVisible(false);
      }
    }
  });
})();
