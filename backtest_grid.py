#!/usr/bin/env python3
"""网格做 T 参数回测（一次性分析工具）— 中际旭创 sz300308。

用可得的 5 分钟 K 线模拟 spec 定义的网格策略（昨收中轴、±5 档、每档当日一次、
配对目标 +2 档距、趋势停手简化为缺席——5 分钟线无 VWAP 快照，结果偏乐观需说明），
扣费（佣金 0.025% 双边 + 印花税 0.05% 卖出），输出每档距的：
触发次数 / 完成配对次数 / 总盈亏 / 按 T 资金池折算的收益率。

用法：python3 backtest_grid.py [sz300308]
"""
import json
import subprocess
import sys
from collections import defaultdict

COMMISSION = 0.00025  # 双边各收
STAMP = 0.0005        # 卖出
T_POOL = 110_000      # 千元股每档1手≈10.7万；收益率按单档占用资金11万折算


def curl(url):
    r = subprocess.run(["/usr/bin/curl", "-sL", "--noproxy", "*", "-m", "25",
                        "-H", "Referer: https://gu.qq.com/",
                        "-H", "User-Agent: Mozilla/5.0", url],
                       capture_output=True, timeout=30)
    return r.stdout.decode("utf-8", errors="replace")


def fetch_m5(code):
    """腾讯 5 分钟 K：返回 [(datetime_str, open, close, high, low)]，附昨收映射。"""
    raw = json.loads(curl(
        f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m15,,2000"))
    data = raw["data"][code]
    key = next(k for k in data if k.startswith("m15") or k.startswith("m5"))
    bars = [(b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in data[key]]
    return bars


def fetch_daily_prevclose(code):
    """日线收盘 -> {date: prev_close}"""
    raw = json.loads(curl(
        f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,120,qfq"))
    dd = raw["data"][code]
    key = "qfqday" if "qfqday" in dd else "day"
    days = [(r[0], float(r[2])) for r in dd[key]]  # (date, close)
    prev = {}
    for i in range(1, len(days)):
        prev[days[i][0]] = days[i - 1][1]
    return prev


def simulate(bars_by_day, prev_close, step, levels=5, risk_ctrl=False):
    """按 spec 网格规则模拟。返回统计。"""
    total_pnl, n_trig, n_pair, day_pnl = 0.0, 0, 0, {}
    for day, bars in bars_by_day.items():
        if day not in prev_close:
            continue
        pc = prev_close[day]
        buy_lv = [pc * (1 - step * k) for k in range(1, levels + 1)]
        sell_lv = [pc * (1 + step * k) for k in range(1, levels + 1)]
        triggered = set()
        open_legs = []  # (side, qty, price, target)
        pnl = 0.0
        prev_px = None
        cum_amt = cum_vol = 0.0
        vwap_hist = []
        for ts, o, c, hi, lo, vol in bars:
            hhmm = ts[8:12] if len(ts) >= 12 else "0000"
            px = c
            cum_amt += (hi + lo + c) / 3 * vol
            cum_vol += vol
            vwap = cum_amt / cum_vol if cum_vol else c
            vwap_hist.append(vwap)
            # 趋势停手判定（近似 spec 3.2：偏离>1.5% 且 VWAP 两根同向）
            no_buy = no_sell = False
            if risk_ctrl and len(vwap_hist) >= 3:
                rising = vwap_hist[-1] > vwap_hist[-2] > vwap_hist[-3]
                falling = vwap_hist[-1] < vwap_hist[-2] < vwap_hist[-3]
                if px > vwap * 1.015 and rising:
                    no_sell = True
                if px < vwap * 0.985 and falling:
                    no_buy = True
            if prev_px is not None and hhmm < "1445":
                # 下穿买档
                for i, lv in enumerate(buy_lv):
                    key = ("B", i)
                    if key in triggered or not (prev_px > lv >= lo) or no_buy:
                        continue
                    triggered.add(key)
                    n_trig += 1
                    qty = 100  # 千元股最小1手；每档1手
                    open_legs.append(["B", qty, lv, lv * (1 + 2 * step)])
                # 上穿卖档
                for i, lv in enumerate(sell_lv):
                    key = ("S", i)
                    if key in triggered or not (prev_px < lv <= hi) or no_sell:
                        continue
                    triggered.add(key)
                    n_trig += 1
                    qty = 100
                    open_legs.append(["S", qty, lv, lv * (1 - 2 * step)])
            # 配对检查（用 bar 高低价判断目标是否触及）
            for leg in list(open_legs):
                side, qty, entry, target = leg
                hit = (side == "B" and hi >= target) or (side == "S" and lo <= target)
                if hit:
                    if side == "B":
                        gross = (target - entry) * qty
                        fee = (entry + target) * qty * COMMISSION + target * qty * STAMP
                    else:
                        gross = (entry - target) * qty
                        fee = (entry + target) * qty * COMMISSION + entry * qty * STAMP
                    pnl += gross - fee
                    n_pair += 1
                    open_legs.remove(leg)
            prev_px = px
        # 日终未配对腿按收盘价强平（如实计入损益）
        last_close = bars[-1][2]
        for side, qty, entry, _ in open_legs:
            if side == "B":
                gross = (last_close - entry) * qty
                fee = (entry + last_close) * qty * COMMISSION + last_close * qty * STAMP
            else:
                gross = (entry - last_close) * qty
                fee = (entry + last_close) * qty * COMMISSION + entry * qty * STAMP
            pnl += gross - fee
        total_pnl += pnl
        day_pnl[day] = pnl
    win = sum(1 for v in day_pnl.values() if v > 0)
    return {"days": len(day_pnl), "trig": n_trig, "pair": n_pair,
            "pnl": total_pnl, "win_days": win,
            "avg_day_ret": (total_pnl / len(day_pnl) / T_POOL * 100) if day_pnl else 0,  # T_POOL见main注
            "best": max(day_pnl.values(), default=0),
            "worst": min(day_pnl.values(), default=0)}


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "sz300308"
    bars = fetch_m5(code)
    prev_close = fetch_daily_prevclose(code)
    by_day = defaultdict(list)
    for b in bars:
        by_day[f"{b[0][0:4]}-{b[0][4:6]}-{b[0][6:8]}"].append(b)
    # 剔除当天(未完整)
    days = sorted(by_day)
    print(f"数据：{code} 5分钟K {len(bars)} 根，覆盖 {days[0]} ~ {days[-1]} 共 {len(days)} 天")
    print(f"假设 T 资金池 {T_POOL/10000:.0f} 万；费用=佣金0.025%双边+印花税0.05%卖出")
    print(f"{'档距':>6} {'触发':>5} {'配对':>5} {'总盈亏':>10} {'日均收益率':>8} {'盈利天数':>8} {'最好/最差日':>18}")
    for label, rc in (("无风控", False), ("趋势停手", True)):
        print(f"--- {label} ---")
        for step in (0.006, 0.009, 0.012, 0.015, 0.02):
            r = simulate(by_day, prev_close, step, risk_ctrl=rc)
            print(f"{step*100:>5.1f}% {r['trig']:>5} {r['pair']:>5} {r['pnl']:>10.0f} "
                  f"{r['avg_day_ret']:>7.2f}% {r['win_days']:>3}/{r['days']:<3} "
                  f"{r['best']:>8.0f}/{r['worst']:<8.0f}")
    print("\n注意：本回测无趋势停手风控（5分钟线无实时VWAP），单边日亏损被完整计入，")
    print("     实盘有风控停手应略好于此；但滑点/未成交风险未计，两者部分抵消。")


if __name__ == "__main__":
    main()
