(function () {
  function makeInlineRename(titleEl, getCurrentText, onSave) {
    if (!titleEl || !onSave) return;
    titleEl.classList.add('editable-title');
    titleEl.setAttribute('title', '双击重命名');
    titleEl.addEventListener('dblclick', function (e) {
      e.stopPropagation();
      e.preventDefault();
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'inline-rename-input';
      input.value = getCurrentText();
      titleEl.style.display = 'none';
      titleEl.parentNode.insertBefore(input, titleEl.nextSibling);
      input.focus();
      input.select();
      function finish() {
        var val = (input.value && input.value.trim()) || '';
        input.remove();
        titleEl.style.display = '';
        if (val !== getCurrentText()) onSave(val);
      }
      function cancel() {
        input.remove();
        titleEl.style.display = '';
      }
      input.addEventListener('blur', function () { finish(); });
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') { ev.preventDefault(); finish(); }
        if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
      });
    });
  }

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
      var roles = task.assignee_roles || (task.assignee_role ? [task.assignee_role] : []);
      var roleHtml = roles.length ? roles.map(function (r) { return '<span class="task-card-role">' + escapeHtml(r) + '</span>'; }).join('') : '';
      card.innerHTML = '<label class="task-card-checkbox-wrap" onclick="event.stopPropagation()">' +
        '<input type="checkbox" class="task-card-checkbox" data-task-id="' + escapeHtml(task.id) + '" aria-label="选择任务">' +
        '</label>' +
        '<div class="task-card-inner">' +
        '<div class="task-title">' + escapeHtml(task.title) + '</div>' +
        (roleHtml ? '<div class="task-card-role-wrap">' + roleHtml + '</div>' : '') +
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
      var titleDiv = card.querySelector('.task-title');
      if (titleDiv) {
        makeInlineRename(titleDiv, function () { return titleDiv.textContent; }, function (newTitle) {
          fetch('/api/tasks/' + encodeURIComponent(task.id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle })
          }).then(function (r) {
            if (r.ok) {
              titleDiv.textContent = newTitle || '未命名任务';
              if (window._currentSessionId === task.id) {
                var h = document.getElementById('task-title');
                if (h) h.textContent = newTitle || '未命名任务';
              }
            }
          });
        });
      }
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
      var r = await fetch('/api/tasks/' + encodeURIComponent(ids[i]), { method: 'DELETE' });
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
    return tasks.find(function (t) { return t.id === sessionId; }) || { id: sessionId, title: '未命名任务', status: 'in_progress', assignee_roles: [] };
  }

  var _rolesCache = null;
  async function fetchRoles() {
    if (_rolesCache) return _rolesCache;
    var r = await fetch('/api/admin/roles');
    _rolesCache = r.ok ? await r.json() : [];
    return _rolesCache;
  }

  function setupMentionDropdown() {
    var input = document.getElementById('user-input');
    var dropdown = document.getElementById('mention-dropdown');
    if (!input || !dropdown) return;
    var selectedIndex = 0;
    var filteredList = [];

    function getQuery() {
      var text = input.value;
      var pos = input.selectionStart != null ? input.selectionStart : text.length;
      var before = text.substring(0, pos);
      var atIndex = before.lastIndexOf('@');
      if (atIndex === -1) return { atIndex: -1, query: '' };
      var query = before.substring(atIndex + 1);
      if (/\s/.test(query)) return { atIndex: -1, query: '' };
      return { atIndex: atIndex, query: query };
    }

    function hideDropdown() {
      dropdown.setAttribute('aria-hidden', 'true');
      dropdown.innerHTML = '';
      filteredList = [];
    }

    function showDropdown(roles) {
      filteredList = roles;
      selectedIndex = 0;
      dropdown.innerHTML = '';
      roles.forEach(function (r, i) {
        var name = r.name || r;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mention-dropdown-item';
        btn.setAttribute('role', 'option');
        btn.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
        btn.innerHTML = '<span class="mention-prefix">@</span>' + escapeHtml(name);
        btn.dataset.roleName = name;
        btn.addEventListener('click', function () {
          pickRole(name);
        });
        dropdown.appendChild(btn);
      });
      dropdown.setAttribute('aria-hidden', 'false');
    }

    function pickRole(roleName) {
      var q = getQuery();
      if (q.atIndex === -1) return;
      var text = input.value;
      var pos = input.selectionStart != null ? input.selectionStart : text.length;
      var before = text.substring(0, q.atIndex);
      var after = text.substring(pos);
      input.value = before + '@' + roleName + ' ' + after;
      var newPos = before.length + roleName.length + 2;
      input.setSelectionRange(newPos, newPos);
      input.focus();
      hideDropdown();
    }

    function updateMentionList() {
      var q = getQuery();
      if (q.atIndex === -1) {
        hideDropdown();
        return;
      }
      fetchRoles().then(function (roles) {
        var q2 = getQuery();
        if (q2.atIndex === -1) return;
        var queryLower = (q2.query || '').toLowerCase();
        var enabled = roles.filter(function (r) { return (r.status || 'enabled') !== 'disabled'; });
        var filtered = enabled.filter(function (r) {
          var name = (r.name || r).toLowerCase();
          return name.indexOf(queryLower) !== -1;
        });
        if (filtered.length === 0) {
          hideDropdown();
          return;
        }
        showDropdown(filtered);
      });
    }

    input.addEventListener('input', function () {
      updateMentionList();
    });
    input.addEventListener('keydown', function (e) {
      if (dropdown.getAttribute('aria-hidden') !== 'true' && filteredList.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          selectedIndex = (selectedIndex + 1) % filteredList.length;
          dropdown.querySelectorAll('.mention-dropdown-item').forEach(function (el, i) {
            el.setAttribute('aria-selected', i === selectedIndex ? 'true' : 'false');
          });
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          selectedIndex = selectedIndex <= 0 ? filteredList.length - 1 : selectedIndex - 1;
          dropdown.querySelectorAll('.mention-dropdown-item').forEach(function (el, i) {
            el.setAttribute('aria-selected', i === selectedIndex ? 'true' : 'false');
          });
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          var name = (filteredList[selectedIndex] && (filteredList[selectedIndex].name || filteredList[selectedIndex])) || '';
          if (name) pickRole(name);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          hideDropdown();
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    input.addEventListener('blur', function () {
      setTimeout(hideDropdown, 150);
    });
    document.addEventListener('click', function (e) {
      if (dropdown.getAttribute('aria-hidden') === 'true') return;
      if (!input.contains(e.target) && !dropdown.contains(e.target)) hideDropdown();
    });
  }

  function setTaskActionsVisible(visible) {
    var el = document.getElementById('task-actions');
    if (el) el.setAttribute('aria-hidden', visible ? 'false' : 'true');
    var wrap = document.getElementById('task-assignee-wrap');
    if (wrap) wrap.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }

  async function updateTaskAssigneeSelect(taskId, currentRoles) {
    var selectEl = document.getElementById('task-assignee-role');
    if (!selectEl) return;
    var roles = await fetchRoles();
    selectEl.innerHTML = '';
    var selected = Array.isArray(currentRoles) ? currentRoles : (currentRoles ? [currentRoles] : []);
    roles.forEach(function (r) {
      var name = r.name || r;
      var opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      opt.selected = selected.indexOf(name) !== -1;
      selectEl.appendChild(opt);
    });
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
    await updateTaskAssigneeSelect(sessionId, task.assignee_roles || (task.assignee_role ? [task.assignee_role] : []));
    var selectEl = document.getElementById('task-assignee-role');
    if (selectEl) {
      selectEl.onchange = function () {
        var selected = Array.from(selectEl.selectedOptions || []).map(function (o) { return o.value; });
        fetch('/api/tasks/' + encodeURIComponent(sessionId), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ assignee_roles: selected })
        }).then(function (r) {
          if (r.ok) loadTaskList();
        });
      };
    }
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
      var displayName = (msg.role === 'assistant' && msg.reply_by_role) ? msg.reply_by_role : (roleMap[msg.role] || msg.role);
      var mentionLine = (msg.mentioned_roles && msg.mentioned_roles.length) ? ' <span class="message-mentions">发给 ' + msg.mentioned_roles.map(function (r) { return '@' + escapeHtml(r); }).join(' ') + '</span>' : '';
      el.innerHTML = '<div class="message-header"><span class="role-icon" aria-hidden="true">💬</span><span class="role-name">' + escapeHtml(displayName) + '</span>' + mentionLine + '<span class="message-time">' + formatTime(msg.timestamp) + '</span></div><div class="message-content">' + escapeHtml(msg.message) + '</div>';
      container.appendChild(el);
    });
    container.scrollTop = container.scrollHeight;
    return true;
  }

  function appendAnsweringPlaceholder(roleName) {
    var container = document.getElementById('chat-container');
    if (!container || container.querySelector('.message-answering-placeholder')) return;
    var displayRole = (roleName && roleName.trim()) ? roleName.trim() : '助手';
    var el = document.createElement('div');
    el.className = 'message ai message-answering-placeholder';
    el.setAttribute('aria-live', 'polite');
    el.innerHTML = '<div class="message-header"><span class="role-icon" aria-hidden="true">💬</span><span class="role-name">' + escapeHtml(displayRole) + '</span><span class="message-time">—</span></div><div class="message-content message-content-answering">正在回答中…</div>';
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  }

  function getFirstMentionedRole(messageText) {
    if (!messageText || typeof messageText !== 'string') return null;
    var m = messageText.match(/@([a-zA-Z0-9_]+(?:\s[a-zA-Z0-9_]+)*)/);
    return m ? m[1] : null;
  }

  function removeAnsweringPlaceholder() {
    var container = document.getElementById('chat-container');
    if (!container) return;
    var placeholder = container.querySelector('.message-answering-placeholder');
    if (placeholder) placeholder.remove();
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
    var r = await fetch('/api/tasks/' + encodeURIComponent(taskId), { method: 'DELETE' });
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

  async function createTask() {
    var btn = document.getElementById('btn-new-task');
    if (btn) btn.disabled = true;
    var r = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '未命名任务' })
    });
    if (btn) btn.disabled = false;
    if (!r.ok) { alert('创建任务失败，请重试'); return; }
    var data = await r.json();
    await loadTaskList();
    if (data.id) openTask(data.id);
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
  document.getElementById('btn-new-task').addEventListener('click', createTask);
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
    var hasAt = (message.indexOf('@') !== -1);
    if (hasAt) {
      btn.textContent = '等待回复…';
      btn.disabled = true;
      var replyingRole = getFirstMentionedRole(message);
      appendAnsweringPlaceholder(replyingRole);
      var list0 = await fetch('/api/chat/room/' + sessionId + '/messages').then(function (r) { return r.json(); });
      var lastCount = list0.length;
      var pollMax = 40;
      var polled = 0;
      while (polled < pollMax) {
        await new Promise(function (resolve) { setTimeout(resolve, 1500); });
        var ok = await loadMessages(sessionId);
        if (!ok) break;
        var list = await fetch('/api/chat/room/' + sessionId + '/messages').then(function (r) { return r.json(); });
        if (list.length > lastCount && list[list.length - 1].role === 'assistant') {
          removeAnsweringPlaceholder();
          await loadMessages(sessionId);
          break;
        }
        appendAnsweringPlaceholder(replyingRole);
        polled++;
      }
      if (polled >= pollMax) removeAnsweringPlaceholder();
      btn.disabled = false;
      btn.textContent = '发送';
    }
  }

  document.addEventListener('DOMContentLoaded', async function () {
    setInputAreaEnabled(false);
    setupMentionDropdown();
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

  /* 任务分组：与主对话页「对话分组」一致的 UI 交互 */
  var taskGroupList = document.getElementById('taskGroupList');
  if (taskGroupList) {
    taskGroupList.querySelectorAll('.group-item').forEach(function (el) {
      el.addEventListener('click', function () {
        taskGroupList.querySelectorAll('.group-item').forEach(function (item) { item.classList.remove('active'); });
        el.classList.add('active');
      });
    });
  }
  var btnNewTaskGroup = document.getElementById('btn-new-task-group');
  if (btnNewTaskGroup) {
    btnNewTaskGroup.addEventListener('click', function () {
      window.alert('任务分组功能即将开放');
    });
  }
})();
