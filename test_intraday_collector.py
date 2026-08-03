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


# --- 收盘检查（2026-08-03 加，防止把未收盘的当天写进仓库）-------------------

def test_refuses_to_write_today_before_close():
    """A 股 15:00 北京时间收盘。收盘前抓到的当天数据末根 K 是残缺的。

    2026-08-03 查出来的隐患：cron 是 `22 15 * * 1-5`，按本机 JST 触发 = 北京 14:22，
    加实测约 +30 分钟延迟 ≈ 北京 14:52，**收盘前 8 分钟就抓**。
    而脚本唯一的校验是「len(bars) != 8 就跳过」——如果新浪那时已经建好了第 8 根
    （14:30-15:00）只是收盘价还是当时的价，len==8 成立，残缺的收盘价就被写进仓库。
    又因为按 (code, date) 去重，第二天再跑也不会覆盖，**错值永久留下且不报错**。

    比漏抓严重得多：漏抓能自动补，写错不能自动改。
    """
    import datetime, zoneinfo
    BJ = zoneinfo.ZoneInfo("Asia/Shanghai")
    def bj(h, m, day=3):
        return datetime.datetime(2026, 8, day, h, m, tzinfo=BJ)

    # 当天，收盘前 → 不写
    assert ic.should_write("2026-08-03", bj(14, 52)) is False
    assert ic.should_write("2026-08-03", bj(15, 4)) is False
    # 当天，收盘后留足结算时间 → 写
    assert ic.should_write("2026-08-03", bj(15, 5)) is True
    assert ic.should_write("2026-08-03", bj(16, 52)) is True
    # 往期日期，任何时刻都可以写
    assert ic.should_write("2026-07-31", bj(9, 30)) is True
    assert ic.should_write("2026-07-31", bj(14, 52)) is True
    # 未来日期（时钟异常）不写
    assert ic.should_write("2026-08-04", bj(16, 0)) is False


def test_daily_fetch_window_covers_a_month():
    """日常增量的 datalen 要够大，让漏跑能自动补齐。

    新浪给的是最近 128 个交易日的滚动窗口，且脚本按 (code, date) 去重后追加，
    所以漏跑的日子只要还在取数窗口内，下一次运行就会自动补上。
    datalen=40 只覆盖 5 个交易日（自愈窗口 4 天），提到 240 覆盖 30 个交易日
    （自愈窗口 29 天）。一次请求，成本没差别。
    """
    assert ic.DAILY_DATALEN == 240
    assert ic.DAILY_DATALEN % 8 == 0          # A 股一天 8 根，整除才是整数天
