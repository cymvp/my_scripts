#!/usr/bin/env python3
"""黄金趋势择时回测 — 训练期选参数，样本外验证。

用法：
    python3 ~/projects/my_scripts/backtest_gold_trend.py

协议（见 docs/gold_trend/spec/2026-08-30-gold-trend-spec.md 第 3 节）：
    前 60% 样本为训练期，只在这一段上从 {20,40,60,100,120,150,200} 里选 N；
    后 40% 为样本外期，只用于验证，选参数时绝不回看。
    选择标准是训练期卡玛比率（年化收益 / 最大回撤），因为目标是降低回撤。

成功标准：
    样本外的最大回撤、夏普比率、卡玛比率三项与买入持有对比。
    三项都赢不了买入持有，结论就是该模型无效，如实输出。

注意：按用户指示不计交易成本，结果偏乐观。请对照输出中的交易次数与持仓占比判断。
"""
import datetime
import math

from gold_trend import (
    ETF_GROUP, MINER_GROUP, N_CANDIDATES, TRADING_DAYS_PER_YEAR,
    backtest, buy_and_hold, fetch_daily, metrics, select_n, signal_series,
)

TRAIN_FRAC = 0.60


def load_group(group):
    """取一组标的的日线。任何一只失败就报错退出，不静默跳过。"""
    out = {}
    for name, sym in group:
        out[name] = fetch_daily(sym)
    return out


def split(bars):
    cut = int(len(bars) * TRAIN_FRAC)
    return bars.slice(0, cut), bars.slice(cut, len(bars))


def evaluate(bars, n):
    """返回 (策略指标, 买入持有指标)。"""
    res = backtest(bars, signal_series(bars.closes, n))
    bh = buy_and_hold(bars)
    return (metrics(res.equity, res.daily_returns, res.trades, res.exposure),
            metrics(bh.equity, bh.daily_returns, bh.trades, bh.exposure))


def fmt(m):
    return (f"总收益 {m['total_return']*100:>8.1f}%  年化 {m['cagr']*100:>6.2f}%  "
            f"最大回撤 {m['max_drawdown']*100:>7.2f}%  夏普 {m['sharpe']:>5.2f}  "
            f"卡玛 {m['calmar']:>5.2f}")


def wins(strat, bench):
    """样本外三项标准各自是否胜出。"""
    return {
        "回撤": strat["max_drawdown"] > bench["max_drawdown"],   # 回撤是负数，越大越好
        "夏普": strat["sharpe"] > bench["sharpe"],
        "卡玛": strat["calmar"] > bench["calmar"],
    }


def run_group_report(label, bars_map, quiet=False):
    """在训练期选 N，分别报告训练期与样本外表现。返回 (n, 样本外策略指标, 样本外基准指标)。"""
    splits = {name: split(b) for name, b in bars_map.items()}
    n = select_n([tr for tr, _ in splits.values()], N_CANDIDATES)

    oos_strat, oos_bench = {}, {}
    if not quiet:
        print(f"\n{'='*104}")
        print(f"【{label}组】  训练期选出 N = {n} 日均线   （候选集 {list(N_CANDIDATES)}，前 "
              f"{int(TRAIN_FRAC*100)}% 训练 / 后 {100-int(TRAIN_FRAC*100)}% 样本外）")
        print("=" * 104)

    for name, (tr, te) in splits.items():
        s_tr, b_tr = evaluate(tr, n)
        s_te, b_te = evaluate(te, n)
        oos_strat[name], oos_bench[name] = s_te, b_te
        if quiet:
            continue
        print(f"\n  {name}   训练 {tr.dates[0]}~{tr.dates[-1]}（{s_tr['years']:.1f}年）   "
              f"样本外 {te.dates[0]}~{te.dates[-1]}（{s_te['years']:.1f}年）")
        print(f"    训练·策略    {fmt(s_tr)}   交易 {s_tr['trades']:>3} 次  持仓占比 {s_tr['exposure']*100:.0f}%")
        print(f"    训练·买入持有 {fmt(b_tr)}")
        print(f"    样本外·策略  {fmt(s_te)}   交易 {s_te['trades']:>3} 次  持仓占比 {s_te['exposure']*100:.0f}%")
        print(f"    样本外·买入持有 {fmt(b_te)}")
        w = wins(s_te, b_te)
        marks = "  ".join(f"{k}{'✓' if v else '✗'}" for k, v in w.items())
        print(f"    样本外胜负：{marks}   （{sum(w.values())}/3 项优于买入持有）")
        if s_tr["calmar"] > 0 and s_te["calmar"] < s_tr["calmar"] * 0.5:
            print(f"    ⚠ 疑似过拟合：样本外卡玛 {s_te['calmar']:.2f} 不到训练期 {s_tr['calmar']:.2f} 的一半")

    return n, oos_strat, oos_bench


def verdict(label, oos_strat, oos_bench):
    """对一组给出结论。三项标准在多数标的上都赢不了买入持有，就判定无效。"""
    per_target = {name: wins(oos_strat[name], oos_bench[name]) for name in oos_strat}
    total = sum(sum(w.values()) for w in per_target.values())
    possible = 3 * len(per_target)
    print(f"\n  【{label}组结论】样本外合计胜出 {total}/{possible} 项")
    dd_win = sum(1 for w in per_target.values() if w["回撤"])
    if total >= possible * 0.6 and dd_win >= len(per_target) * 0.6:
        print(f"    该组趋势信号在样本外站得住：{dd_win}/{len(per_target)} 只标的的最大回撤优于买入持有。")
    elif dd_win >= len(per_target) * 0.6:
        print(f"    只有降回撤这一项站得住（{dd_win}/{len(per_target)} 只），收益端跑输。"
              f"若你的目标就是降回撤，可用；若要收益，不如买入持有。")
    else:
        print(f"    **该组模型无效**：样本外三项标准多数跑输买入持有，建议直接定投，不要用这个信号。")


def main():
    print(f"===== 黄金趋势择时回测  {datetime.datetime.now():%Y-%m-%d %H:%M} =====")
    print(f"信号：收盘价 > N 日均线则持有，否则空仓。信号用 T 日收盘算，成交按 T+1 日开盘。")
    print(f"交易成本：按指示不计入，结果偏乐观——请看每行的交易次数判断成本敞口。")
    for label, group in [("黄金ETF", ETF_GROUP), ("金矿股", MINER_GROUP)]:
        bars_map = load_group(group)
        n, s, b = run_group_report(label, bars_map)
        verdict(label, s, b)
    print()


if __name__ == "__main__":
    main()
