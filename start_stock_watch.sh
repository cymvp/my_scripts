#!/bin/bash
# 启动 A 股盯盘悬浮窗。
#
# 必须用 Tk 8.6 的解释器：Tk 9.0 会让 -transparent 失效（背景不透明）、
# 并把 Menlo 12 的行高从 14px 放大到 19px。2026-09-04 误用 csrs venv 的
# Python 3.11（Tk 9.0）踩过一次，两个症状同时出现。
PY=/usr/local/bin/python3.10

TK=$("$PY" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null)
if [ "$TK" != "8.6" ]; then
    echo "拒绝启动：$PY 的 Tk 版本是 ${TK:-取不到}，需要 8.6" >&2
    exit 1
fi

pkill -f "[s]tock_watch.py" && echo "已停掉运行中的实例"
cd "$(dirname "$0")"
nohup "$PY" stock_watch.py > /tmp/stock_watch.log 2>&1 &
sleep 3
if pgrep -f "[s]tock_watch.py" > /dev/null; then
    echo "已启动（Tk ${TK}），PID $(pgrep -f '[s]tock_watch.py')"
else
    echo "启动失败，日志：" >&2; cat /tmp/stock_watch.log >&2; exit 1
fi
