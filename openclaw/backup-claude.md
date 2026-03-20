# backup-claude.sh — Claude + OpenClaw 迁移备份

这个脚本用于**定期备份 Claude Code 本地数据**，并额外生成一份 **OpenClaw 全量备份归档**，用于迁移到新设备后“无缝继续工作”。

## 备份内容

### 1) Claude Code（全局）
- 源：`~/.claude/`
- 目的：`${HOME}/projects/migrate_to_new_device/claude-backups/claude-global/`
- 说明：包含 Claude 的全局配置、记忆、transcripts 等。

### 2) Claude Code（各项目）
- 源：每个项目目录下的 `.claude/`
- 目的：`${HOME}/projects/migrate_to_new_device/claude-backups/claude-projects/<safe-name>/`
- 项目列表来源：`~/.claude/projects/`（目录名为路径的 dash 编码形式）

### 3) OpenClaw（迁移用增量镜像，单目录）
- 方式：`rsync` 增量镜像（每次只同步变化，维护一个目录，不生成一堆新 tar.gz）
- 输出目录：`${HOME}/projects/migrate_to_new_device/openclaw-mirror/`
- 内容：
  - `~/.openclaw/`（配置、凭据、会话、workspace/记忆等）
  - `~/Library/LaunchAgents/ai.openclaw.*.plist`（便于新机快速恢复服务）
- 排除：`logs/`、`canvas/`、`browser/`、`cache/`、`tmp/` 等非迁移必需的大目录

## 运行方式

手动运行：

```bash
${HOME}/projects/my_scripts/openclaw/backup-claude.sh
```

日志：
- 脚本日志：`${HOME}/projects/migrate_to_new_device/claude-backups/backup.log`
- LaunchAgent stdout/stderr：
  - `${HOME}/projects/migrate_to_new_device/claude-backups/launchd.out.log`
  - `${HOME}/projects/migrate_to_new_device/claude-backups/launchd.err.log`

## 定时任务（LaunchAgent）

- plist：`~/Library/LaunchAgents/ai.openclaw.backup-claude.plist`
- label：`ai.openclaw.backup-claude`
- 当前计划：每天 02:00 运行一次（StartCalendarInterval）

常用命令：

```bash
# 查看
launchctl print "gui/$(id -u)/ai.openclaw.backup-claude" | sed -n '1,120p'

# 重新加载
launchctl bootout "gui/$(id -u)/ai.openclaw.backup-claude" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/ai.openclaw.backup-claude.plist"
```

## OpenClaw 目录说明（迁移时哪些要留、哪些可丢）

OpenClaw 的主要本地数据都在 `~/.openclaw/`。为了迁移“像现在一样接着工作”，一般建议按下面思路：

### 必须迁移（建议保留）

这些直接影响你的工作连续性：

- `~/.openclaw/openclaw.json`
  - 主配置（gateway、channels、默认模型、workspace 路径等）
- `~/.openclaw/agents/`
  - Agent 运行状态、会话索引与转录等
  - 典型：`~/.openclaw/agents/main/sessions/`（会话/上下文）
  - 以及 agent 的 auth profile（API key）等
- `~/.openclaw/workspace/`
  - 你的“可编辑工作区”
  - 里面的 `MEMORY.md`、`memory/*.md`、`TOOLS.md`、`SOUL.md` 等属于你持续工作的核心资产

> 备注：很多情况下 workspace 也会出现在 `~/.openclaw/` 的管理范围内（取决于配置），迁移目标是保证这套文件在新机可用。

### 可选迁移（看你需求）

- `~/.openclaw/plugins/`、`~/.openclaw/skills/`（如果存在）
  - 可能包含插件/技能的本地状态或缓存；一般保留没坏处

### 可丢弃（缓存/临时/日志，必要时可重建）

这些不影响“能继续用”，但可能体积大：

- `~/.openclaw/logs/`
  - 日志文件，迁移通常不需要
- `~/.openclaw/canvas/`
  - 控制台/画布相关的静态内容或缓存
- `~/.openclaw/browser/`
  - OpenClaw 的浏览器控制服务/配置/缓存（如无需保留登录态，可不迁）
- `~/.openclaw/cache/`、`~/.openclaw/tmp/`
  - 缓存与临时文件

### 系统服务配置（建议迁移一份，方便快速恢复）

- `~/Library/LaunchAgents/ai.openclaw.*.plist`
  - gateway / keepawake / 其他 OpenClaw 相关 LaunchAgent
  - 新机上仍建议用 `openclaw gateway install` 重新安装服务（plist 主要用于参考/快速恢复）

---

## 迁移提示

把 `${HOME}/projects/migrate_to_new_device/claude-backups/` 整个目录带到新设备：
- Claude 数据：直接覆盖/合并到新机的 `~/.claude/` 和项目 `.claude/`
- OpenClaw：
  1) 在新机安装 OpenClaw
  2) 将 `openclaw-mirror/.openclaw/` 放回新机的 `~/.openclaw/`
  3) 将 `openclaw-mirror/LaunchAgents/` 下的 `ai.openclaw.*.plist` 拷到新机 `~/Library/LaunchAgents/`
  4) 用 `openclaw gateway install` 或 `launchctl bootstrap ...` 重新加载服务

> 注意：备份中可能包含 token/key，按敏感文件对待。
