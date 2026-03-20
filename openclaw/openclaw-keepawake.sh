#!/bin/zsh
# Prevent macOS from sleeping by periodically asserting user activity.
# Safe + lightweight: every run pokes the system for a short time.

LOG="$HOME/.openclaw/logs/keepawake.log"
mkdir -p "$HOME/.openclaw/logs" 2>/dev/null || true

date '+%Y-%m-%d %H:%M:%S keepawake: poke' >> "$LOG"

# -u: declare user activity
# -t: duration in seconds for the assertion
/usr/bin/caffeinate -u -t 30
