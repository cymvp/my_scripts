"""breadth_recorder 纯函数的单元测试（不联网）。"""
import pytest

import breadth_recorder as br


# --- 指标计算 -------------------------------------------------------------

def test_breadth_counts_by_gap_threshold():
    """普涨强度 = 开盘跳空 >= +3% 的股票占比；普跌强度 = <= -2% 的占比。

    口径按 my_data/trading/回测依据.md：开盘跳空 = (今开 - 昨收) / 昨收。
    """
    quotes = [
        {"code": "a", "prev": 100.0, "open": 105.0},   # +5.0%  计入普涨
        {"code": "b", "prev": 100.0, "open": 103.0},   # +3.0%  边界，计入普涨
        {"code": "c", "prev": 100.0, "open": 101.0},   # +1.0%  都不计
        {"code": "d", "prev": 100.0, "open": 98.0},    # -2.0%  边界，计入普跌
        {"code": "e", "prev": 100.0, "open": 95.0},    # -5.0%  计入普跌
    ]
    b = br.breadth(quotes)
    assert b["n"] == 5
    assert b["up3_pct"] == pytest.approx(40.0)      # a、b
    assert b["dn2_pct"] == pytest.approx(40.0)      # d、e
    assert b["median_gap"] == pytest.approx(1.0)    # c 是中位


def test_breadth_skips_invalid_quotes():
    """昨收或今开为 0（停牌、数据缺失）的票要剔除，不能当成 0% 跳空混进分母。"""
    quotes = [
        {"code": "a", "prev": 100.0, "open": 105.0},
        {"code": "b", "prev": 0.0, "open": 10.0},      # 昨收缺失
        {"code": "c", "prev": 100.0, "open": 0.0},     # 未开盘
    ]
    b = br.breadth(quotes)
    assert b["n"] == 1
    assert b["up3_pct"] == pytest.approx(100.0)


def test_breadth_empty_returns_none():
    assert br.breadth([]) is None


# --- 事件判定 -------------------------------------------------------------

def test_classify_event_thresholds():
    """>=50% 记为强事件，>=30% 记为弱事件，都不到则不是事件。

    阈值来源：2025-06-24~2026-07-31 的 270 个交易日里，
    普涨日（>=50%）只有 6 天、普跌日 15 天，是罕见事件，所以两档都记。
    """
    assert br.classify_event(97.0, 0.0) == "普涨-强"
    assert br.classify_event(53.0, 0.0) == "普涨-强"
    assert br.classify_event(35.0, 0.0) == "普涨-弱"
    assert br.classify_event(29.0, 0.0) is None
    assert br.classify_event(0.0, 82.0) == "普跌-强"
    assert br.classify_event(0.0, 34.0) == "普跌-弱"
    assert br.classify_event(10.0, 10.0) is None


def test_classify_event_prefers_stronger_side():
    """同一天两边都超阈值时（极端分化），取占比更高的一边。"""
    assert br.classify_event(60.0, 55.0) == "普涨-强"
    assert br.classify_event(35.0, 70.0) == "普跌-强"


# --- CSV 行 ---------------------------------------------------------------

def test_csv_row_field_order_is_stable():
    """字段顺序必须固定，否则历史文件和新追加的行会错位。"""
    assert br.CSV_HEADER.split(",")[:5] == [
        "date", "n", "up3_pct", "dn2_pct", "median_gap"]


def test_format_row_matches_header_length():
    row = br.format_row("2026-07-31", {"n": 38, "up3_pct": 97.4, "dn2_pct": 0.0,
                                       "median_gap": 9.39, "median_chg": 3.52,
                                       "median_o2c": -5.09, "close_high_pct": 5.3},
                        "普涨-强")
    assert len(row.split(",")) == len(br.CSV_HEADER.split(","))
