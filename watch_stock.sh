#!/bin/bash
# 启动 A 股盯盘悬浮窗。
# 用 Homebrew python@3.10（含 Tk 8.6）；系统 python 的 Tk 8.5 在 macOS 26 上建窗会崩。
exec /usr/local/bin/python3.10 "$(dirname "$0")/stock_watch.py"
