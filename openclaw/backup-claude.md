# backup-claude.sh — Claude + OpenClaw 跨机器双向同步

## 它解决什么问题

你有两台 Mac（机器 A 用户名 `cuiyang`，机器 B 用户名 `ycui`），都在用 Claude Code 和 OpenClaw。你希望：

1. 两台机器的 Claude 记忆、配置、会话数据保持同步
2. 在 A 上积累的项目记忆，在 B 上也能被 Claude 读到（反之亦然）
3. 全自动，每天定时跑，不需要手动操作

难点在于：Claude Code 按**绝对路径的编码名**存储 per-project 记忆。同一个项目 `umu/csrs` 在两台机器上的记忆目录名不同：

```
机器 A: ~/.claude/projects/-Users-cuiyang-projects-umu-csrs/memory/
机器 B: ~/.claude/projects/-Users-ycui-projects-umu-csrs/memory/
```

Claude 只读与本机路径匹配的那个目录，所以即使文件同步过来了，另一台机器的记忆也不会被使用。

## 整体方案

### 第一层：双向同步（backup-claude.sh）

通过一个**共享 git 仓库**（`~/projects/migrate_to_new_device/`）做中转，两台机器各自运行同一个脚本：

```
┌──────────┐     rsync      ┌──────────┐     git      ┌──────────┐     rsync      ┌──────────┐
│  本地数据  │ ──backup──→  │  备份目录  │ ──push──→  │  远端仓库  │ ──pull──→   │  备份目录  │ ──restore──→ 本地数据
│ ~/.claude │               │ claude-   │             │  GitHub   │              │ claude-   │
│ ~/.openclaw│              │ backups/  │             │           │              │ backups/  │
└──────────┘               └──────────┘             └──────────┘              └──────────┘
     机器 A                                                                         机器 B
```

脚本每次运行的 5 个阶段：

| 阶段 | 做什么 | 说明 |
|------|--------|------|
| 1. Backup | 本地 → 备份目录 | `rsync --update`，只覆盖更旧的文件 |
| 2. Commit | git add + commit | 把本机变更存入 git |
| 3. Pull | git pull + 冲突解决 | 合并对方机器的变更（冲突时调用 AI 解决） |
| 4. Restore | 备份目录 → 本地 | 把合并后的结果写回本地（同样 `--update`） |
| 5. Push | git push | 上传合并结果 |

### 第二层：symlink 解决记忆路径不一致

选 `cuiyang` 作为**标准编码路径**。只在机器 B（ycui）上建立 symlink：

```
机器 B 的 ~/.claude/projects/:
  -Users-cuiyang-projects-umu-csrs/     ← 真实目录（两台机器共享的记忆数据）
  -Users-ycui-projects-umu-csrs         → -Users-cuiyang-projects-umu-csrs  (symlink)
```

效果：
- 机器 B 上 Claude Code 打开项目时，查找 `-Users-ycui-...`，通过 symlink 实际读写 `-Users-cuiyang-...`
- 两台机器的记忆都写入同一个 `cuiyang` 编码的目录
- 同步时只有真实目录在流转，方向一致，不会冲突

**机器 A（cuiyang）不需要任何改动。**

## 同步的三类数据

### 1) Claude Code 全局 — `~/.claude/`
- 备份到：`claude-backups/claude-global/`
- 内容：全局配置、per-project 记忆（`projects/` 下的 memory 目录）、transcripts
- 排除：`statsig/`、`.git/`

### 2) Claude Code 各项目 — 每个项目工作目录下的 `.claude/`
- 备份到：`claude-backups/claude-projects/<safe-name>/`
- 内容：CLAUDE.md、settings.json 等项目级配置
- 项目列表来自 `~/.claude/projects/` 的目录名，通过 `resolve_path()` 还原为实际路径

### 3) OpenClaw — `~/.openclaw/` + LaunchAgents
- 备份到：`openclaw-mirror/`
- 内容：配置、凭据、会话、workspace/记忆、LaunchAgents plist
- 排除：`logs/`、`canvas/`、`browser/`、`cache/`、`tmp/`、`.git/`

