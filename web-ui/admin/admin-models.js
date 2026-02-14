(function () {
  function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /** 模型清单中的显示名：cursor-local → Cursor-local，copilot-local → Copilot-local */
  function modelDisplayName(modelId) {
    if (!modelId) return '';
    var map = { 'cursor-local': 'Cursor-local', 'copilot-local': 'Copilot-local' };
    return map[modelId] || modelId;
  }

  async function loadModels() {
    var list = document.getElementById('models-list');
    var loading = document.getElementById('models-loading');
    if (loading) loading.style.display = 'block';
    if (list) list.innerHTML = '';
    var response;
    var data;
    try {
      var controller = new AbortController();
      var timeoutId = setTimeout(function () { controller.abort(); }, 15000);
      response = await fetch('/api/models', { signal: controller.signal });
      clearTimeout(timeoutId);
      if (!response.ok) {
        if (loading) loading.style.display = 'none';
        if (list) list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">加载失败</p><p class="empty-state-hint">请检查后端配置与网络（' + response.status + '）</p></div>';
        return;
      }
      data = await response.json();
    } catch (err) {
      if (loading) loading.style.display = 'none';
      var msg = (err && err.name === 'AbortError') ? '请求超时，请检查后端是否正常' : (err && err.message) || '网络错误';
      if (list) list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">加载失败</p><p class="empty-state-hint">' + escapeHtml(msg) + '</p></div>';
      return;
    }
    if (loading) loading.style.display = 'none';
    var models = (data && data.models) ? data.models : [];
    var defaultId = data.default || null;
    if (!models.length) {
      if (list) list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">暂无配置模型</p><p class="empty-state-hint">在 config/models.yaml 的 chat_providers 下配置 models 列表</p></div>';
      return;
    }
    if (list) list.innerHTML = '';
    models.forEach(function (m) {
      var card = document.createElement('div');
      card.className = 'model-card';
      card.setAttribute('data-model-id', m);
      var isDefault = m === defaultId;
      var actions = ['<button type="button" class="btn-outline-sm model-test-one-btn">测试</button>'];
      if (!isDefault) actions.push('<button type="button" class="btn-outline-sm model-set-default-btn">设为默认</button>');
      var actionHtml = '<div class="model-card-actions">' + actions.join('') + '</div>';
      card.innerHTML =
        '<div class="model-card-inner">' +
          '<div class="model-header">' +
            '<span class="model-id">' + escapeHtml(modelDisplayName(m)) + '</span>' +
            (isDefault ? '<span class="model-badge model-badge-default">默认</span>' : '') +
          '</div>' +
          '<div class="model-status" data-status="pending">—</div>' +
          actionHtml +
        '</div>';
      list.appendChild(card);
    });
  }

  async function runTest() {
    var btn = document.getElementById('models-test-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '测试中…';
    }
    var list = document.getElementById('models-list');
    var cards = list.querySelectorAll('.model-card');
    cards.forEach(function (card) {
      var statusEl = card.querySelector('.model-status');
      if (statusEl) {
        statusEl.setAttribute('data-status', 'testing');
        statusEl.textContent = '测试中…';
      }
    });
    var response = await fetch('/api/admin/models/test', { method: 'POST' });
    var data = response.ok ? await response.json().catch(function () { return { results: [] }; }) : { results: [] };
    var results = data.results || [];
    var byId = {};
    results.forEach(function (r) {
      byId[r.model_id] = r;
    });
    cards.forEach(function (card) {
      var id = card.getAttribute('data-model-id');
      var statusEl = card.querySelector('.model-status');
      if (!statusEl) return;
      var r = byId[id];
      if (!r) {
        statusEl.setAttribute('data-status', 'unknown');
        card.removeAttribute('data-fail-message');
        statusEl.textContent = '未测试';
        return;
      }
      if (r.available) {
        statusEl.setAttribute('data-status', 'ok');
        card.removeAttribute('data-fail-message');
        statusEl.textContent = '可用 — ' + escapeHtml((r.message || '').slice(0, 60));
      } else {
        statusEl.setAttribute('data-status', 'fail');
        card.setAttribute('data-fail-message', r.message || '');
        statusEl.innerHTML = '不可用 — ' + escapeHtml((r.message || '').slice(0, 80)) + ' <button type="button" class="link-like model-fail-detail-link">详情</button>';
      }
    });
    if (btn) {
      btn.disabled = false;
      btn.textContent = '测试可用性';
    }
  }

  async function runTestOne(modelId) {
    modelId = String(modelId || '').trim();
    if (!modelId) return;
    var card = document.querySelector('.model-card[data-model-id="' + modelId + '"]');
    if (!card) return;
    var statusEl = card.querySelector('.model-status');
    var testBtn = card.querySelector('.model-test-one-btn');
    if (statusEl) {
      statusEl.setAttribute('data-status', 'testing');
      statusEl.textContent = '测试中…';
    }
    if (testBtn) testBtn.disabled = true;
    var response;
    var data = { results: [] };
    try {
      response = await fetch('/api/admin/models/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId })
      });
      data = await response.json().catch(function () { return { results: [] }; });
      if (!response.ok && !data.results) {
        data.detail = data.detail || data.message || ('HTTP ' + response.status);
      }
    } catch (err) {
      response = { ok: false };
      data = { results: [], detail: (err && err.message) || '网络错误' };
    }
    var results = data.results || [];
    var r = results.length === 1 ? results[0] : (results.find(function (x) { return x.model_id === modelId; }) || results[0]);
    if (testBtn) testBtn.disabled = false;
    if (!statusEl) return;
    if (!r) {
      statusEl.setAttribute('data-status', 'unknown');
      card.removeAttribute('data-fail-message');
      statusEl.textContent = response && response.ok ? '未返回结果' : (data.detail || data.message || '请求失败');
      return;
    }
    if (r.available) {
      statusEl.setAttribute('data-status', 'ok');
      card.removeAttribute('data-fail-message');
      statusEl.textContent = '可用 — ' + escapeHtml((r.message || '').slice(0, 60));
    } else {
      statusEl.setAttribute('data-status', 'fail');
      card.setAttribute('data-fail-message', r.message || '');
      statusEl.innerHTML = '不可用 — ' + escapeHtml((r.message || '').slice(0, 80)) + ' <button type="button" class="link-like model-fail-detail-link">详情</button>';
    }
  }

  function openFailDetailModal(modelId, message) {
    var modal = document.getElementById('model-fail-modal');
    if (!modal) return;
    document.getElementById('model-fail-modal-model').textContent = modelId || '';
    var pre = document.getElementById('model-fail-modal-message');
    pre.textContent = message || '';
    modal.setAttribute('aria-hidden', 'false');
    modal.classList.add('is-open');
    modal.style.display = 'flex';
  }

  function closeFailDetailModal() {
    var modal = document.getElementById('model-fail-modal');
    if (!modal) return;
    modal.setAttribute('aria-hidden', 'true');
    modal.classList.remove('is-open');
    modal.style.display = 'none';
  }

  async function reloadConfig() {
    var btn = document.getElementById('models-reload-config-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '刷新中…';
    }
    var response = await fetch('/admin/reload', { method: 'POST' });
    if (btn) {
      btn.disabled = false;
      btn.textContent = '刷新配置';
    }
    if (!response.ok) {
      var err = await response.json().catch(function () { return {}; });
      alert('刷新配置失败：' + (err.detail || err.message || response.statusText));
      return;
    }
    await loadModels();
    alert('配置已刷新，模型列表已更新。');
  }

  async function setDefault(modelId) {
    var btn = document.querySelector('.model-card[data-model-id="' + modelId + '"] .model-set-default-btn');
    if (btn) btn.disabled = true;
    var response = await fetch('/api/admin/models/default', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelId })
    });
    if (btn) btn.disabled = false;
    if (!response.ok) {
      var err = await response.json().catch(function () { return {}; });
      alert('设置失败：' + (err.detail || response.statusText));
      return;
    }
    await loadModels();
  }

  document.getElementById('models-reload-config-btn').addEventListener('click', reloadConfig);
  document.getElementById('models-test-btn').addEventListener('click', runTest);
  document.getElementById('models-list').addEventListener('click', function (e) {
    var card = e.target && e.target.closest('.model-card') ? e.target.closest('.model-card') : null;
    if (!card) return;
    var modelId = card.getAttribute('data-model-id');
    if (e.target && e.target.closest('.model-test-one-btn')) {
      runTestOne(modelId);
      return;
    }
    if (e.target && e.target.closest('.model-fail-detail-link')) {
      e.preventDefault();
      var msg = card.getAttribute('data-fail-message') || '';
      openFailDetailModal(modelId, msg);
      return;
    }
    if (e.target && e.target.closest('.model-set-default-btn')) {
      setDefault(modelId);
    }
  });
  var failModalClose = document.getElementById('model-fail-modal-close');
  if (failModalClose) failModalClose.addEventListener('click', closeFailDetailModal);
  document.getElementById('model-fail-modal').addEventListener('click', function (e) {
    if (e.target === this) closeFailDetailModal();
  });
  document.addEventListener('DOMContentLoaded', function () {
    var list = document.getElementById('models-list');
    var loading = document.getElementById('models-loading');
    function showLoadError(msg) {
      if (loading) loading.style.display = 'none';
      if (list) list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">加载异常</p><p class="empty-state-hint">' + escapeHtml(msg) + '</p></div>';
    }
    loadModels().catch(function (err) {
      showLoadError((err && err.message) || String(err));
    });
  });
})();
