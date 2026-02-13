(function () {
  function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  async function loadModels() {
    var list = document.getElementById('models-list');
    var loading = document.getElementById('models-loading');
    if (loading) loading.style.display = 'block';
    list.innerHTML = '';
    var response = await fetch('/api/models');
    if (loading) loading.style.display = 'none';
    if (!response.ok) {
      list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">加载失败</p><p class="empty-state-hint">请检查后端配置与网络</p></div>';
      return;
    }
    var data = await response.json();
    var models = data.models || [];
    var defaultId = data.default || null;
    if (!models.length) {
      list.innerHTML = '<div class="empty-state"><p class="empty-state-lead">暂无配置模型</p><p class="empty-state-hint">在 config/models.yaml 的 chat_providers 下配置 models 列表</p></div>';
      return;
    }
    models.forEach(function (m) {
      var card = document.createElement('div');
      card.className = 'model-card';
      card.setAttribute('data-model-id', m);
      var isDefault = m === defaultId;
      var actionHtml = isDefault
        ? ''
        : '<button type="button" class="btn-outline-sm model-set-default-btn">设为默认</button>';
      card.innerHTML =
        '<div class="model-card-inner">' +
          '<div class="model-header">' +
            '<span class="model-id">' + escapeHtml(m) + '</span>' +
            (isDefault ? '<span class="model-badge model-badge-default">默认</span>' : '') +
          '</div>' +
          '<div class="model-status" data-status="pending">—</div>' +
          (actionHtml ? '<div class="model-card-actions">' + actionHtml + '</div>' : '') +
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
        statusEl.textContent = '未测试';
        return;
      }
      if (r.available) {
        statusEl.setAttribute('data-status', 'ok');
        statusEl.textContent = '可用 — ' + escapeHtml((r.message || '').slice(0, 60));
      } else {
        statusEl.setAttribute('data-status', 'fail');
        statusEl.textContent = '不可用 — ' + escapeHtml((r.message || '').slice(0, 80));
      }
    });
    if (btn) {
      btn.disabled = false;
      btn.textContent = '测试可用性';
    }
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

  document.getElementById('models-test-btn').addEventListener('click', runTest);
  document.getElementById('models-list').addEventListener('click', function (e) {
    var btn = e.target && e.target.closest('.model-set-default-btn');
    if (!btn) return;
    var card = btn.closest('.model-card');
    if (card) setDefault(card.getAttribute('data-model-id'));
  });
  document.addEventListener('DOMContentLoaded', loadModels);
})();
