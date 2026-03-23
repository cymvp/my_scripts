#!/bin/bash
# resolve-conflicts-with-ai.sh
# Called after git stash pop (or merge) produces conflicts.
# Uses Claude Code CLI (non-interactive) to intelligently resolve each conflict.
#
# Context: This repo stores backup data from multiple machines (config files,
# session data, memory). Conflicts typically arise when two machines modify
# the same file between syncs.
#
# Strategy:
#   - For binary files or files without conflict markers: accept local (ours) version
#   - For text files with conflict markers: ask Claude to resolve intelligently
#   - Falls back to accepting local version if AI resolution fails
#
# Usage: ./resolve-conflicts-with-ai.sh [log_file]

set -uo pipefail

LOG_FILE="${1:-/dev/stderr}"

CLAUDE_BIN="/Users/cuiyang/.local/bin/claude"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [resolve] $1" | tee -a "${LOG_FILE}"
}

resolve_one_file() {
    local filepath="$1"

    # Check if file has conflict markers (text file with standard git markers)
    if grep -q '^<<<<<<<' "$filepath" 2>/dev/null; then
        log "  AI resolving: ${filepath}"

        if [ -n "$CLAUDE_BIN" ]; then
            local abs_path resolved_file
            abs_path="$(cd "$(dirname "$filepath")" && pwd)/$(basename "$filepath")"
            resolved_file=".ai-resolve-result.tmp"

            # Let Claude read the conflicted file and write the resolved version
            # inside the repo (sandbox allows writing here).
            "$CLAUDE_BIN" -p "Read the file ${abs_path}. It has git merge conflict markers. This is a backup sync repo for Claude Code and OpenClaw config/session data between two machines. Resolve the conflicts: for config files merge both sides preferring newer/more complete version; for session/memory data prefer the HEAD/ours side; for log-like data combine both in chronological order. Write ONLY the resolved file content to $(pwd)/${resolved_file} using the Write tool. No conflict markers, no explanations." --allowedTools "Read,Write" 2>/dev/null

            if [ -s "$resolved_file" ]; then
                if ! grep -q '^<<<<<<<' "$resolved_file" 2>/dev/null; then
                    cp "$resolved_file" "$filepath"
                    rm -f "$resolved_file"
                    log "  AI resolved successfully: ${filepath}"
                    return 0
                else
                    log "  AI output still contains conflict markers, falling back: ${filepath}"
                fi
            else
                log "  AI resolution failed, falling back: ${filepath}"
            fi
            rm -f "$resolved_file"
        else
            log "  claude CLI not found, falling back: ${filepath}"
        fi

        # Fallback: accept ours (local version)
        /usr/bin/git checkout --ours "$filepath" 2>/dev/null || true
        log "  Fallback (ours): ${filepath}"
    else
        # Binary file or no conflict markers — accept ours
        /usr/bin/git checkout --ours "$filepath" 2>/dev/null || true
        log "  Accept ours (no markers): ${filepath}"
    fi
}

# --- Main ---
log "Starting AI-assisted conflict resolution"

# Get list of conflicted files (UU = both modified)
conflicted=$(/usr/bin/git diff --name-only --diff-filter=U 2>/dev/null || true)

if [ -z "$conflicted" ]; then
    log "No conflicts to resolve"
    exit 0
fi

count=$(echo "$conflicted" | wc -l | tr -d ' ')
log "Resolving ${count} conflicted file(s)"

while IFS= read -r filepath; do
    [ -z "$filepath" ] && continue
    resolve_one_file "$filepath"
    /usr/bin/git add "$filepath"
done <<< "$conflicted"

log "Conflict resolution completed"
exit 0
