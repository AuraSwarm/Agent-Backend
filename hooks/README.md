# Git Hooks（长线迭代更新）

本目录存放可版本化的 Git 钩子，便于随仓库一起长线迭代更新。

## 安装

在仓库根目录执行（会覆盖 `.git/hooks` 中同名钩子）：

```bash
./scripts/install-hooks.sh
```

或手动复制并赋予执行权限：

```bash
cp hooks/post-merge .git/hooks/post-merge
chmod +x .git/hooks/post-merge
```

## 当前钩子

| 钩子        | 触发时机     | 用途说明           |
|-------------|--------------|--------------------|
| `post-merge` | `git pull` 成功后 | 可在此做拉取后的自动化（如提示规则更新） |

## 迭代方式

- 在 `hooks/` 中新增或修改脚本后，重新运行 `./scripts/install-hooks.sh` 即可生效。
- 建议将 hook 逻辑保持简单，复杂流程可调用 `scripts/` 下的脚本。
