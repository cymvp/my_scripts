#!/bin/bash
set -euo pipefail

# Backup all Claude Code data to a centralized location for cloud sync.
#
# Two backup targets:
#   1. claude-global/    ← ~/.claude/ (global config, memory, transcripts)
#   2. claude-projects/  ← each project's .claude/ (CLAUDE.md, settings.json)
#
# Project paths are resolved from ~/.claude/projects/ directory names.
# e.g. "-Users-cuiyang-projects-umu-csrs" → /Users/cuiyang/projects/umu/csrs
#
# Usage: ./backup-claude.sh
# Recommended: crontab -e → 0 2 * * * /Users/cuiyang/scripts/backup-claude.sh

BACKUP_ROOT="${HOME}/projects/migrate_to_new_device/claude-backups"
GLOBAL_DEST="${BACKUP_ROOT}/claude-global"
PROJECTS_DEST="${BACKUP_ROOT}/claude-projects"
LOG_FILE="${BACKUP_ROOT}/backup.log"

OPENCLAW_BIN="${HOME}/.npm-global/bin/openclaw"

mkdir -p "${GLOBAL_DEST}" "${PROJECTS_DEST}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"
}

# Resolve a dash-encoded directory name back to a real filesystem path.
# Greedy left-to-right: at each level, try the longest matching segment first.
resolve_path() {
    local encoded="$1"
    local IFS='-'
    read -ra parts <<< "${encoded#-}"

    local current=""
    local i=0
    local n=${#parts[@]}

    while [ $i -lt $n ]; do
        local matched=false
        local j=$n
        while [ $j -gt $i ]; do
            local candidate=""
            for (( k=i; k<j; k++ )); do
                [ -n "$candidate" ] && candidate="${candidate}-"
                candidate="${candidate}${parts[$k]}"
            done
            if [ -d "${current}/${candidate}" ]; then
                current="${current}/${candidate}"
                i=$j
                matched=true
                break
            fi
            (( j-- ))
        done
        if ! $matched; then
            current="${current}/${parts[$i]}"
            (( i++ ))
        fi
    done

    echo "$current"
}

log "=== Backup started ==="

# 1. ~/.claude/ → claude-global/
/usr/bin/rsync -a --delete \
    --exclude='statsig/' \
    "${HOME}/.claude/" "${GLOBAL_DEST}/"
log "~/.claude/ -> ${GLOBAL_DEST}/"

# 1b. OpenClaw full local backup (config, creds, sessions, workspace)
# This is the easiest way to migrate to a new machine and keep working seamlessly.
OPENCLAW_BACKUP_DIR="${BACKUP_ROOT}/openclaw"
mkdir -p "${OPENCLAW_BACKUP_DIR}"
if [ -x "${OPENCLAW_BIN}" ]; then
    "${OPENCLAW_BIN}" backup create --verify --output "${OPENCLAW_BACKUP_DIR}" \
        && log "openclaw backup -> ${OPENCLAW_BACKUP_DIR}/" \
        || log "WARN: openclaw backup failed (see output above)"
else
    log "WARN: openclaw binary not found at ${OPENCLAW_BIN}; skipping openclaw backup"
fi

# 2. Each project's .claude/ → claude-projects/<safe-name>/
CLAUDE_PROJECTS="${HOME}/.claude/projects"
if [ -d "${CLAUDE_PROJECTS}" ]; then
    for entry in "${CLAUDE_PROJECTS}"/*/; do
        [ -d "$entry" ] || continue
        dir_name=$(basename "$entry")
        [[ "$dir_name" == .* ]] && continue
        [[ "$dir_name" == "-Users-cuiyang" ]] && continue

        project_path=$(resolve_path "$dir_name")
        safe_name=$(echo "$dir_name" | sed 's/^-//')

        if [ -d "${project_path}/.claude" ]; then
            dest="${PROJECTS_DEST}/${safe_name}"
            mkdir -p "${dest}"
            /usr/bin/rsync -a --delete "${project_path}/.claude/" "${dest}/"
            log "  ${project_path}/.claude/ -> ${dest}/"
        fi
    done
fi

log "=== Backup completed ==="
