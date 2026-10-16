#!/usr/bin/env python3
"""黄金趋势择时 — A股黄金ETF 与 金矿股。

用法：
    python3 ~/projects/my_scripts/gold_trend.py            # 看当前信号
    python3 ~/projects/my_scripts/backtest_gold_trend.py   # 跑回测与选参数

做什么：
    唯一规则、唯一参数——收盘价高于 N 日简单移动均线则持有，否则空仓。
    N 从预先固定的候选集 {20,40,60,100,120,150,200} 中，用训练期（前60%样本）的
    卡玛比率选出；后40%样本仅用于样本外验证，选参数时不回看。

本工具不做短期方向预测：
    本仓库 open_gap_forecast.py 的 117 个交易日回测显示，收盘前一切信号对次日方向的
    预测准确率均为随机水平；对黄金另做的 21 次 FOMC 事件研究中，纽约金决议当日、
    +1/+3/+5 日七项检验的 t 值全部在 1.6 以内，无一显著。
    因此这里只做数周到数月尺度的趋势跟踪，目标是降低最大回撤，不是预测涨跌。

回测口径（见 docs/gold_trend/spec/2026-08-30-gold-trend-spec.md）：
    信号用 T 日收盘价算，成交按 T+1 日开盘价，避免前视偏差。
    按用户指示不计交易成本，因此结果偏乐观——请对照输出中的交易次数与换手率判断。
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import os
import urllib.request
from dataclasses import dataclass, field

# 参数候选集：预先固定，事后不扩充（防止看到结果后添加"正好有效"的值）
N_CANDIDATES = (20, 40, 60, 100, 120, 150, 200)

# 按用户指示不计交易成本。保留常量以便结论被正确解读。
COST_BPS = 0.0

TRADING_DAYS_PER_YEAR = 252

ETF_GROUP = [("华安黄金ETF", "518880.SS"), ("黄金ETF基金", "159934.SZ")]
MINER_GROUP = [("山东黄金", "600547.SS"), ("中金黄金", "600489.SS"), ("赤峰黄金", "600988.SS")]

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_gold_trend")


# --- 数据结构 -----------------------------------------------------------

@dataclass
class Bars:
    """一只标的的日线序列。构造时即校验，不允许带病数据进入计算。"""
    dates: list
    opens: list
    closes: list

    def __post_init__(self):
        if not (len(self.dates) == len(self.opens) == len(self.closes)):
            raise ValueError(
                f"长度不一致: dates={len(self.dates)} opens={len(self.opens)} closes={len(self.closes)}")
        for i, (o, c) in enumerate(zip(self.opens, self.closes)):
            if o is None or c is None:
                raise ValueError(f"第 {i} 根K线价格缺失: open={o} close={c}（日期 {self.dates[i]}）")
            if not (math.isfinite(o) and math.isfinite(c)):
                raise ValueError(f"第 {i} 根K线价格非有限值: open={o} close={c}")
            if o <= 0 or c <= 0:
                raise ValueError(f"第 {i} 根K线价格非正: open={o} close={c}（日期 {self.dates[i]}）")

    def __len__(self):
        return len(self.dates)

    def slice(self, start, end):
        return Bars(self.dates[start:end], self.opens[start:end], self.closes[start:end])


@dataclass
class BacktestResult:
    daily_returns: list
    equity: list
    trades: int
    exposure: float


# --- 信号 ---------------------------------------------------------------

def sma(values, n):
    """N 日简单移动均线。前 n-1 位为 None。"""
    if n < 1:
        raise ValueError(f"均线窗口必须为正整数，收到 {n}")
    out, run = [], 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        out.append(run / n if i >= n - 1 else None)
    return out


def signal_series(closes, n):
    """信号：收盘价严格高于 N 日均线为 1（持有），否则为 0（空仓）。

    signal[i] 只依赖 closes[0..i]，不含未来信息。
    """
    ma = sma(closes, n)
    return [1 if (ma[i] is not None and closes[i] > ma[i]) else 0 for i in range(len(closes))]


# --- 回测引擎 -----------------------------------------------------------

def backtest(bars: Bars, signals):
    """按 T+1 开盘执行的回测。

    pos[i]（第 i 日持有的仓位）由 signals[i-1] 决定；第 0 日无前一日信号，故空仓。
    日收益按四种状态转换分别计算，见 spec 第 7 节。
    """
    if len(signals) != len(bars):
        raise ValueError(f"信号长度 {len(signals)} 与K线数 {len(bars)} 不匹配")
    if len(bars) == 0:
        raise ValueError("K线为空")

    pos = [0] + list(signals[:-1])
    rets, equity, nav, trades, held = [], [], 1.0, 0, 0

    for i in range(len(bars)):
        cur, prev = pos[i], (pos[i - 1] if i > 0 else 0)
        if cur == 1 and prev == 1:
            r = bars.closes[i] / bars.closes[i - 1] - 1.0
        elif cur == 1 and prev == 0:
            r = bars.closes[i] / bars.opens[i] - 1.0
            trades += 1
        elif cur == 0 and prev == 1:
            r = bars.opens[i] / bars.closes[i - 1] - 1.0
        else:
            r = 0.0
        if COST_BPS and cur != prev:
            r -= COST_BPS / 10000.0
        nav *= 1.0 + r
        rets.append(r)
        equity.append(nav)
        held += cur

    return BacktestResult(rets, equity, trades, held / len(bars))


def buy_and_hold(bars: Bars):
    """买入持有基准：第 1 日开盘买入，之后一直持有。"""
    return backtest(bars, [1] * len(bars))


# --- 指标 ---------------------------------------------------------------

def max_drawdown(equity):
    """最大回撤，返回负数（无回撤为 0.0）。"""
    if not equity:
        raise ValueError("净值曲线为空")
    peak, worst = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def metrics(equity, daily_returns, trades, exposure):
    if not equity or not daily_returns:
        raise ValueError("净值曲线或日收益序列为空")
    years = len(equity) / TRADING_DAYS_PER_YEAR
    total = equity[-1] - 1.0
    cagr = equity[-1] ** (1.0 / years) - 1.0 if years > 0 and equity[-1] > 0 else float("nan")
    mdd = max_drawdown(equity)
    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd * math.sqrt(TRADING_DAYS_PER_YEAR)) if sd > 0 else float("nan")
    else:
        sharpe = float("nan")
    calmar = (cagr / abs(mdd)) if mdd < 0 else float("inf")
    return {
        "total_return": total, "cagr": cagr, "max_drawdown": mdd,
        "sharpe": sharpe, "calmar": calmar, "trades": trades,
        "exposure": exposure, "years": years,
    }


# --- 参数选择 -----------------------------------------------------------

def select_n(bars_list, candidates=N_CANDIDATES):
    """在给定的（训练期）K线集合上，选卡玛比率均值最高的 N。

    bars_list 有多只标的时（金矿股组），取各标的卡玛的平均，即共用一个 N。
    """
    if not bars_list:
        raise ValueError("标的列表为空")
    need = max(candidates) + 2
    for b in bars_list:
        if len(b) < need:
            raise ValueError(f"数据点 {len(b)} 不足以支撑最大候选窗口 {max(candidates)}（至少需要 {need}）")

    best_n, best_score = None, -float("inf")
    for n in candidates:
        scores = []
        for b in bars_list:
            res = backtest(b, signal_series(b.closes, n))
            m = metrics(res.equity, res.daily_returns, res.trades, res.exposure)
            c = m["calmar"]
            scores.append(c if math.isfinite(c) else 0.0)
        avg = sum(scores) / len(scores)
        if avg > best_score:
            best_n, best_score = n, avg
    return best_n


# --- 数据获取 -----------------------------------------------------------

def fetch_daily(symbol, start="2004-01-01", use_cache=True):
    """Yahoo 日线。用 period1/period2 时间戳区间，range=max 会被静默降级为月线。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = os.path.join(CACHE_DIR, f"{symbol.replace('=', '_')}_{today}.csv")

    if use_cache and os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.reader(f))[1:]
        return Bars([r[0] for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows])

    p1 = int(datetime.datetime.fromisoformat(start).timestamp())
    p2 = int(datetime.datetime.now().timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        payload = json.load(r)

    result = payload.get("chart", {}).get("result")
    if not result:
        raise RuntimeError(f"{symbol}: Yahoo 返回空结果 {payload.get('chart', {}).get('error')}")
    res = result[0]
    off = res["meta"]["gmtoffset"]
    q = res["indicators"]["quote"][0]

    dates, opens, closes = [], [], []
    for i, t in enumerate(res["timestamp"]):
        o, c = q["open"][i], q["close"][i]
        if o is None or c is None:          # 停牌等造成的空值：跳过整根K线并记录，不插值
            continue
        dates.append(datetime.datetime.utcfromtimestamp(t + off).strftime("%Y-%m-%d"))
        opens.append(float(o))
        closes.append(float(c))

    if len(dates) < max(N_CANDIDATES) + 2:
        raise RuntimeError(f"{symbol}: 仅取到 {len(dates)} 根日线，不足以回测")

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "close"])
        w.writerows(zip(dates, opens, closes))
    return Bars(dates, opens, closes)


