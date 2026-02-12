# Changelog

## [Unreleased] – branch `sandbox/jy/split_function`

### Changed

- **Web UI 拆分**：静态资源与 Web 相关逻辑迁至独立仓库 Web-Service；本仓库仅提供 API。当通过 Aura 启动且设置环境变量 `WEB_UI_DIR`（指向 Web-Service/static）时，仍会挂载 `/` 与 `/static` 以兼容单端口访问。
- **API-only 默认行为**：未设置 `WEB_UI_DIR` 时，`GET /` 返回 404；Web UI 需单独访问 Web-Service（或由 Aura 注入 `WEB_UI_DIR`）。
- **EADDRINUSE 提示**：`serve` 在端口被占用时（errno 98）打印提示：先执行 `Aura down` 或使用 `--port` 指定其他端口。

### Removed

- `static/` 目录及 `index.html`、`app.js`、`style.css`。
- `tests/test_web_ui.py`、`tests/test_web_layout.py`（已迁移至 Web-Service）。

### Added

- **CLI 主入口测试**（`tests/test_cli_main.py`）：覆盖 `main()` 与 `python -m app.cli version`，验证 `version`、`--help`、`serve --help` 行为。
- **长时记忆存储**：当 `config/app.yaml`（或 Aura 注入的配置）中配置了阿里云 OSS（`oss_endpoint`、`oss_bucket`、`oss_access_key_*`）时，启动时初始化 OSS 作为 Memory-Base 长时存储后端；未配置时使用内存后端。`app.storage.long_term` 提供 `get_long_term_backend()`、`is_long_term_oss()`、`reset_long_term_backend()`（测试用）。
- **Chat 长时记忆注入**：当 OSS 已配置且请求带 `user_id`（或使用 `session_id`）时，从长时存储加载用户画像与相关知识三元组并作为 system 前缀注入对话。
- **POST /chat 可选 `user_id`**：用于长时记忆用户维度；详见 API.md。
- **短/中/长记忆机制测试**（`tests/test_memory_stages.py`）：短期（Session/Message 模型与 DB）、中期（SessionSummary/MessageArchive 与 archive 任务）、长期（backend 从配置创建、profile/knowledge 读写、is_long_term_oss）。
