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

**Phase 1: Backup（本地 → 备份目录）**

1. **Symlink 转换**（仅机器 B）：将 `-Users-ycui-*` 真实目录的内容合并到 `-Users-cuiyang-*`，原目录替换为 symlink
2. **非 canonical 清理**（两台机器）：删除本地和备份中非 `cuiyang` 编码的真实目录，防止被提交到 git 污染远端
3. **rsync `--update --no-links`**：`~/.claude/` → `claude-global/`，跳过 symlink
4. **rsync `--update`**：`~/.openclaw/` → `openclaw-mirror/`，LaunchAgents plist
5. **各项目 `.claude/`** → `claude-projects/`：safe_name 归一化为 canonical 前缀

**Phase 2: Commit**

`git add -A` + `git commit`，将本机最新状态存入 git。

**Phase 3: Pull + Merge（合并的核心）**

`git pull` 拉取对方机器的提交。git merge 负责合并：
- 不同文件各自添加：自动合并，无冲突
- 同一文件两边都改了：产生冲突 → 调用 `resolve-conflicts-with-ai.sh` 自动解决

**合并后，备份目录包含两台机器数据的并集。这是整个同步不丢数据的关键。**

**Phase 4: Restore（备份目录 → 本地）**

1. **非 canonical 清理**：删除备份中 git pull 可能带回的非 canonical 目录
2. **rsync `--update --no-links`**：`claude-global/` → `~/.claude/`
3. **rsync `--update`**：`openclaw-mirror/` → `~/.openclaw/`，LaunchAgents plist
4. **`claude-projects/`** → 各项目 `.claude/`：canonical 前缀转回本机前缀以 resolve 实际项目路径

**Phase 5: Push**

`git push` 推送合并结果，供对方机器下次拉取。

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

## 机器 B（ycui）的 symlink 自动维护

脚本在 Phase 1（Backup）开始时自动处理：
1. 检测当前用户名，如果不是标准用户（`cuiyang`），将本机编码的记忆目录合并到标准编码目录，然后替换为 symlink
2. 清理两台机器上残留的非 canonical 真实目录（`claude-global/projects/` 和 `claude-projects/` 中非 `cuiyang` 编码的目录）
3. rsync 使用 `--no-links`，symlink 不进入备份目录和 git 仓库

无需手动初始化或维护。新项目在下次同步时会自动创建 symlink。

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
