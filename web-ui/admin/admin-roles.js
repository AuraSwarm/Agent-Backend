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
        '<div class="role-abilities">' + (role.abilities || []).map(function (a) { return '<span class="ability-tag">' + escapeHtml(a) + '</span>'; }).join('') + '</div>' +
        '</div>' +
        '<button type="button" class="role-card-delete" aria-label="删除角色" title="删除角色">×</button>';
      card.addEventListener('click', function (e) { if (!e.target.closest('.role-card-delete')) openRoleEdit(role.name); });
      card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!e.target.closest('.role-card-delete')) openRoleEdit(role.name); } });
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

  async function openRoleEdit(roleName) {
    var response = await fetch('/api/admin/roles/' + encodeURIComponent(roleName));
    if (!response.ok) {
      alert('加载角色失败，请重试');
      return;
    }
    var role = await response.json();
    document.getElementById('role-name').value = role.name;
    document.getElementById('role-name').readOnly = true;
    document.getElementById('role-description').value = role.description || '';
    document.getElementById('role-status').value = role.status || 'enabled';
    document.getElementById('role-prompt').value = role.system_prompt || '';
    await loadAbilitiesSelect(role.abilities || []);
    openModal();
    document.getElementById('modal-title').textContent = '编辑角色: ' + role.name;
  }

  async function loadAbilitiesSelect(selectedIds) {
    var response = await fetch('/api/abilities');
    var abilities = await response.json();
    var select = document.getElementById('role-abilities');
    select.innerHTML = '';
    abilities.forEach(function (a) {
      var opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.name || a.id;
      if (selectedIds.indexOf(a.id) !== -1) opt.selected = true;
      select.appendChild(opt);
    });
  }

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
    loadAbilitiesSelect([]);
  });

  function onCloseModal() { closeModal(); }
  document.getElementById('modal-close-btn').addEventListener('click', onCloseModal);
  document.getElementById('role-modal').addEventListener('click', function (e) {
    if (e.target === document.getElementById('role-modal')) onCloseModal();
  });

  document.getElementById('role-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var isNew = document.getElementById('modal-title').textContent.indexOf('新建') !== -1;
    var payload = {
      name: document.getElementById('role-name').value,
      description: document.getElementById('role-description').value,
      status: document.getElementById('role-status').value,
      abilities: Array.from(document.getElementById('role-abilities').selectedOptions).map(function (o) { return o.value; }),
      system_prompt: document.getElementById('role-prompt').value
    };
    var url = isNew ? '/api/admin/roles' : '/api/admin/roles/' + encodeURIComponent(payload.name);
    var method = isNew ? 'POST' : 'PUT';
    var body = isNew ? payload : { description: payload.description, status: payload.status, abilities: payload.abilities, system_prompt: payload.system_prompt };
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
