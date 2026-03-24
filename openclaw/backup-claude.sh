#!/bin/bash
set -euo pipefail

# Ensure HOME is set (launchd does not guarantee it)
HOME="${HOME:-$(dscl . -read /Users/"$(whoami)" NFSHomeDirectory | awk '{print $2}')}"
export HOME

# Sync Claude Code and OpenClaw data across machines via a shared git repo.
#
# Flow: backup → commit → pull (merge) → restore → push
#
# Three sync targets:
#   1. claude-global/    ↔ ~/.claude/ (global config, memory, transcripts)
#   2. claude-projects/  ↔ each project's .claude/ (CLAUDE.md, settings.json)
#   3. openclaw-mirror/  ↔ ~/.openclaw/ (config, sessions) + LaunchAgents plists
#
# All rsync operations use --update (only overwrite if source is newer),
# so neither machine deletes or overwrites the other's newer data.
#
# Project paths are resolved from ~/.claude/projects/ directory names.
# e.g. "-Users-ycui-projects-umu-csrs" → /Users/ycui/projects/umu/csrs
#
# Usage: ./backup-claude.sh

BACKUP_ROOT="${HOME}/projects/migrate_to_new_device/claude-backups"
GLOBAL_DEST="${BACKUP_ROOT}/claude-global"
PROJECTS_DEST="${BACKUP_ROOT}/claude-projects"
LOG_DIR="${HOME}/projects/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/backup-claude.log"

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

OPENCLAW_MIRROR_DEST="${HOME}/projects/migrate_to_new_device/openclaw-mirror"
REPO_ROOT="${HOME}/projects/migrate_to_new_device"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_PROJECTS="${HOME}/.claude/projects"

# ============================================================
# Phase 1: Backup — local → backup (save local state first)
# ============================================================
log "=== Backup started ==="

# 1a. ~/.claude/ → claude-global/
/usr/bin/rsync -a --update \
    --exclude='statsig/' \
    --exclude='.git/' \
    "${HOME}/.claude/" "${GLOBAL_DEST}/"
log "~/.claude/ -> ${GLOBAL_DEST}/"

# 1b. ~/.openclaw/ → openclaw-mirror/.openclaw/
mkdir -p "${OPENCLAW_MIRROR_DEST}"
/usr/bin/rsync -a --update \
    --exclude='logs/' \
    --exclude='canvas/' \
    --exclude='browser/' \
    --exclude='cache/' \
    --exclude='tmp/' \
    --exclude='.git/' \
    "${HOME}/.openclaw/" "${OPENCLAW_MIRROR_DEST}/.openclaw/"
log "~/.openclaw/ -> ${OPENCLAW_MIRROR_DEST}/.openclaw/ (backup)"

# 1c. LaunchAgents → openclaw-mirror/LaunchAgents/
mkdir -p "${OPENCLAW_MIRROR_DEST}/LaunchAgents"
/usr/bin/rsync -a --update \
    --include='ai.openclaw.*.plist' \
    --exclude='*' \
    "${HOME}/Library/LaunchAgents/" "${OPENCLAW_MIRROR_DEST}/LaunchAgents/"
log "~/Library/LaunchAgents/ai.openclaw.*.plist -> ${OPENCLAW_MIRROR_DEST}/LaunchAgents/"

