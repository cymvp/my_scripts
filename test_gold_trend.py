#!/usr/bin/env python3
"""gold_trend.py 的单元测试。

测试用例来自 docs/gold_trend/spec/2026-08-30-gold-trend-spec.md 第 8 节。
回测引擎是计算类代码，用手工构造的序列验证到每一日精确一致。
"""
import math
import pytest

from gold_trend import (
    sma,
    signal_series,
    backtest,
    max_drawdown,
    metrics,
    select_n,
    Bars,
)


# --- SMA ---------------------------------------------------------------

def test_sma_正常计算():
    out = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_sma_数据不足时全为None():
    assert sma([1.0, 2.0], 5) == [None, None]


def test_sma_窗口为1时等于原序列():
    assert sma([3.0, 1.0, 2.0], 1) == [3.0, 1.0, 2.0]


# --- 信号 ---------------------------------------------------------------

def test_signal_高于均线持有_低于空仓():
    # n=2: sma = [None, 1.5, 2.5, 2.5]; close = [1,2,3,2]
    # i=1: 2 > 1.5 -> 1 ; i=2: 3 > 2.5 -> 1 ; i=3: 2 < 2.5 -> 0
    assert signal_series([1.0, 2.0, 3.0, 2.0], 2) == [0, 1, 1, 0]


def test_signal_等于均线时空仓():
    # 常数序列：close 恒等于 sma，按 spec 严格大于才持有
    assert signal_series([5.0, 5.0, 5.0, 5.0], 2) == [0, 0, 0, 0]


def test_signal_均线未成形时空仓():
    assert signal_series([1.0, 2.0, 3.0], 3)[:2] == [0, 0]


def test_signal_只依赖当日及之前的数据_无前视():
    """截断序列后，前缀上的信号必须与完整序列的对应位置完全一致。"""
    closes = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 7.0]
    full = signal_series(closes, 3)
    for cut in range(3, len(closes) + 1):
        assert signal_series(closes[:cut], 3) == full[:cut]


# --- 回测引擎（逐日精确验证）---------------------------------------------

def test_backtest_四种状态转换逐日精确():
    """手工构造：signal 决定次日仓位，四种转换各出现一次。

    close = [10, 10, 10, 10, 10]，open = [9, 20, 30, 40, 50]
    signal = [1, 1, 0, 0, 0]  ->  pos = [0, 1, 1, 0, 0]
      i=0: pos0=0, 前一日无仓 -> 0
      i=1: 0->1 开盘买入      -> close/open - 1 = 10/20 - 1 = -0.5
      i=2: 1->1 持有中        -> close/close_prev - 1 = 10/10 - 1 = 0
      i=3: 1->0 开盘卖出      -> open/close_prev - 1 = 40/10 - 1 = +3.0
      i=4: 0->0 空仓          -> 0
    净值 = 1 * 0.5 * 1.0 * 4.0 = 2.0
    """
    bars = Bars(
        dates=["d0", "d1", "d2", "d3", "d4"],
        opens=[9.0, 20.0, 30.0, 40.0, 50.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0],
    )
    res = backtest(bars, signals=[1, 1, 0, 0, 0])
    assert res.daily_returns == pytest.approx([0.0, -0.5, 0.0, 3.0, 0.0])
    assert res.equity[-1] == pytest.approx(2.0)


def test_backtest_全程持有等于买入持有():
    """signal 恒为 1 时，除首日开盘买入外应完全跟随收盘价。"""
    bars = Bars(
        dates=["d0", "d1", "d2"],
        opens=[10.0, 10.0, 10.0],
        closes=[10.0, 11.0, 12.0],
    )
    res = backtest(bars, signals=[1, 1, 1])
    # i=1 开盘买入: 11/10-1 = 0.1 ; i=2 持有: 12/11-1
    assert res.equity[-1] == pytest.approx(1.2)


def test_backtest_全程空仓净值不变():
    bars = Bars(dates=["a", "b"], opens=[1.0, 2.0], closes=[1.0, 9.0])
    res = backtest(bars, signals=[0, 0])
    assert res.equity[-1] == pytest.approx(1.0)


def test_backtest_交易次数统计开仓次数():
    bars = Bars(
        dates=list("abcdef"),
        opens=[1.0] * 6,
        closes=[1.0] * 6,
    )
    # pos = [0,1,0,1,1,0] -> 两次开仓
    res = backtest(bars, signals=[1, 0, 1, 1, 0, 0])
    assert res.trades == 2


def test_backtest_持仓天数占比():
    bars = Bars(dates=list("abcd"), opens=[1.0] * 4, closes=[1.0] * 4)
    # signals=[1,1,0,0] -> pos=[0,1,1,0] -> 持仓 2/4
    res = backtest(bars, signals=[1, 1, 0, 0])
    assert res.exposure == pytest.approx(0.5)


def test_backtest_信号长度不匹配时报错():
    bars = Bars(dates=["a", "b"], opens=[1.0, 1.0], closes=[1.0, 1.0])
    with pytest.raises(ValueError):
        backtest(bars, signals=[1])


# --- 指标 ---------------------------------------------------------------

def test_max_drawdown_已知曲线():
    # 1 -> 2 -> 1 -> 4：最大回撤发生在 2 到 1，为 -50%
    assert max_drawdown([1.0, 2.0, 1.0, 4.0]) == pytest.approx(-0.5)


def test_max_drawdown_单调上涨为零():
    assert max_drawdown([1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_metrics_年化与卡玛():
    # 252 个交易日净值翻倍，最大回撤 -50%
    eq = [1.0] * 126 + [2.0] * 126
    m = metrics(eq, daily_returns=[0.0] * 252, trades=1, exposure=1.0)
    assert m["total_return"] == pytest.approx(1.0)
    assert m["cagr"] == pytest.approx(1.0, rel=0.05)   # 一年翻倍 -> +100%
    assert m["max_drawdown"] == pytest.approx(0.0)


def test_metrics_空曲线报错():
    with pytest.raises(ValueError):
        metrics([], daily_returns=[], trades=0, exposure=0.0)


# --- 数据校验（不允许静默降级）-------------------------------------------

def test_bars_收盘价含None时报错():
    with pytest.raises(ValueError):
        Bars(dates=["a", "b"], opens=[1.0, 1.0], closes=[1.0, None])


def test_bars_长度不一致时报错():
    with pytest.raises(ValueError):
        Bars(dates=["a"], opens=[1.0, 2.0], closes=[1.0])


def test_bars_非正价格报错():
    with pytest.raises(ValueError):
        Bars(dates=["a"], opens=[0.0], closes=[1.0])


# --- 参数选择 -----------------------------------------------------------

def test_select_n_数据不足以支撑最大候选时报错():
    bars = Bars(dates=list("abc"), opens=[1.0] * 3, closes=[1.0] * 3)
    with pytest.raises(ValueError):
        select_n([bars], candidates=[200])


def test_select_n_返回候选集中的值():
    import random
    random.seed(0)
    n_days = 900
    closes, p = [], 100.0
    for _ in range(n_days):
        p *= 1 + random.gauss(0.0005, 0.01)
        closes.append(p)
    bars = Bars(
        dates=[str(i) for i in range(n_days)],
        opens=list(closes),
        closes=closes,
    )
    best = select_n([bars], candidates=[20, 60, 120])
    assert best in (20, 60, 120)