# --- 日常工具：输出当前信号 ---------------------------------------------

def current_state(bars: Bars, n):
    """当前是持有还是空仓、距均线多少、该状态已持续几个交易日。"""
    sig = signal_series(bars.closes, n)
    ma = sma(bars.closes, n)
    if ma[-1] is None:
        raise ValueError(f"数据不足以计算 {n} 日均线")
    streak = 1
    for i in range(len(sig) - 2, -1, -1):
        if sig[i] == sig[-1]:
            streak += 1
        else:
            break
    return {
        "date": bars.dates[-1], "close": bars.closes[-1], "ma": ma[-1],
        "signal": sig[-1], "gap_pct": (bars.closes[-1] / ma[-1] - 1.0) * 100.0,
        "streak": streak,
    }


def main():
    from backtest_gold_trend import load_group, run_group_report
    print(f"===== 黄金趋势择时  运行时间 {datetime.datetime.now():%Y-%m-%d %H:%M} =====\n")
    for label, group in [("黄金ETF", ETF_GROUP), ("金矿股", MINER_GROUP)]:
        bars_map = load_group(group)
        n, _, _ = run_group_report(label, bars_map, quiet=True)
        print(f"【{label}组】  选定参数 N = {n} 日均线")
        for name, bars in bars_map.items():
            s = current_state(bars, n)
            state = "持有" if s["signal"] else "空仓"
            print(f"  {name:<10} {state}   收盘 {s['close']:.3f}  {n}日均线 {s['ma']:.3f}  "
                  f"距均线 {s['gap_pct']:+.2f}%   该状态已持续 {s['streak']} 个交易日   （{s['date']}）")
        print()
    print("提示：本工具只做数周至数月的趋势跟踪，不预测短期涨跌。")
    print("     样本外实测表现请跑 backtest_gold_trend.py 查看。")


if __name__ == "__main__":
    main()
