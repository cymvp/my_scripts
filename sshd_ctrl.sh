#!/usr/bin/env bash
  set -euo pipefail

  DIR="$HOME/my-sshd"
  CONFIG="$DIR/sshd_config"
  HOST_KEY="$DIR/host_ed25519_key"
  AUTH_KEYS="$DIR/keys"
  PIDFILE="$DIR/sshd.pid"
  LOGFILE="$DIR/sshd.log"
  PORT=2222
  SSHD_BIN="/usr/sbin/sshd"

  cmd_init() {
      mkdir -p "$DIR" && chmod 700 "$DIR"
      [[ -f "$CONFIG" ]] || touch "$CONFIG"

      if [[ ! -f "$HOST_KEY" ]]; then
          echo "[init] generating host key: $HOST_KEY"
          ssh-keygen -t ed25519 -f "$HOST_KEY" -N '' -q
      fi
      chmod 600 "$HOST_KEY"

      if [[ ! -f "$AUTH_KEYS" ]]; then
          echo "[init] WARN: $AUTH_KEYS not found. Create it and add client public keys (one per line)."
          touch "$AUTH_KEYS"
      fi
      chmod 600 "$AUTH_KEYS"
  }

  is_running() {
      [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
  }

  cmd_start() {
      cmd_init
      if is_running; then
          echo "sshd already running (pid $(cat "$PIDFILE"), port $PORT)"
          return 0
      fi

      "$SSHD_BIN" \
          -f "$CONFIG" \
          -p "$PORT" \
          -h "$HOST_KEY" \
          -E "$LOGFILE" \
          -o "AuthorizedKeysFile=$AUTH_KEYS" \
          -o "PidFile=$PIDFILE" \
          -o "UsePAM=no" \
          -o "StrictModes=yes" \
          -o "PasswordAuthentication=no" \
          -o "PubkeyAuthentication=yes"

      sleep 0.5
      if is_running; then
          echo "sshd started: pid=$(cat "$PIDFILE") port=$PORT log=$LOGFILE"
      else
          echo "sshd failed to start. Check log: $LOGFILE"
          return 1
      fi
  }

  cmd_stop() {
      if ! is_running; then
          echo "sshd is not running"
          rm -f "$PIDFILE"
          return 0
      fi
      local pid
      pid=$(cat "$PIDFILE")
      kill "$pid"
      for _ in {1..15}; do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.2
      done
      if kill -0 "$pid" 2>/dev/null; then
          echo "force killing pid $pid"
          kill -9 "$pid" || true
      fi
      rm -f "$PIDFILE"
      echo "sshd stopped"
  }

  cmd_status() {
      if is_running; then
          echo "running (pid $(cat "$PIDFILE"), port $PORT)"
      else
          echo "stopped"
      fi
  }

  cmd_restart() {
      cmd_stop || true
      cmd_start
  }

  cmd_logs() {
      [[ -f "$LOGFILE" ]] || { echo "no log file yet: $LOGFILE"; return 1; }
      tail -f "$LOGFILE"
  }

  case "${1:-}" in
      start)   cmd_start   ;;
      stop)    cmd_stop    ;;
      status)  cmd_status  ;;
      restart) cmd_restart ;;
      init)    cmd_init    ;;
      logs)    cmd_logs    ;;
      *)
          echo "Usage: $0 {start|stop|status|restart|init|logs}"
          exit 1
          ;;
  esac