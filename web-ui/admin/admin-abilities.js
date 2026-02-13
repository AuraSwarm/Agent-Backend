(function () {
  function escapeHtml(s) {
    if (!s) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function abilityToRoles(roles) {
    var map = {};
    roles.forEach(function (role) {
      (role.abilities || []).forEach(function (aid) {
        if (!map[aid]) map[aid] = [];
        if (map[aid].indexOf(role.name) === -1) map[aid].push(role.name);
      });
    });
    return map;
  }

  function setLoading(loading) {
    var listEl = document.getElementById('abilities-list');
    if (listEl) listEl.setAttribute('aria-busy', loading ? 'true' : 'false');
  }

  var abilityModal = document.getElementById('ability-modal');
  var abilityForm = document.getElementById('ability-form');
  var editingId = null;
  var lastAbilitiesList = [];

  function openAbilityModal(isNew) {
    editingId = isNew ? null : null;
    document.getElementById('ability-modal-title').textContent = isNew ? '新建能力' : '编辑能力';
    var baseWrap = document.getElementById('ability-base-wrap');
    baseWrap.style.display = isNew ? 'block' : 'none';
    var baseSelect = document.getElementById('ability-base');
    baseSelect.innerHTML = '<option value="">— 不基于现有 —</option>';
    if (isNew && lastAbilitiesList.length) {
      lastAbilitiesList.forEach(function (a) {
        var opt = document.createElement('option');
        opt.value = a.id;
        opt.textContent = (a.name || a.id) + ' (' + a.id + ')';
        baseSelect.appendChild(opt);
      });
    }
    baseSelect.value = '';
    document.getElementById('ability-id').value = '';
    document.getElementById('ability-id').readOnly = !isNew;
    document.getElementById('ability-id').disabled = !isNew;
    document.getElementById('ability-name').value = '';
    document.getElementById('ability-description').value = '';
    document.getElementById('ability-command').value = '';
    abilityModal.classList.add('is-open');
    abilityModal.style.display = 'flex';
    abilityModal.setAttribute('aria-hidden', 'false');
  }

  function onAbilityBaseChange() {
    var baseSelect = document.getElementById('ability-base');
    var id = baseSelect.value;
    if (!id) return;
    var a = lastAbilitiesList.find(function (x) { return x.id === id; });
    if (!a) return;
    document.getElementById('ability-name').value = a.name || '';
    document.getElementById('ability-description').value = a.description || '';
    var cmd = a.command;
    document.getElementById('ability-command').value = Array.isArray(cmd) ? cmd.join('\n') : (cmd || '');
  }

  function closeAbilityModal() {
    abilityModal.classList.remove('is-open');
    abilityModal.style.display = 'none';
    abilityModal.setAttribute('aria-hidden', 'true');
    editingId = null;
  }

  function openEditAbility(ability) {
    editingId = ability.id;
    document.getElementById('ability-modal-title').textContent = '编辑能力';
    document.getElementById('ability-id').value = ability.id;
    document.getElementById('ability-id').readOnly = true;
    document.getElementById('ability-id').disabled = true;
    document.getElementById('ability-name').value = ability.name || '';
    document.getElementById('ability-description').value = ability.description || '';
    var cmd = ability.command;
    document.getElementById('ability-command').value = Array.isArray(cmd) ? cmd.join('\n') : (cmd || '');
    abilityModal.classList.add('is-open');
    abilityModal.style.display = 'flex';
    abilityModal.setAttribute('aria-hidden', 'false');
  }

  function commandTextToArray(text) {
    return text.trim().split(/\n/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  async function submitAbility(e) {
    e.preventDefault();
    var idEl = document.getElementById('ability-id');
    var nameEl = document.getElementById('ability-name');
    var descEl = document.getElementById('ability-description');
    var cmdEl = document.getElementById('ability-command');
    var id = idEl.value.trim();
    var name = nameEl.value.trim();
    var description = descEl.value.trim();
    var command = commandTextToArray(cmdEl.value);
    if (!id) {
      alert('请填写能力 ID');
      return;
    }
    if (!name) {
      alert('请填写名称');
      return;
    }
    if (!command.length) {
      alert('请填写至少一个命令参数');
      return;
    }
    if (editingId !== null) {
      var putRes = await fetch('/api/abilities/' + encodeURIComponent(editingId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, description: description, command: command })
      });
      if (!putRes.ok) {
        var err = await putRes.json().catch(function () { return {}; });
        alert('更新失败: ' + (err.detail || putRes.status));
        return;
      }
    } else {
      var postRes = await fetch('/api/abilities', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, name: name, description: description, command: command })
      });
      if (!postRes.ok) {
        var err = await postRes.json().catch(function () { return {}; });
        alert('创建失败: ' + (err.detail || postRes.status));
        return;
      }
    }
    closeAbilityModal();
    await loadAbilities();
  }

  async function deleteAbility(abilityId) {
    if (!window.confirm('确定删除该能力？删除后无法恢复，且依赖此能力的角色将不再拥有该能力。')) return;
    var r = await fetch('/api/abilities/' + encodeURIComponent(abilityId), { method: 'DELETE' });
    if (!r.ok) {
      var err = await r.json().catch(function () { return {}; });
      alert('删除失败: ' + (err.detail || r.status));
      return;
    }
    await loadAbilities();
  }

  async function loadAbilities() {
    var listEl = document.getElementById('abilities-list');
    setLoading(true);

    var abilitiesRes = await fetch('/api/abilities');
    var rolesRes = await fetch('/api/admin/roles');

    setLoading(false);
    var loadingEl = document.getElementById('abilities-loading');
    if (loadingEl) loadingEl.remove();
    listEl.innerHTML = '';

    if (!abilitiesRes.ok) {
      listEl.innerHTML =
        '<div class="empty-state empty-state-abilities">' +
        '<div class="empty-state-icon" aria-hidden="true">⚠️</div>' +
        '<p class="empty-state-lead">加载能力列表失败</p>' +
        '<p class="empty-state-hint">请检查网络或后端服务后刷新页面重试</p>' +
        '</div>';
      return;
    }
    if (!rolesRes.ok) {
      listEl.innerHTML =
        '<div class="empty-state empty-state-abilities">' +
        '<div class="empty-state-icon" aria-hidden="true">⚠️</div>' +
        '<p class="empty-state-lead">加载角色列表失败</p>' +
        '<p class="empty-state-hint">请检查网络或后端服务后刷新页面重试</p>' +
        '</div>';
      return;
    }

    var abilities = await abilitiesRes.json();
    lastAbilitiesList = abilities;
    var roles = await rolesRes.json();
    var abToRoles = abilityToRoles(roles);

    if (!abilities.length) {
      listEl.innerHTML =
        '<div class="empty-state empty-state-abilities">' +
        '<div class="empty-state-icon" aria-hidden="true">🔧</div>' +
        '<p class="empty-state-lead">暂无能力</p>' +
        '<p class="empty-state-hint">点击右上角「新建能力」添加，或可在 config/models.yaml 的 local_tools 中配置</p>' +
        '</div>';
      return;
    }

    abilities.forEach(function (a) {
      var roleNames = abToRoles[a.id] || [];
      var isCustom = a.source === 'custom';
      var card = document.createElement('article');
      card.className = 'ability-card';
      card.setAttribute('aria-label', '能力 ' + escapeHtml(a.name || a.id));
      var roleLinksHtml = roleNames.length
        ? roleNames.map(function (name) {
            return '<a href="/team/admin/roles.html" class="ability-role-pill">' + escapeHtml(name) + '</a>';
          }).join('')
        : '<span class="ability-no-roles">暂无角色绑定</span>';
      var sourceBadge = '<span class="ability-source-badge ' + (isCustom ? 'custom' : 'config') + '">' + (isCustom ? '自定义' : 'config') + '</span>';
      var actionsHtml = isCustom
        ? '<div class="ability-card-actions">' +
            '<button type="button" class="btn-outline-sm ability-btn-edit">编辑</button>' +
            '<button type="button" class="btn-outline-sm btn-danger ability-btn-delete">删除</button>' +
          '</div>'
        : '';
      card.innerHTML =
        '<div class="ability-card-inner">' +
          '<div class="ability-card-head">' +
            '<span class="ability-id-badge">' + escapeHtml(a.id) + '</span>' +
            sourceBadge +
            (roleNames.length ? '<span class="ability-role-count">' + roleNames.length + ' 个角色</span>' : '') +
          '</div>' +
          '<h3 class="ability-name">' + escapeHtml(a.name || a.id) + '</h3>' +
          (a.description ? '<p class="ability-description">' + escapeHtml(a.description) + '</p>' : '') +
          '<div class="ability-bound-roles">' +
            '<span class="ability-bound-label">绑定的角色</span>' +
            '<div class="ability-role-pills">' + roleLinksHtml + '</div>' +
          '</div>' +
          actionsHtml +
        '</div>';
      listEl.appendChild(card);
      if (isCustom) {
        var editBtn = card.querySelector('.ability-btn-edit');
        var delBtn = card.querySelector('.ability-btn-delete');
        editBtn.addEventListener('click', function () { openEditAbility(a); });
        delBtn.addEventListener('click', function () { deleteAbility(a.id); });
      }
    });
  }

  async function refreshAbilitiesList() {
    var btn = document.getElementById('ability-refresh-btn');
    btn.disabled = true;
    btn.textContent = '刷新中…';
    var r = await fetch('/admin/reload', { method: 'POST' });
    btn.disabled = false;
    btn.textContent = '刷新能力列表';
    if (!r.ok) {
      alert('刷新失败，请重试');
      return;
    }
    await loadAbilities();
  }

  async function repairPromptTemplate() {
    var btn = document.getElementById('ability-repair-btn');
    if (!btn) return;
    btn.disabled = true;
    var origText = btn.textContent;
    btn.textContent = '修复中…';
    var r = await fetch('/api/admin/migrate-prompt-template', { method: 'POST' });
    btn.disabled = false;
    btn.textContent = origText;
    var body = {};
    try { body = await r.json(); } catch (_) {}
    if (r.ok) {
      alert((body.detail || body.message || '修复完成') + '\n可点击「刷新能力列表」重新加载。');
      await loadAbilities();
    } else {
      alert('修复失败: ' + (body.detail || r.statusText || r.status));
    }
  }

  document.getElementById('ability-repair-btn').addEventListener('click', repairPromptTemplate);
  document.getElementById('ability-refresh-btn').addEventListener('click', refreshAbilitiesList);
  document.getElementById('ability-create-btn').addEventListener('click', function () { openAbilityModal(true); });
  document.getElementById('ability-modal-close').addEventListener('click', closeAbilityModal);
  document.getElementById('ability-base').addEventListener('change', onAbilityBaseChange);
  abilityModal.addEventListener('click', function (e) {
    if (e.target === abilityModal) closeAbilityModal();
  });
  abilityForm.addEventListener('submit', submitAbility);

  document.addEventListener('DOMContentLoaded', loadAbilities);
})();
