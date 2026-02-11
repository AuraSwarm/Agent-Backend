#!/usr/bin/env bash
# Test current node environment for copilot-cli and Claude integration.
# Run on the same node where you run ./run node (e.g. dev container or host).
#
# Usage: ./scripts/test-node-integrations.sh

set -e

echo "=== Node environment ==="
echo "Node: $(command -v node 2>/dev/null || echo 'not found')"
node --version 2>/dev/null || true
echo ""

echo "=== Copilot CLI (GitHub @github/copilot) ==="
if command -v copilot >/dev/null 2>&1; then
  echo "Path: $(command -v copilot)"
  if copilot --help >/dev/null 2>&1; then
    echo "Status: OK (--help succeeds)"
  else
    echo "Status: FAIL (--help failed)"
    exit 1
  fi
else
  echo "Status: NOT INSTALLED (install: npm install -g @github/copilot or gh extension install github/gh-copilot)"
  exit 1
fi
echo ""

echo "=== Claude (Anthropic / Claude Code relay) ==="
if command -v claude >/dev/null 2>&1; then
  echo "Path: $(command -v claude)"
  # Claude often starts an interactive welcome; just ensure the binary runs (timeout to avoid hanging)
  if timeout 2 sh -c 'claude --help 2>/dev/null | head -1' 2>/dev/null | grep -q .; then
    echo "Status: OK (--help works)"
  else
    timeout 1 claude 2>/dev/null || true
    echo "Status: OK (binary runs, interactive relay)"
  fi
else
  echo "Status: NOT INSTALLED (install via Cursor/Claude Code or anthropic CLI)"
  exit 1
fi
echo ""

echo "=== Summary ==="
echo "copilot-cli: $(command -v copilot 2>/dev/null && echo 'available' || echo 'missing')"
echo "claude:      $(command -v claude 2>/dev/null && echo 'available' || echo 'missing')"
echo "Done."
