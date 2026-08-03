"""intraday_collector 纯函数的单元测试（不联网）。"""
import pytest

import intraday_collector as ic


# --- 板块宽度（原 breadth_recorder，2026-08-03 并入本脚本）-------------------

def _day(code, o, c):
    """构造一条只关心开收的记录：8 根 K，首根开 = o，末根收 = c。"""
    bars = [{"t": "10:00", "o": o, "h": max(o, c), "l": min(o, c), "c": o, "v": 1.0}]
    bars += [{"t": t, "o": o, "h": max(o, c), "l": min(o, c), "c": c, "v": 1.0}
             for t in ("10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00")]
    return {"code": code, "date": None, "bars": bars}


def test_breadth_one_day():
    """七个字段的口径（与原 breadth_recorder 一致）：

      跳空   = (今开 − 昨收) ÷ 昨收，昨收取【前一交易日末根 K 的收盘】
      涨跌幅 = (今收 − 昨收) ÷ 昨收
      开→收  = (今收 − 今开) ÷ 今开
      收高率 = 今收 > 今开 的比例

    构造三只票：
      A 昨收 100 今开 110(+10%) 今收 105  → 涨跌 +5%   开→收 −4.55%  收低
      B 昨收 100 今开 104(+4%)  今收 108  → 涨跌 +8%   开→收 +3.85%  收高
      C 昨收 100 今开  97(−3%)  今收  95  → 涨跌 −5%   开→收 −2.06%  收低
    跳空 ≥3% 的两只 = 66.67%；跳空 ≤−2% 的一只 = 33.33%
    """
    prev = {"A": _day("A", 100, 100), "B": _day("B", 100, 100), "C": _day("C", 100, 100)}
    cur = {"A": _day("A", 110, 105), "B": _day("B", 104, 108), "C": _day("C", 97, 95)}
    r = ic.breadth_of_day(cur, prev)
    assert r["n"] == 3
    assert round(r["up3_pct"], 2) == 66.67
    assert round(r["dn2_pct"], 2) == 33.33
    assert round(r["median_gap"], 2) == 4.00        # 跳空 10 / 4 / −3 的中位
    assert round(r["median_chg"], 2) == 5.00        # 涨跌 5 / 8 / −5 的中位
    assert round(r["median_o2c"], 2) == -2.06       # 开→收 −4.55 / +3.85 / −2.06 的中位
    assert round(r["close_high_pct"], 2) == 33.33   # 只有 B 收高


def test_breadth_drops_ex_dividend_outliers():
    """30 分钟线是不复权原始价，除权日会算出假跳空。

    超过 ±25% 的样本直接剔除——2026-08-02 踩过一次（混用数据源导致寒武纪
    出现 +60% 的假日内涨幅）。科技股很少高比例分红，影响面很小。
    """
    prev = {"A": _day("A", 100, 100), "B": _day("B", 100, 100)}
    cur = {"A": _day("A", 104, 106), "B": _day("B", 50, 51)}   # B 跳空 −50%，除权
    r = ic.breadth_of_day(cur, prev)
    assert r["n"] == 1                    # B 被剔除
    assert r["dropped"] == 1
    assert round(r["median_gap"], 2) == 4.00


def test_breadth_labels_event():
    """事件标签：跳空≥3% 占比 ≥50% 记普涨，≤−2% 占比 ≥50% 记普跌，其余留空。"""
    assert ic.breadth_event(97.4, 0.0) == "普涨-强"
    assert ic.breadth_event(55.0, 0.0) == "普涨"
    assert ic.breadth_event(0.0, 80.0) == "普跌-强"
    assert ic.breadth_event(0.0, 55.0) == "普跌"
    assert ic.breadth_event(20.0, 20.0) == ""


def test_breadth_needs_previous_day():
    """没有前一交易日就算不出跳空，返回 None 而不是拿今开当昨收。"""
    assert ic.breadth_of_day({"A": _day("A", 100, 101)}, {}) is None
