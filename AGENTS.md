# Agent instructions for Agent-Backend

- Keep debugging and improving until `./run node` works on this node: run it, fix any errors (config, script, or app), then run again. Do not stop after one failure.
- Node mode: `./run node` or `./run` (default). Local mode: `./run local`. Both start the DB (when localhost:5432) and the backend.
- Use `config/app.yaml` for database_url (localhost:5432 for auto-start), host, and port. Run tests with `pytest tests/ -v`.
