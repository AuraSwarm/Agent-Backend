(function () {
  function closeModal() {
    var modal = document.getElementById('role-modal');
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    modal.style.display = 'none';
  }

  function openModal() {
    var modal = document.getElementById('role-modal');
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    modal.style.display = 'flex';
  }

  async function loadRoles() {
    var list = document.getElementById('roles-list');
    list.innerHTML = '';
    var response = await fetch('/api/admin/roles');
    if (!response.ok) {
      list.innerHTML = '<div class="empty-state empty-state-roles"><p class="empty-state-lead">加载失败</p><p class="empty-state-hint">请刷新页面重试</p></div>';
      return;
    }
    var roles = await response.json();
    if (!roles.length) {
      list.innerHTML = '<div class="empty-state empty-state-roles"><p class="empty-state-lead">暂无角色</p><p class="empty-state-hint">点击右上角「新建角色」创建第一个 AI 员工角色</p></div>';
      return;
    }
    roles.forEach(function (role) {
      var card = document.createElement('div');
      card.className = 'role-card';
      card.setAttribute('role', 'button');
      card.tabIndex = 0;
      card.innerHTML = '<div class="role-card-inner">' +
        '<div class="role-header"><h3>' + escapeHtml(role.name) + '</h3><span class="role-status ' + role.status + '">' + (role.status === 'enabled' ? '启用' : '禁用') + '</span></div>' +
        '<div class="role-description">' + escapeHtml(role.description || '无描述') + '</div>' +
        (role.default_model ? '<div class="role-model">模型: ' + escapeHtml(role.default_model) + '</div>' : '') +
        '<div class="role-abilities">' + (role.abilities || []).map(function (a) { return '<span class="ability-tag">' + escapeHtml(a) + '</span>'; }).join('') + '</div>' +
        '</div>' +
        '<button type="button" class="role-card-copy" aria-label="复制角色" title="复制角色配置">复制</button>' +
        '<button type="button" class="role-card-test" aria-label="测试角色" title="测试对话与能力">测试</button>' +
        '<button type="button" class="role-card-delete" aria-label="删除角色" title="删除角色">×</button>';
      card.addEventListener('click', function (e) {
        if (e.target.closest('.role-card-delete') || e.target.closest('.role-card-test') || e.target.closest('.role-card-copy')) return;
        openRoleEdit(role.name);
      });
      card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!e.target.closest('.role-card-delete') && !e.target.closest('.role-card-test') && !e.target.closest('.role-card-copy')) openRoleEdit(role.name); } });
      card.querySelector('.role-card-copy').addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); copyRole(role.name, this); });
      card.querySelector('.role-card-test').addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); testRole(role.name); });
      card.querySelector('.role-card-delete').addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); deleteRole(role.name); });
      list.appendChild(card);
    });
  }

  async function deleteRole(roleName) {
    if (!window.confirm('确定删除角色「' + roleName + '」？删除后无法恢复。')) return;
    var response = await fetch('/api/admin/roles/' + encodeURIComponent(roleName), { method: 'DELETE' });
    if (!response.ok) {
      alert('删除失败，请重试');
      return;
    }
    closeModal();
    await loadRoles();
  }

  async function copyRole(roleName, btnEl) {
    var response = await fetch('/api/admin/roles/' + encodeURIComponent(roleName));
    if (!response.ok) {
      alert('获取角色详情失败，无法复制');
      return;
    }
    var role = await response.json();
    var lines = [
      '角色名: ' + (role.name || ''),
      '描述: ' + (role.description || ''),
      '状态: ' + (role.status || 'enabled'),
      '绑定模型: ' + (role.default_model || '（使用全局默认）'),
      '绑定能力: ' + (role.abilities && role.abilities.length ? role.abilities.join(', ') : '无'),
      '系统提示词:',
      role.system_prompt || '(无)'
    ];
    var text = lines.join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      var orig = btnEl.textContent;
      btnEl.textContent = '已复制';
      btnEl.disabled = true;
      setTimeout(function () { btnEl.textContent = orig; btnEl.disabled = false; }, 1500);
    } else {
      alert('当前浏览器不支持复制，请手动复制：\n\n' + text);
    }
  }

  function openTestResultModal() {
    var modal = document.getElementById('role-test-result-modal');
    if (modal) {
      modal.classList.add('is-open');
      modal.setAttribute('aria-hidden', 'false');
      modal.style.display = 'flex';
    }
  }
  function closeTestResultModal() {
    var modal = document.getElementById('role-test-result-modal');
    if (modal) {
      modal.classList.remove('is-open');
      modal.setAttribute('aria-hidden', 'true');
      modal.style.display = 'none';
    }
  }

  async function testRole(roleName) {
    var bodyEl = document.getElementById('role-test-result-body');
    var titleEl = document.getElementById('role-test-result-title');
    if (titleEl) titleEl.textContent = '角色测试结果：' + roleName;
    if (bodyEl) bodyEl.innerHTML = '<p class="role-test-loading">测试中…（基础对话与能力检测可能需要几秒）</p>';
    openTestResultModal();
    var response = await fetch('/api/admin/roles/' + encodeURIComponent(roleName) + '/test', { method: 'POST' });
    var data = { dialogue_ok: false, dialogue_message: '', abilities: [] };
    if (response.ok) {
      data = await response.json();
    } else {
      var err = await response.json().catch(function () { return {}; });
      data.dialogue_message = '请求失败: ' + (err.detail || response.status);
    }
    var dialogueLabel = data.dialogue_ok ? '通过' : '未通过';
    var dialogueClass = data.dialogue_ok ? 'role-test-ok' : 'role-test-fail';
    var html = '<div class="role-test-section"><h3>基础对话</h3><p class="' + dialogueClass + '">' + dialogueLabel + '</p>';
    if (data.dialogue_message) html += '<p class="role-test-detail">' + escapeHtml(data.dialogue_message) + '</p>';
    html += '</div><div class="role-test-section"><h3>能力</h3><ul class="role-test-abilities">';
    (data.abilities || []).forEach(function (a) {
      var c = a.ok ? 'role-test-ok' : 'role-test-fail';
      html += '<li class="' + c + '"><span>' + escapeHtml(a.id) + '</span>: ' + (a.ok ? '通过' : '未通过');
      if (a.message) html += ' — ' + escapeHtml(a.message);
      html += '</li>';
    });
    html += '</ul></div>';
    if (bodyEl) bodyEl.innerHTML = html;
  }

  document.getElementById('role-test-result-close').addEventListener('click', closeTestResultModal);
  document.getElementById('role-test-result-modal').addEventListener('click', function (e) {
    if (e.target === document.getElementById('role-test-result-modal')) closeTestResultModal();
  });

  async function openRoleEdit(roleName) {
    var url = '/api/admin/roles/' + encodeURIComponent(roleName);
    var response = await fetch(url);
    if (!response.ok) {
      var detail = '';
      try {
        var err = await response.json();
        detail = (err.detail || response.statusText || String(response.status)).toString();
      } catch (_) {
        detail = response.status + ' ' + response.statusText;
      }
      alert('加载角色失败: ' + detail + '\n\n若为 404，请确认角色「' + roleName + '」是否存在（名称需完全一致，含空格与大小写）。');
      return;
    }
    var role = await response.json();
    document.getElementById('role-name').value = role.name;
    document.getElementById('role-name').readOnly = true;
    document.getElementById('role-description').value = role.description || '';
    document.getElementById('role-status').value = role.status || 'enabled';
    document.getElementById('role-prompt').value = role.system_prompt || '';
    await loadModelsSelect(role.default_model || '');
    await loadAbilitiesSelect(role.abilities || []);
    openModal();
    document.getElementById('modal-title').textContent = '编辑角色: ' + role.name;
  }

  async function loadModelsSelect(selectedId) {
    var response = await fetch('/api/models');
    if (!response.ok) return;
    var data = await response.json();
    var select = document.getElementById('role-model');
    select.innerHTML = '<option value="">（使用全局默认）</option>';
    (data.models || []).forEach(function (m) {
      var opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      if (m === selectedId) opt.selected = true;
      select.appendChild(opt);
    });
  }

  async function loadAbilitiesSelect(selectedIds) {
    var response = await fetch('/api/abilities');
    var abilities = await response.json();
    var container = document.getElementById('role-abilities-list');
    container.innerHTML = '';
    abilities.forEach(function (a) {
      var label = document.createElement('label');
      label.className = 'ability-checkbox-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.name = 'role-ability';
      cb.value = a.id;
      cb.checked = selectedIds.indexOf(a.id) !== -1;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(' ' + (a.name || a.id)));
      container.appendChild(label);
    });
  }

  function getSelectedAbilityIds() {
    var nodes = document.querySelectorAll('#role-abilities-list input[name="role-ability"]:checked');
    return Array.from(nodes).map(function (n) { return n.value; });
  }

  function setAllAbilitiesChecked(checked) {
    document.querySelectorAll('#role-abilities-list input[name="role-ability"]').forEach(function (cb) {
      cb.checked = checked;
    });
  }

  document.getElementById('role-abilities-select-all').addEventListener('click', function () {
    setAllAbilitiesChecked(true);
  });
  document.getElementById('role-abilities-deselect-all').addEventListener('click', function () {
    setAllAbilitiesChecked(false);
  });

  function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  document.getElementById('create-role-btn').addEventListener('click', function () {
    document.getElementById('role-name').value = '';
    document.getElementById('role-name').readOnly = false;
    document.getElementById('role-description').value = '';
    document.getElementById('role-status').value = 'enabled';
    document.getElementById('role-prompt').value = '';
    openModal();
    document.getElementById('modal-title').textContent = '新建 AI 员工角色';
    loadModelsSelect('');
    loadAbilitiesSelect([]);
  });

  async function reloadConfig() {
    var btn = document.getElementById('config-reload-btn');
    if (btn) { btn.disabled = true; btn.textContent = '刷新中…'; }
    var r = await fetch('/admin/reload', { method: 'POST' });
    if (btn) { btn.disabled = false; btn.textContent = '刷新配置'; }
    if (!r.ok) {
      alert('刷新配置失败：' + (r.statusText || '请重试'));
      return;
    }
    alert('配置已刷新。编辑角色时模型与能力选项将使用最新配置。');
  }
  document.getElementById('config-reload-btn').addEventListener('click', reloadConfig);

  function onCloseModal() { closeModal(); }
  document.getElementById('modal-close-btn').addEventListener('click', onCloseModal);
  document.getElementById('role-modal').addEventListener('click', function (e) {
    if (e.target === document.getElementById('role-modal')) onCloseModal();
  });

  document.getElementById('role-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var isNew = document.getElementById('modal-title').textContent.indexOf('新建') !== -1;
    var modelEl = document.getElementById('role-model');
    var defaultModel = modelEl && modelEl.value ? modelEl.value : null;
    var payload = {
      name: document.getElementById('role-name').value,
      description: document.getElementById('role-description').value,
      status: document.getElementById('role-status').value,
      abilities: getSelectedAbilityIds(),
      system_prompt: document.getElementById('role-prompt').value,
      default_model: defaultModel || undefined
    };
    var url = isNew ? '/api/admin/roles' : '/api/admin/roles/' + encodeURIComponent(payload.name);
    var method = isNew ? 'POST' : 'PUT';
    var body = isNew ? payload : { description: payload.description, status: payload.status, abilities: payload.abilities, system_prompt: payload.system_prompt, default_model: payload.default_model };
    var response = await fetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (response.ok) {
      closeModal();
      await loadRoles();
    } else {
      var err = await response.json().catch(function () { return {}; });
      alert('保存失败: ' + (err.detail || response.status));
    }
  });

  document.addEventListener('DOMContentLoaded', loadRoles);
})();
