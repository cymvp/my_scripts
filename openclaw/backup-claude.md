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

### 3) OpenClaw（迁移用全量归档）
- 命令：`openclaw backup create --verify --output <dir>`
- 输出目录：`${HOME}/projects/migrate_to_new_device/claude-backups/openclaw/`
- 说明：归档包含 OpenClaw state（配置、凭据、会话、workspace/记忆等），适合迁移到新机器后直接恢复。

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

## 迁移提示

把 `${HOME}/projects/migrate_to_new_device/claude-backups/` 整个目录带到新设备：
- Claude 数据：直接覆盖/合并到新机的 `~/.claude/` 和项目 `.claude/`
- OpenClaw：在新机安装 OpenClaw 后，将 `openclaw/*.tar.gz` 解包回 HOME（或按归档内路径恢复），再启动 gateway 服务。

> 注意：归档中可能包含 token/key，按敏感文件对待。
