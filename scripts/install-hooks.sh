#!/bin/sh
# 将 hooks/ 中的钩子安装到 .git/hooks/，支持长线迭代更新

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/hooks"
DEST="${ROOT}/.git/hooks"

if [ ! -d "$DEST" ]; then
  echo "错误: 未找到 .git/hooks，请在仓库根目录执行。" >&2
  exit 1
fi

for f in "$SRC"/*; do
  [ -f "$f" ] || continue
  [ "$(basename "$f")" = "README.md" ] && continue
  cp "$f" "$DEST/$(basename "$f")"
  chmod +x "$DEST/$(basename "$f")"
  echo "已安装: $(basename "$f")"
done

echo "Hooks 安装完成。"
