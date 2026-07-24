#!/usr/bin/env python3
"""追踪网格 vs 固定网格 回测对比（方案 A 决策依据）。

同段数据（腾讯 15 分钟 K）、同费用，四组对比：
  1 固定昨收 无风控
  2 固定昨收 + 趋势停手（当前上线版）
  3 滚动VWAP 无风控
  4 滚动VWAP + 趋势停手 + 2%熔断（拟上线版）

判据：组4 需在日均收益率 / 最差日回撤上明显优于组2 才实现。
用法：python3 backtest_trailing.py [sz300308]
"""
import json
import subprocess
import sys
from collections import defaultdict

COMMISSION = 0.00025
STAMP = 0.0005
T_POOL = 110_000          # 千元股每档 1 手≈11 万；单档占用资金基准
STEP = 0.006
LEVELS = 5
FUSE_RATIO = 0.02         # 日内最大亏损熔断 = 资金池 × 2%


def curl(url):
    r = subprocess.run(["/usr/bin/curl", "-sL", "--noproxy", "*", "-m", "25",
                        "-H", "Referer: https://gu.qq.com/",
                        "-H", "User-Agent: Mozilla/5.0", url],
                       capture_output=True, timeout=30)
    return r.stdout.decode("utf-8", errors="replace")


def fetch_m15(code):
    raw = json.loads(curl(
        f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m15,,2000"))
    data = raw["data"][code]
    key = next(k for k in data if k.startswith("m15"))
    return [(b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in data[key]]


def fetch_prevclose(code):
    raw = json.loads(curl(
        f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,120,qfq"))
    dd = raw["data"][code]
    key = "qfqday" if "qfqday" in dd else "day"
    days = [(r[0], float(r[2])) for r in dd[key]]
    return {days[i][0]: days[i - 1][1] for i in range(1, len(days))}


def sim_day(bars, prev_close, mode, trend_stop, fuse, recenter_steps=1):
    """单日模拟。mode: 'fixed'|'trail'(VWAP驱动)|'trail_px'(价格驱动)。返回当日盈亏。
    recenter_steps: 价格/VWAP 漂移达到几档才把中枢移到当前价。"""
    anchor = prev_close if mode == "fixed" else None
    triggered = set()
    open_legs = []          # [side, qty, entry, target]
    realized = 0.0
    cum_amt = cum_vol = 0.0
    vwap_hist = []
    prev_px = None
    fused = False

    def levels(center):
        return ([center * (1 - STEP * k) for k in range(1, LEVELS + 1)],
                [center * (1 + STEP * k) for k in range(1, LEVELS + 1)])

    for ts, o, c, hi, lo, vol in bars:
        hhmm = ts[8:12] if len(ts) >= 12 else "0000"
        px = c
        cum_amt += (hi + lo + c) / 3 * vol
        cum_vol += vol
        vwap = cum_amt / cum_vol if cum_vol else c
        vwap_hist.append(vwap)
        if mode == "trail":
            if anchor is None:
                anchor = vwap
                triggered = set()
            elif abs(vwap - anchor) >= STEP * anchor:   # VWAP 漂移满一格 -> 跳格
                anchor = vwap
                triggered = set()
        elif mode == "trail_px":
            if anchor is None:
                anchor = px
                triggered = set()
            elif abs(px - anchor) >= recenter_steps * STEP * anchor:  # 价格漂移达阈值 -> 中枢移到现价
                anchor = px
                triggered = set()
        buy_lv, sell_lv = levels(anchor)

        # 熔断：当日浮动+已实现亏损触阈 → 立即平掉所有未配对腿止损（模拟用户照做）
        if fuse and not fused:
            floating = 0.0
            for side, qty, entry, _ in open_legs:
                floating += (px - entry) * qty if side == "B" else (entry - px) * qty
            if realized + floating <= -FUSE_RATIO * T_POOL:
                fused = True
                for side, qty, entry, _ in list(open_legs):
                    if side == "B":
                        realized += (px - entry) * qty
                        realized -= (entry + px) * qty * COMMISSION + px * qty * STAMP
                    else:
                        realized += (entry - px) * qty
                        realized -= (entry + px) * qty * COMMISSION + entry * qty * STAMP
                open_legs.clear()

        # 趋势停手
        no_buy = no_sell = False
        if trend_stop and len(vwap_hist) >= 3:
            v = vwap_hist[-3:]
            if px > vwap * 1.015 and v[0] < v[1] < v[2]:
                no_sell = True
            if px < vwap * 0.985 and v[0] > v[1] > v[2]:
                no_buy = True

        if prev_px is not None and hhmm < "1445" and not fused:
            for i, lv in enumerate(buy_lv):
                if ("B", i) in triggered or no_buy:
                    continue
                if prev_px > lv >= lo:
                    triggered.add(("B", i))
                    open_legs.append(["B", 100, lv, lv * (1 + 2 * STEP)])
            for i, lv in enumerate(sell_lv):
                if ("S", i) in triggered or no_sell:
                    continue
                if prev_px < lv <= hi:
                    triggered.add(("S", i))
                    open_legs.append(["S", 100, lv, lv * (1 - 2 * STEP)])

        for leg in list(open_legs):
            side, qty, entry, target = leg
            hit = (side == "B" and hi >= target) or (side == "S" and lo <= target)
            if hit:
                if side == "B":
                    realized += (target - entry) * qty
                    realized -= (entry + target) * qty * COMMISSION + target * qty * STAMP
                else:
                    realized += (entry - target) * qty
                    realized -= (entry + target) * qty * COMMISSION + entry * qty * STAMP
                open_legs.remove(leg)
        prev_px = px

    last = bars[-1][2]
    for side, qty, entry, _ in open_legs:      # 日终强平
        if side == "B":
            realized += (last - entry) * qty
            realized -= (entry + last) * qty * COMMISSION + last * qty * STAMP
        else:
            realized += (entry - last) * qty
            realized -= (entry + last) * qty * COMMISSION + entry * qty * STAMP
    return realized


def run(by_day, prev_close, mode, trend_stop, fuse, recenter_steps=1):
    day_pnl = {}
    for day, bars in by_day.items():
        if day in prev_close:
            day_pnl[day] = sim_day(bars, prev_close[day], mode, trend_stop,
                                   fuse, recenter_steps)
    tot = sum(day_pnl.values())
    win = sum(1 for v in day_pnl.values() if v > 0)
    n = len(day_pnl)
    return (tot, tot / n / T_POOL * 100 if n else 0, win, n,
            max(day_pnl.values(), default=0), min(day_pnl.values(), default=0))


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "sz300308"
    bars = fetch_m15(code)
    prev_close = fetch_prevclose(code)
    by_day = defaultdict(list)
    for b in bars:
        by_day[f"{b[0][0:4]}-{b[0][4:6]}-{b[0][6:8]}"].append(b)
    days = sorted(by_day)
    print(f"{code} 15分钟K {len(bars)}根 覆盖 {days[0]}~{days[-1]} 共{len(days)}天；"
          f"档距{STEP*100:.1f}% 每档1手 池{T_POOL/1e4:.0f}万 熔断{FUSE_RATIO*100:.0f}%")
    print(f"{'组':<28}{'总盈亏':>9}{'日均%':>8}{'盈利天':>7}{'最好/最差日':>17}")
    groups = [
        ("2 固定昨收+停手(旧)", "fixed", True, False, 1),
        ("4 VWAP驱动+停手+熔断(现状)", "trail", True, True, 1),
        ("5 价格驱动1档+停手+熔断", "trail_px", True, True, 1),
        ("6 价格驱动2档+停手+熔断", "trail_px", True, True, 2),
        ("7 价格驱动3档+停手+熔断", "trail_px", True, True, 3),
    ]
    for label, mode, ts, fuse, rs in groups:
        tot, avg, win, n, best, worst = run(by_day, prev_close, mode, ts, fuse, rs)
        print(f"{label:<26}{tot:>9.0f}{avg:>7.2f}%{win:>4}/{n:<3}{best:>8.0f}/{worst:<8.0f}")


if __name__ == "__main__":
    main()