# 1d. Each project's .claude/ → claude-projects/<safe-name>/
# Skip the home directory itself (e.g. "-Users-ycui" or "-Users-cuiyang")
HOME_ENCODED=$(echo "${HOME}" | sed 's|/|-|g')
if [ -d "${CLAUDE_PROJECTS}" ]; then
    for entry in "${CLAUDE_PROJECTS}"/*/; do
        [ -d "$entry" ] || continue
        dir_name=$(basename "$entry")
        [[ "$dir_name" == .* ]] && continue
        [[ "$dir_name" == "$HOME_ENCODED" ]] && continue

        project_path=$(resolve_path "$dir_name")
        safe_name=$(echo "$dir_name" | sed 's/^-//')

        if [ -d "${project_path}/.claude" ]; then
            dest="${PROJECTS_DEST}/${safe_name}"
            mkdir -p "${dest}"
            /usr/bin/rsync -a --update --exclude='.git/' "${project_path}/.claude/" "${dest}/"
            log "  ${project_path}/.claude/ -> ${dest}/"
        fi
    done
fi

log "=== Backup completed ==="

# ============================================================
# Phase 2: Git commit — save local backup into git
# ============================================================
HOSTNAME_SHORT=$(scutil --get LocalHostName 2>/dev/null || hostname)
TS=$(date '+%Y-%m-%d %H:%M:%S')

if [ -d "${REPO_ROOT}/.git" ]; then
    cd "${REPO_ROOT}"

    /usr/bin/git add -A
    if ! /usr/bin/git diff --cached --quiet; then
        /usr/bin/git commit -m "backup: ${HOSTNAME_SHORT} ${TS}" || true
        log "git commit created"
    else
        log "git: no local changes to commit"
    fi
else
    log "git commit skipped: ${REPO_ROOT} is not a git repo"
fi

# ============================================================
# Phase 3: Git pull — merge with the other machine's backup
# ============================================================
if [ -d "${REPO_ROOT}/.git" ]; then
    cd "${REPO_ROOT}"

    if ! /usr/bin/git pull; then
        # Pull failed — try AI conflict resolution
        CONFLICTED=$(/usr/bin/git diff --name-only --diff-filter=U 2>/dev/null || true)
        if [ -n "$CONFLICTED" ]; then
            log "git pull: conflicts detected — invoking AI resolver"
            if ! "${SCRIPT_DIR}/resolve-conflicts-with-ai.sh" "${LOG_FILE}"; then
                log "AI conflict resolution failed. Please resolve manually."
                exit 2
            fi
            /usr/bin/git add -A
            /usr/bin/git commit -m "backup: ${HOSTNAME_SHORT} ${TS} (merge resolved)" || true
            log "git merge conflicts resolved"
        else
            log "git pull failed. Please check remote auth/connectivity."
            exit 2
        fi
    fi
    log "git pull: remote changes merged"
else
    log "git pull skipped: ${REPO_ROOT} is not a git repo"
fi

# ============================================================
# Phase 4: Restore — backup → local (apply the other machine's changes)
# ============================================================
log "=== Restore started ==="

# 4a. claude-global/ → ~/.claude/
if [ -d "${GLOBAL_DEST}" ]; then
    /usr/bin/rsync -a --update \
        --exclude='statsig/' \
        --exclude='.git/' \
        "${GLOBAL_DEST}/" "${HOME}/.claude/"
    log "${GLOBAL_DEST}/ -> ~/.claude/ (restore)"
fi

# 4b. openclaw-mirror/.openclaw/ → ~/.openclaw/
if [ -d "${OPENCLAW_MIRROR_DEST}/.openclaw" ]; then
    /usr/bin/rsync -a --update \
        --exclude='logs/' \
        --exclude='canvas/' \
        --exclude='browser/' \
        --exclude='cache/' \
        --exclude='tmp/' \
        --exclude='.git/' \
        "${OPENCLAW_MIRROR_DEST}/.openclaw/" "${HOME}/.openclaw/"
    log "${OPENCLAW_MIRROR_DEST}/.openclaw/ -> ~/.openclaw/ (restore)"
fi

# 4c. openclaw-mirror/LaunchAgents/ → ~/Library/LaunchAgents/
if [ -d "${OPENCLAW_MIRROR_DEST}/LaunchAgents" ]; then
    /usr/bin/rsync -a --update \
        --include='ai.openclaw.*.plist' \
        --exclude='*' \
        "${OPENCLAW_MIRROR_DEST}/LaunchAgents/" "${HOME}/Library/LaunchAgents/"
    log "${OPENCLAW_MIRROR_DEST}/LaunchAgents/ -> ~/Library/LaunchAgents/ (restore)"
fi

# 4d. claude-projects/ → each project's .claude/
if [ -d "${PROJECTS_DEST}" ]; then
    for backup_entry in "${PROJECTS_DEST}"/*/; do
        [ -d "$backup_entry" ] || continue
        safe_name=$(basename "$backup_entry")
        dir_name="-${safe_name}"

        project_path=$(resolve_path "$dir_name")
        if [ -d "${project_path}" ]; then
            mkdir -p "${project_path}/.claude"
            /usr/bin/rsync -a --update --exclude='.git/' "${backup_entry}" "${project_path}/.claude/"
            log "  ${backup_entry} -> ${project_path}/.claude/ (restore)"
        fi
    done
fi

log "=== Restore completed ==="

# ============================================================
# Phase 4.5: Symlink — ensure non-canonical machine uses canonical paths
# ============================================================
CANONICAL_USER="cuiyang"
LOCAL_USER="$(whoami)"

if [ "$LOCAL_USER" != "$CANONICAL_USER" ] && [ -d "${CLAUDE_PROJECTS}" ]; then
    LOCAL_PREFIX="-Users-${LOCAL_USER}-"
    CANONICAL_PREFIX="-Users-${CANONICAL_USER}-"

    for local_dir in "${CLAUDE_PROJECTS}"/${LOCAL_PREFIX}*/; do
        [ -d "$local_dir" ] || continue
        [ -L "${local_dir%/}" ] && continue

        local_name=$(basename "$local_dir")
        canonical_dir="${CLAUDE_PROJECTS}/${CANONICAL_PREFIX}${local_name#${LOCAL_PREFIX}}"
        mkdir -p "$canonical_dir"
        /usr/bin/rsync -a --update "${local_dir}" "${canonical_dir}/"
        rm -rf "$local_dir"
        ln -sf "$(basename "$canonical_dir")" "${local_dir%/}"
        log "symlink: $(basename "${local_dir%/}") → $(basename "$canonical_dir")"
    done
fi

# ============================================================
# Phase 5: Git push — upload merged result
# ============================================================
if [ -d "${REPO_ROOT}/.git" ]; then
    cd "${REPO_ROOT}"

    if ! /usr/bin/git push; then
        log "git push failed. Please check remote auth/connectivity."
        exit 3
    fi

    log "git sync completed (backup → commit → pull → restore → push)"
else
    log "git sync skipped: ${REPO_ROOT} is not a git repo"
fi

