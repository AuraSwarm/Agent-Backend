# Changelog

## [Unreleased] – branch `sandbox/jy/split_function`

### Changed

- **Web UI 拆分**：静态资源与 Web 相关逻辑迁至独立仓库 Web-Service；本仓库仅提供 API。当通过 Aura 启动且设置环境变量 `WEB_UI_DIR`（指向 Web-Service/static）时，仍会挂载 `/` 与 `/static` 以兼容单端口访问。
- **API-only 默认行为**：未设置 `WEB_UI_DIR` 时，`GET /` 返回 404；Web UI 需单独访问 Web-Service（或由 Aura 注入 `WEB_UI_DIR`）。
- **EADDRINUSE 提示**：`serve` 在端口被占用时（errno 98）打印提示：先执行 `Aura down` 或使用 `--port` 指定其他端口。

### Fixed

- **GET /api/admin/roles/{role_name}**：查询最新提示词时对 `PromptVersion` 加 `.limit(1)`，避免多版本时 `MultipleResultsFound` 导致 500。
- **任务房间回复**：后台 `_process_task_and_reply` 异常时记录日志并写入一条 assistant 错误提示，避免无反馈。

### Removed

- `static/` 目录及 `index.html`、`app.js`、`style.css`。
- `tests/test_web_ui.py`、`tests/test_web_layout.py`（已迁移至 Web-Service）。

### Added

- **对话回复展示模型**：主对话页（/team/chat）与 GET /sessions/{id}/messages 返回每条 assistant 消息的 `model` 字段；POST /chat 持久化时写入使用的模型 ID；chat 页模型下拉在加载失败或暂无模型时显示「加载失败（状态码）」/「暂无模型」；支持 `<html data-api-base="...">` 以适配反向代理。
- **CLI 主入口测试**（`tests/test_cli_main.py`）：覆盖 `main()` 与 `python -m app.cli version`，验证 `version`、`--help`、`serve --help` 行为。
- **长时记忆存储**：当 `config/app.yaml`（或 Aura 注入的配置）中配置了阿里云 OSS（`oss_endpoint`、`oss_bucket`、`oss_access_key_*`）时，启动时初始化 OSS 作为 Memory-Base 长时存储后端；未配置时使用内存后端。`app.storage.long_term` 提供 `get_long_term_backend()`、`is_long_term_oss()`、`reset_long_term_backend()`（测试用）。
- **Chat 长时记忆注入**：当 OSS 已配置且请求带 `user_id`（或使用 `session_id`）时，从长时存储加载用户画像与相关知识三元组并作为 system 前缀注入对话。
- **POST /chat 可选 `user_id`**：用于长时记忆用户维度；详见 API.md。
- **短/中/长记忆机制测试**（`tests/test_memory_stages.py`）：短期（Session/Message 模型与 DB）、中期（SessionSummary/MessageArchive 与 archive 任务）、长期（backend 从配置创建、profile/knowledge 读写、is_long_term_oss）。
- **任务与角色对话**：任务房间 `POST /api/chat/room/{id}/message` 支持 @ 角色；仅当消息 @ 到有效角色时触发角色回复（对话能力）。`GET /api/chat/room/{id}/messages` 返回 `mentioned_roles`、`reply_by_role`（assistant 消息显示由哪个角色回复）。
- **对话能力为角色必备**：内置能力 `chat`（对话），每个角色列表与回复流程均包含；启动与 `POST /api/admin/roles/ensure-chat-ability` 可为历史角色补齐该能力。
- **能力 prompt_template**：自定义能力支持 `prompt_template`（提示词能力）；任务房间可识别并执行。`custom_abilities` 表迁移：启动与 `POST /api/admin/migrate-prompt-template` 或 Web 端「修复」按钮可补齐 `prompt_template` 列。
- **@ 解析支持空格**：@ 提及支持角色名含空格（如 `@Claude Analyst`），正则 `@([a-zA-Z0-9_]+(?:\s[a-zA-Z0-9_]+)*)`。
- **任务房间回复与前端**：回复失败时写入一条 assistant 错误提示；前端发送 @ 消息后轮询直至收到助手回复，并显示「正在回答中」占位与对应角色名。