## 机器 B（ycui）初始化 symlink

在机器 B 上首次运行以下脚本，将所有 `ycui` 编码的记忆目录替换为指向 `cuiyang` 编码的 symlink：

```bash
#!/bin/bash
set -euo pipefail

CANONICAL_USER="cuiyang"
LOCAL_USER="$(whoami)"
PROJECTS_DIR="${HOME}/.claude/projects"

if [ "$LOCAL_USER" = "$CANONICAL_USER" ]; then
    echo "本机就是标准机器，不需要建 symlink。"
    exit 0
fi

LOCAL_PREFIX="-Users-${LOCAL_USER}-"
CANONICAL_PREFIX="-Users-${CANONICAL_USER}-"

cd "$PROJECTS_DIR"

for local_dir in ${LOCAL_PREFIX}*/; do
    [ -d "$local_dir" ] || continue
    # 跳过已经是 symlink 的
    [ -L "${local_dir%/}" ] && continue

    canonical_dir="${local_dir/$LOCAL_PREFIX/$CANONICAL_PREFIX}"

    # 确保标准目录存在
    mkdir -p "$canonical_dir"

    # 把本机记忆合并到标准目录（只覆盖更旧的）
    rsync -a --update "$local_dir" "$canonical_dir"

    # 替换为 symlink
    rm -rf "$local_dir"
    ln -sf "${canonical_dir%/}" "${local_dir%/}"

    echo "✓ ${local_dir%/} → ${canonical_dir%/}"
done

echo "初始化完成。后续新项目需手动建 symlink 或重新运行此脚本。"
```

保存为 `init-symlinks.sh`，在机器 B 上运行一次即可。之后每次在机器 B 上打开新项目，Claude 会创建新的 `ycui` 编码真实目录，需要重新运行此脚本将其替换为 symlink。

## 运行方式

手动运行：

```bash
${HOME}/projects/my_scripts/openclaw/backup-claude.sh
```

日志：`${HOME}/projects/logs/backup-claude.log`

## 定时任务（LaunchAgent）

- plist：`~/Library/LaunchAgents/ai.openclaw.backup-claude.plist`
- label：`ai.openclaw.backup-claude`
- 当前计划：每天 02:00 运行一次

```bash
# 查看
launchctl print "gui/$(id -u)/ai.openclaw.backup-claude" | sed -n '1,120p'

# 重新加载
launchctl bootout "gui/$(id -u)/ai.openclaw.backup-claude" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/ai.openclaw.backup-claude.plist"
```

## 依赖

- `resolve-conflicts-with-ai.sh`（同目录）：git pull 冲突时的 AI 自动解决脚本

## 关键目录

| 路径 | 说明 |
|------|------|
| `~/projects/migrate_to_new_device/` | 共享 git 仓库根目录 |
| `~/projects/migrate_to_new_device/claude-backups/claude-global/` | 全局 Claude 数据备份 |
| `~/projects/migrate_to_new_device/claude-backups/claude-projects/` | 各项目 .claude/ 备份 |
| `~/projects/migrate_to_new_device/openclaw-mirror/` | OpenClaw 数据备份 |
| `~/projects/logs/backup-claude.log` | 运行日志 |

## OpenClaw 目录说明（迁移时哪些要留、哪些可丢）

### 必须迁移

- `~/.openclaw/openclaw.json` — 主配置
- `~/.openclaw/agents/` — Agent 运行状态、会话、auth profile
- `~/.openclaw/workspace/` — 工作区（`MEMORY.md`、`memory/*.md`、`TOOLS.md`、`SOUL.md`）
- `~/.openclaw/skills/` — 已安装的技能包（如 `openmaic`）

### 可丢弃

- `~/.openclaw/logs/`、`~/.openclaw/canvas/`、`~/.openclaw/browser/`、`~/.openclaw/cache/`、`~/.openclaw/tmp/`

### 系统服务配置

- `~/Library/LaunchAgents/ai.openclaw.*.plist` — 建议迁移一份参考，新机用 `openclaw gateway install` 重装

> 注意：备份中可能包含 token/key，按敏感文件对待。
