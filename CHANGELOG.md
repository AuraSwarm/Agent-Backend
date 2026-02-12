# Changelog

## [Unreleased] – branch `sandbox/jy/split_function` (2026-02-12)

### Changed

- **Web UI 拆分**：静态资源与 Web 相关逻辑迁至独立仓库 Web-Service；本仓库仅提供 API。当通过 Aura 启动且设置环境变量 `WEB_UI_DIR`（指向 Web-Service/static）时，仍会挂载 `/` 与 `/static` 以兼容单端口访问。
- **API-only 默认行为**：未设置 `WEB_UI_DIR` 时，`GET /` 返回 404；Web UI 需单独访问 Web-Service（或由 Aura 注入 `WEB_UI_DIR`）。
- **EADDRINUSE 提示**：`serve` 在端口被占用时（errno 98）打印提示：先执行 `Aura down` 或使用 `--port` 指定其他端口。

### Removed

- `static/` 目录及 `index.html`、`app.js`、`style.css`。
- `tests/test_web_ui.py`、`tests/test_web_layout.py`（已迁移至 Web-Service）。

### Added

- **CLI 主入口测试**（`tests/test_cli_main.py`）：覆盖 `main()` 与 `python -m app.cli version`，验证 `version`、`--help`、`serve --help` 行为。
