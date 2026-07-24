#!/usr/bin/env python3
"""做 T 日终自检 — 扫描 trade_assist.log，自动发现异常并给改进建议。

目的：让程序自己观测数据、暴露问题，而不是等人去发现。
每个交易日收盘后运行（可挂 cron）。只诊断和建议，不自动改参数（改交易参数需人确认）。

用法：python3 trade_selfcheck.py [YYYY-MM-DD]   # 省略=今天(北京时间)
"""
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = os.path.expanduser("~/projects/logs/trade_assist.log")
STEP = 0.006          # 与 trade_assist 默认档距一致
GRAZE = 0.0015        # "掠过"阈值：距档 0.15% 内算贴脸

LINE = re.compile(
    r"^([\d-]+ [\d:]+)\(BJT\) (\w+) px=([\d.]+) center=([\d.]+) "
    r"买档([\d.]+|-)\(?([-+.\d]*)%?\)? 卖档([\d.]+|-)\(?([-+.\d]*)%?\)? "
    r"pnl=([-+\d]+) flags=(\S+) sig=(\S+)")


def load(day):
    if not os.path.exists(LOG):
        return {}
    per = defaultdict(list)
    events = defaultdict(list)
    for ln in open(LOG, encoding="utf-8"):
        if f"{day} " not in ln:
            continue
        m = LINE.match(ln)
        if m:
            ts, code, px, center = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
            per[code].append({
                "ts": ts, "px": px, "center": center,
                "buy": None if m.group(5) == "-" else float(m.group(5)),
                "sell": None if m.group(7) == "-" else float(m.group(7)),
                "pnl": int(m.group(9)), "flags": m.group(10), "sig": m.group(11)})
        else:
            mm = re.search(r"\(BJT\) (\w+) (★SIGNAL|✅成交|信号超时|成交回报被拒|⚠异常|🔴|熔断)", ln)
            if mm:
                events[mm.group(1)].append(ln.strip())
    return per, events


def diagnose(day):
    per, events = load(day)
    if not per:
        return f"做T自检 {day}：当天无做 T 日志（未启用或非交易日）。"
    out = [f"===== 做 T 日终自检 {day} ====="]
    for code, ticks in per.items():
        n = len(ticks)
        pxs = [t["px"] for t in ticks]
        rng = (max(pxs) - min(pxs)) / min(pxs) * 100 if pxs else 0
        centers = sorted({round(t["center"], 2) for t in ticks})
        n_signal = len(events.get(code, []))
        # 掠过买/卖档但没触发的次数
        graze_buy = sum(1 for t in ticks if t["buy"] and 0 <= (t["px"] - t["buy"]) / t["buy"] <= GRAZE)
        graze_sell = sum(1 for t in ticks if t["sell"] and 0 <= (t["sell"] - t["px"]) / t["px"] <= GRAZE)
        # 引擎停顿：相邻 tick 间隔 > 30s。区分"成交弹窗阻塞(正常)"与"疑似崩溃"
        fmt = "%Y-%m-%d %H:%M:%S"
        ev_secs = []
        for e in events.get(code, []):
            em = re.search(r"(\d\d:\d\d:\d\d)\(BJT\)", e)
            if em:
                ev_secs.append(datetime.strptime(day + " " + em.group(1), fmt))
        crash_gaps = 0
        for a, b in zip(ticks, ticks[1:]):
            ta_, tb = datetime.strptime(a["ts"], fmt), datetime.strptime(b["ts"], fmt)
            if (tb - ta_).total_seconds() <= 30:
                continue
            # 停顿两端 90s 内有信号/成交事件 → 弹窗阻塞，正常
            near_event = any(abs((ta_ - ev).total_seconds()) < 90 or
                             abs((tb - ev).total_seconds()) < 90 for ev in ev_secs)
            if not near_event:
                crash_gaps += 1
        gaps = crash_gaps
        # 风控占比
        stopped = sum(1 for t in ticks if t["flags"] != "-")
        last_pnl = ticks[-1]["pnl"]

        out.append(f"\n【{code}】{n} tick，日内振幅 {rng:.2f}%，中枢移动 {len(centers)} 次，"
                   f"信号 {n_signal} 次，收盘做T盈亏 {last_pnl:+d}")
        issues = []
        if len(centers) <= 1 and rng > 1.5:
            issues.append(f"⚠中枢一整天没移动（{centers}）但振幅达 {rng:.2f}% "
                          f"→ 追踪失效，检查 recenter 阈值")
        if graze_buy + graze_sell >= 20 and n_signal == 0:
            issues.append(f"⚠贴脸档位 {graze_buy+graze_sell} 次(买{graze_buy}/卖{graze_sell})却 0 触发 "
                          f"→ 档距({STEP*100:.1f}%)对当日振幅偏宽，或触发条件太严(建议触及式)")
        if gaps:
            issues.append(f"⚠引擎停顿 {gaps} 段(非成交弹窗期，间隔>30s) → 疑似崩溃/卡死，查 stock_watch.out")
        # 出信号但无成交回报
        sigs = sum(1 for e in events.get(code, []) if "★SIGNAL" in e)
        fills = sum(1 for e in events.get(code, []) if "✅成交" in e)
        if sigs and fills == 0:
            issues.append(f"⚠出了 {sigs} 个信号但 0 笔成交回报 → 要么没来得及下单/回报，要么信号不合理")
        if stopped / n > 0.5:
            issues.append(f"⚠{stopped/n*100:.0f}% 时间处于风控停手 → 当日多为单边趋势，做T本就该少动")
        if issues:
            out.extend("  " + i for i in issues)
        else:
            out.append("  ✓ 未发现异常")
    # 关键事件回放
    allev = [e for evs in events.values() for e in evs]
    if allev:
        out.append("\n关键事件：")
        out.extend("  " + e[-120:] for e in allev[-10:])
    out.append("\n（自检只提示不改参数；要调档距/阈值/触发方式，确认后由人执行。）")
    return "\n".join(out)


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    print(diagnose(day))
