"""intraday_guide 纯函数的单元测试（不联网、不依赖行情）。"""
import json

import pytest

import intraday_guide as ig


# --- trim_mean 截尾均值 ---------------------------------------------------

def test_trim_mean_removes_both_tails():
    """10% 截尾：去掉最小的 10% 和最大的 10% 再取均值。

    2026-08-01 立此测试的原因：原始均值会被单个极端值翻转符号（做 T 那组
    8 个样本，去掉一个 -9.36% 后均值从 -0.87% 变 +0.35%），必须有抗极端值的口径。
    """
    v = list(range(1, 11))            # 1..10，去掉 1 和 10，剩 2..9
    assert ig.trim_mean(v, 0.10) == pytest.approx(5.5)


def test_trim_mean_zero_ratio_equals_plain_mean():
    assert ig.trim_mean([1, 2, 3, 100], 0.0) == pytest.approx(26.5)


def test_trim_mean_small_sample_falls_back_to_plain_mean():
    """样本太少时截尾会把数据削光，退回普通均值而不是报错或返回空。"""
    assert ig.trim_mean([1, 2, 3], 0.10) == pytest.approx(2.0)
    assert ig.trim_mean([5], 0.10) == pytest.approx(5.0)


def test_trim_mean_empty_returns_none():
    assert ig.trim_mean([], 0.10) is None


# --- 趋势状态判定 ---------------------------------------------------------

def test_classify_trend_boundaries():
    """趋势用「昨收距近60日最高收盘的回撤」判定，边界必须确定。

    -15% 与 -5% 是分界点，归属规则：<=-15 下跌，(-15,-5] 震荡，>-5 上涨。
    """
    assert ig.classify_trend(-34.75) == "下跌"     # 中际旭创 2026-07-31 实际值
    assert ig.classify_trend(-15.0) == "下跌"      # 边界归下跌
    assert ig.classify_trend(-14.99) == "震荡"
    assert ig.classify_trend(-5.0) == "震荡"       # 边界归震荡
    assert ig.classify_trend(-4.99) == "上涨"
    assert ig.classify_trend(2.0) == "上涨"


# --- 开盘涨幅分档 ---------------------------------------------------------

def test_classify_gap_buckets():
    assert ig.classify_gap(11.11) == "高开>=3%"    # 中际旭创 2026-07-31
    assert ig.classify_gap(3.0) == "高开>=3%"
    assert ig.classify_gap(2.99) == "高开0~3%"
    assert ig.classify_gap(0.0) == "高开0~3%"
    assert ig.classify_gap(-0.01) == "低开0~3%"
    assert ig.classify_gap(-2.99) == "低开0~3%"
    assert ig.classify_gap(-3.0) == "低开>=3%"


# --- 量能分档 -------------------------------------------------------------

def test_classify_volume_buckets():
    assert ig.classify_volume(0.57) == "缩量"
    assert ig.classify_volume(0.85) == "平量"      # 边界归平量
    assert ig.classify_volume(1.14) == "平量"
    assert ig.classify_volume(1.49) == "放量"      # 中际旭创 2026-07-31
    assert ig.classify_volume(1.6) == "巨量"


# --- 分位数 ---------------------------------------------------------------

def test_percentile_of_value():
    """当前值落在历史分布的第几分位——盘中实时层的核心输出。"""
    hist = [-3.0, -1.0, 0.0, 1.0, 3.0]
    assert ig.percentile_of(-5.0, hist) == 0        # 比所有历史值都低
    assert ig.percentile_of(0.5, hist) == 60        # 5个里有3个小于它
    assert ig.percentile_of(10.0, hist) == 100


def test_percentile_of_empty_history_returns_none():
    assert ig.percentile_of(1.0, []) is None


# --- 时段归属 -------------------------------------------------------------

def test_slot_of_maps_time_to_30min_bucket():
    """A股 9:30-11:30 / 13:00-15:00，8 个 30 分钟时段，按「归属到哪根K线」映射。"""
    assert ig.slot_of("09:35") == "10:00"
    assert ig.slot_of("10:00") == "10:00"
    assert ig.slot_of("10:01") == "10:30"
    assert ig.slot_of("11:29") == "11:30"
    assert ig.slot_of("13:20") == "13:30"
    assert ig.slot_of("14:59") == "15:00"


def test_slot_of_outside_session_returns_none():
    """午间休市和盘前盘后不属于任何时段，返回 None 而不是猜一个。"""
    assert ig.slot_of("09:20") is None
    assert ig.slot_of("12:00") is None
    assert ig.slot_of("15:30") is None


# --- 最近期偏离检查 -------------------------------------------------------

def test_recent_drift_flags_deviation():
    """2026-08-01 新增的方法论纪律：统计结论必须附带「最近一个月是否偏离历史」。

    实测背景：2026H2 的当日开→收比 2024H1~2026H1 整体低约 2 个百分点，
    不检查就会拿历史均值给出系统性偏乐观的结论。
    """
    # 历史至少要 10 个样本才算标准差，少于这个数算出来的 z 值没有意义
    hist = [0.1, 0.2, -0.1, 0.0, 0.3, 0.15, -0.05, 0.25, 0.05, 0.1, -0.2, 0.2]
    assert ig.recent_drift(hist, recent=[-2.0, -2.3, -1.8])["deviated"] is True
    assert ig.recent_drift(hist, recent=[0.1, 0.2, 0.0])["deviated"] is False


def test_recent_drift_insufficient_sample_returns_unknown():
    """样本不足时不下结论，标 None，不假装「没偏离」。

    两种不足都要覆盖：最近样本为空、历史样本少于 10 个。
    """
    hist = [0.1, 0.2, -0.1, 0.0, 0.3, 0.15, -0.05, 0.25, 0.05, 0.1, -0.2, 0.2]
    assert ig.recent_drift(hist, recent=[])["deviated"] is None
    assert ig.recent_drift([0.1, 0.2, 0.3], recent=[1.0, 2.0, 3.0])["deviated"] is None


def test_recent_drift_uses_standard_error_not_raw_sd():
    """判「最近均值是否偏离历史均值」，分母必须是均值的标准误（sd/√n），
    不是单日观测值的标准差。

    2026-08-01 实测暴露的 bug：中际旭创「下跌趋势×低开>=3%」历史当日开→收
    +0.44%、最近一个月 -4.40%，差了近 5 个百分点，却因为用了原始标准差做分母
    算出 z=-1.59 而被判为「未偏离」。个股日涨跌本身波动就有好几个百分点，
    用它做分母永远报不出警。
    """
    # 历史：均值约 0，单日波动很大（标准差约 3）
    hist = [-4.0, 3.5, -3.0, 2.8, -2.5, 3.2, -3.5, 2.0, -1.5, 3.0, -2.8, 2.8]
    recent = [-4.4, -4.0, -5.0, -4.2, -4.6]      # 均值约 -4.4，明显偏离
    r = ig.recent_drift(hist, recent)
    assert r["deviated"] is True
    assert r["n_recent"] == 5
    # 同样离散度但均值贴近历史的样本，不该被误报
    assert ig.recent_drift(hist, [-3.0, 3.2, -2.0, 2.5, 0.1])["deviated"] is False


# ---------------------------------------------------------------------------
# 以下为 advise 子命令的新增功能，spec 见
# docs/intraday/spec/2026-08-03-intraday-guide-spec.md
# ---------------------------------------------------------------------------


def test_day_amplitude():
    """日振幅 = (当日最高 − 当日最低) ÷ 当日开盘。

    中际旭创 2026-07-31：(986.89 − 901.00) ÷ 960.00 = 8.947%
    """
    assert round(ig.day_amplitude(o=960.0, h=986.89, l=901.0), 3) == 8.947


def test_day_amplitude_zero_open_returns_none():
    assert ig.day_amplitude(o=0.0, h=1.0, l=0.5) is None


def test_predicted_amplitude_uses_median_of_last_10():
    """预测振幅 = 该股过去 10 个交易日日振幅的中位数，不乘任何系数。

    用中位数而不是平均数：8 种窗口/统计量配置严格样本外对比，中位数在
    下跌/震荡/上涨每一格都优于平均数（平均数被涨停跌停拉偏，系统性高估 10~20%）。
    """
    # 12 天，只应取最后 10 天；最后 10 天的振幅是 1..10，中位 5.5
    bars = [{"o": 100.0, "h": 100.0 + a, "l": 100.0} for a in [99.0, 98.0] + list(range(1, 11))]
    assert ig.predicted_amplitude(bars) == 5.5


def test_predicted_amplitude_insufficient_history_returns_none():
    bars = [{"o": 100.0, "h": 103.0, "l": 100.0}] * 9
    assert ig.predicted_amplitude(bars) is None


def test_amp_cell_boundaries():
    """振幅分格左闭右开：3.0 落 3~4%，4.0 落 4~6%，6.0 落 6~8%，8.0 落 >8%。"""
    assert ig.amp_cell(2.99) == "0~3%"
    assert ig.amp_cell(3.0) == "3~4%"
    assert ig.amp_cell(3.99) == "3~4%"
    assert ig.amp_cell(4.0) == "4~6%"
    assert ig.amp_cell(5.99) == "4~6%"
    assert ig.amp_cell(6.0) == "6~8%"
    assert ig.amp_cell(7.99) == "6~8%"
    assert ig.amp_cell(8.0) == ">8%"
    assert ig.amp_cell(6.83) == "6~8%"


def test_risk_parity_allocates_least_to_most_volatile():
    """风险平价：分配权重与预测振幅成反比，波动最大的分得最少。

    50 万，三只票预测振幅 6.83 / 5.00 / 4.00：
    倒数 0.1464129 / 0.2 / 0.25，合计 0.5964129
    分配 122745 / 167669 / 209586 元
    """
    alloc = ig.risk_parity({"A": 6.83, "B": 5.0, "C": 4.0}, cash=500000.0)
    assert round(alloc["A"]) == 122745
    assert round(alloc["B"]) == 167669
    assert round(alloc["C"]) == 209586
    assert round(sum(alloc.values())) == 500000
    assert alloc["A"] < alloc["B"] < alloc["C"]


def test_risk_parity_ignores_missing_amplitude():
    alloc = ig.risk_parity({"A": 5.0, "B": None}, cash=100000.0)
    assert "B" not in alloc
    assert round(alloc["A"]) == 100000


def test_position_cap():
    """仓位上限 = 总资产 × 单笔风险预算 ÷ 止损宽度。

    总资产 300 万、风险预算 1%、止损 5% → 60 万
    """
    assert ig.position_cap(total=3_000_000, risk_pct=1.0, stop_pct=5.0) == 600_000
    assert ig.position_cap(total=3_000_000, risk_pct=1.0, stop_pct=3.0) == 1_000_000


def test_position_cap_zero_stop_returns_none():
    assert ig.position_cap(total=3_000_000, risk_pct=1.0, stop_pct=0.0) is None


def test_walked_amplitude():
    """盘中已走振幅 = (至此刻最高 − 至此刻最低) ÷ 今日开盘。

    中际旭创 2026-07-31 到 10:30：(986.89 − 934.00) ÷ 960.00 = 5.51%
    """
    bars = [{"o": 960.0, "h": 986.89, "l": 947.04}, {"o": 970.0, "h": 975.0, "l": 934.0}]
    assert round(ig.walked_amplitude(bars), 2) == 5.51


def test_walked_amplitude_empty_returns_none():
    assert ig.walked_amplitude([]) is None


def test_implied_full_amplitude():
    """反推全天振幅 = 盘中已走振幅 ÷ 该时点的「已走%」。

    下跌趋势 10:30 已走 75.3%，已走振幅 5.51% → 5.51 / 0.753 = 7.32%
    """
    table_e = {"下跌": {"10:30": {"walked_pct": 75.3, "remain": 1.22}}}
    assert round(ig.implied_full_amplitude(5.51, "下跌", "10:30", table_e), 2) == 7.32


def test_implied_full_amplitude_missing_cell_returns_none():
    """基准缺失时返回 None，不静默用默认值顶上。"""
    assert ig.implied_full_amplitude(5.0, "下跌", "09:00", {"下跌": {}}) is None


def test_calibration_flag_when_far_from_prediction():
    """反推值与盘前预测相差超过 50% 时提示偏离常态。"""
    assert ig.calibration_off(pred=6.83, implied=7.32) is False
    assert ig.calibration_off(pred=6.83, implied=11.0) is True
    assert ig.calibration_off(pred=6.83, implied=3.0) is True
    assert ig.calibration_off(pred=None, implied=7.0) is None


# ---- 基准表生成（纯计算，不联网）----------------------------------------


_REC_SEQ = [0]


def _rec(trend, cell, o, h, l, c, date=None):
    """date 默认每次递增一天——表 A 的聚类稳健区间按日期分组，日期是必需字段。
    默认给不同日期，等价于「天内无相关」，区间退化成普通二项区间。"""
    if date is None:
        _REC_SEQ[0] += 1
        date = f"2026-{_REC_SEQ[0] // 28 + 1:02d}-{_REC_SEQ[0] % 28 + 1:02d}"
    return {"date": date, "trend": trend, "amp_cell": cell,
            "o": o, "h": h, "l": l, "c": c}


def test_build_table_a_counts_touches_both_directions():
    """表 A：向下 = 当日最低 < 开盘×(1−X) 的比例；向上 = 当日最高 > 开盘×(1+X) 的比例。

    不等号取严格：最低必须【低于】开盘×(1−X)，最高必须【高于】开盘×(1+X)，
    恰好等于不算触及。查表手册里所有已发布的数字都是按这个口径算的。

    四条记录，开盘都是 100：
      最低 96/98/99/101 → 低于 97 的只有第一条 = 25%
      最高 104/101/100/103 → 高于 103 的只有第一条（第四条恰好 103 不算）= 25%
      最低低于 99 的有 96/98 两条 = 50%… 但 −1% 档是低于 99，96/98 都算，
      加上第三条 99 不算，所以 −1% 是 50%
    """
    recs = [_rec("下跌", "4~6%", 100, 104, 96, 100),
            _rec("下跌", "4~6%", 100, 101, 98, 100),
            _rec("下跌", "4~6%", 100, 100, 99, 100),
            _rec("下跌", "4~6%", 100, 103, 101, 102)]
    t = ig.build_table_a(recs, min_n=4)
    assert t["下跌"]["4~6%"]["n"] == 4
    assert t["下跌"]["4~6%"]["down"]["3"] == 25.0
    assert t["下跌"]["4~6%"]["up"]["3"] == 25.0
    assert t["下跌"]["4~6%"]["down"]["1"] == 50.0


def test_build_table_a_skips_cells_below_min_sample():
    """样本不足的格子不出现在基准里，避免用几个样本算出的比例被当成概率。"""
    recs = [_rec("下跌", "4~6%", 100, 104, 96, 100)] * 5
    assert ig.build_table_a(recs, min_n=150) == {}


def test_build_table_b_bought_high_and_back_above():
    """表 B：以【成交价】为基准算「买高了」，以【开盘价】为基准算「触发后回到开盘上方」。

    三条记录里有两条触发 −3%（成交价 97），触发概率 2/3 = 66.7%：
      收盘 96 → 低于成交价，买高了；也低于开盘价，没回到开盘上方
      收盘 101 → 高于成交价，没买高；也高于开盘价，回到了开盘上方
    买高了 50%，回到开盘上方 50%，无效止损概率 = 66.7% × 50% = 33.3%
    """
    recs = [_rec("下跌", "4~6%", 100, 100, 95, 96),
            _rec("下跌", "4~6%", 100, 102, 96, 101),
            _rec("下跌", "4~6%", 100, 101, 99, 100)]   # 未触发 −3%，不计入分母
    b = ig.build_table_b(recs, min_n=3)
    d3 = b["下跌"]["4~6%"]["down"]["3"]
    assert d3["triggered"] == 2
    assert d3["bought_high"] == 50.0
    assert d3["back_above"] == 50.0
    assert round(d3["ineffective"], 1) == 33.3


def test_build_table_b_sell_early_is_complement_of_bought_high():
    """高位挂单：卖亏了（收盘 > 成交价）与买高了（收盘 < 成交价）互补。"""
    recs = [_rec("上涨", "4~6%", 100, 104, 100, 105),
            _rec("上涨", "4~6%", 100, 104, 98, 101)]
    u3 = ig.build_table_b(recs, min_n=2)["上涨"]["4~6%"]["up"]["3"]
    assert u3["triggered"] == 2
    assert u3["sell_early"] == 50.0
    assert round(u3["sell_early"] + u3["bought_high"]) == 100


def test_build_table_e_walked_pct_and_remain():
    """表 E：已走% = 截止该时点的高低差 ÷ 全天高低差；剩余空间 = 差额 ÷ 今开。

    一条记录，今开 100，两根 K：第一根高低 (106, 98)，第二根 (108, 97)
      10:00 已走 = (106−98)/(108−97) = 8/11 = 72.7%
      10:00 剩余 = (11 − 8)/100 = 3.0%
      10:00 价格位置 = (该根收盘 104 − 100)/100 = 4.0%
    """
    rec = {"trend": "下跌", "o": 100.0,
           "bars": [{"t": "10:00", "o": 100.0, "h": 106.0, "l": 98.0, "c": 104.0, "v": 10.0},
                    {"t": "10:30", "o": 104.0, "h": 108.0, "l": 97.0, "c": 99.0, "v": 30.0}]}
    e = ig.build_table_e([rec], min_n=1)["下跌"]
    assert round(e["10:00"]["walked_pct"], 1) == 72.7
    assert round(e["10:00"]["remain"], 1) == 3.0
    assert round(e["10:00"]["price_pos"], 1) == 4.0
    assert e["10:30"]["walked_pct"] == 100.0
    assert e["10:30"]["remain"] == 0.0


def test_build_table_d_segment_metrics():
    """表 D：段内为正比例、段振幅、段振幅占全天、成交量占比。

    同上那条记录：
      10:00 段内 100→104 为正；段振幅 (106−98)/100 = 8%；占全天 8/11 = 72.7%
      成交量占比 10/(10+30) = 25%
    """
    rec = {"trend": "下跌", "o": 100.0,
           "bars": [{"t": "10:00", "o": 100.0, "h": 106.0, "l": 98.0, "c": 104.0, "v": 10.0},
                    {"t": "10:30", "o": 104.0, "h": 108.0, "l": 97.0, "c": 99.0, "v": 30.0}]}
    d = ig.build_table_d([rec], min_n=1)["下跌"]
    assert d["10:00"]["pos_ratio"] == 100.0
    assert round(d["10:00"]["seg_amp"], 1) == 8.0
    assert round(d["10:00"]["seg_share"], 1) == 72.7
    assert round(d["10:00"]["vol_share"], 1) == 25.0
    assert d["10:30"]["pos_ratio"] == 0.0


# ---- 纪律检查（交易纪律.md 的 21 条）--------------------------------------


def _portfolio(**kw):
    # 示例组合，金额是示意值。比例刻意做成「三条硬性上限全破」：
    # 主题 80%、单票 33.3%、现金 16.7%
    p = {"holdings": {"300308": 100_000.0, "688825": 100_000.0, "301526": 40_000.0},
         "cash": 50_000.0, "total": 300_000.0, "risk_pct": 1.0, "leverage": 0.0}
    p.update(kw)
    return p


def _ids(results):
    return {r["id"] for r in results if r["status"] == "fail"}


def test_check_hard_caps_all_three_breached():
    """三条硬性上限：单一主题<=50%、单票<=25%、现金>=30%。

    示例组合刻意做成三条全破：主题 80%、单票 33.3%、现金 16.7%。
    """
    r = ig.discipline_checks(_portfolio(), snapshots={}, slot="10:30")
    assert {"theme_cap", "single_cap", "cash_floor"} <= _ids(r)


def test_check_hard_caps_all_pass():
    p = _portfolio(holdings={"300308": 60_000.0, "002185": 40_000.0},
                   cash=200_000.0, total=300_000.0)
    r = ig.discipline_checks(p, snapshots={}, slot="10:30")
    assert not ({"theme_cap", "single_cap", "cash_floor"} & _ids(r))


def test_check_forbids_buy_on_downtrend_big_gap_up():
    """下跌趋势 × 高开>=5%：收高率 23.5%，禁止买入。"""
    snaps = {"300308": {"trend": "下跌", "gap_pct": 11.11}}
    r = ig.discipline_checks(_portfolio(), snapshots=snaps, slot="10:30")
    assert "no_buy_gap_up" in _ids(r)
    snaps = {"300308": {"trend": "下跌", "gap_pct": 4.9}}
    r = ig.discipline_checks(_portfolio(), snapshots=snaps, slot="10:30")
    assert "no_buy_gap_up" not in _ids(r)


def test_check_first_hour_no_ad_hoc_decision():
    """开盘后第一个小时不做临时决策。10:00 和 10:30 两个时段都算。"""
    assert "first_hour" in _ids(ig.discipline_checks(_portfolio(), {}, slot="10:00"))
    assert "first_hour" in _ids(ig.discipline_checks(_portfolio(), {}, slot="10:30"))
    assert "first_hour" not in _ids(ig.discipline_checks(_portfolio(), {}, slot="11:00"))


def test_check_after_1400_blocks_round_trip_not_add():
    """14:00 之后：禁止日内往返，但明确不禁止加仓。

    这一条 2026-08-03 改过——上一版写成「不新开日内仓位」，把两件事混成一条，
    而且对买方和卖方的含义相反。输出必须同时说清楚两个方向。
    """
    r = ig.discipline_checks(_portfolio(), {}, slot="14:30")
    hit = [x for x in r if x["id"] == "no_round_trip"]
    assert hit and hit[0]["status"] == "fail"
    assert "加仓" in hit[0]["detail"]
    assert "往返" in hit[0]["detail"]
    assert "no_round_trip" not in _ids(ig.discipline_checks(_portfolio(), {}, slot="13:30"))


def test_check_no_reversal_expectation_at_1430():
    """14:30 时离开盘价已超 4%，不要期待尾盘翻转（实测翻转概率 0.2%）。"""
    snaps = {"300308": {"trend": "下跌", "pos_vs_open": -6.15}}
    assert "no_reversal" in _ids(ig.discipline_checks(_portfolio(), snaps, slot="14:30"))
    snaps = {"300308": {"trend": "下跌", "pos_vs_open": -2.0}}
    assert "no_reversal" not in _ids(ig.discipline_checks(_portfolio(), snaps, slot="14:30"))
    # 14:30 之前不触发这一条
    snaps = {"300308": {"trend": "下跌", "pos_vs_open": -6.15}}
    assert "no_reversal" not in _ids(ig.discipline_checks(_portfolio(), snaps, slot="11:00"))


def test_check_leverage_cap():
    """杠杆与反向产品仓位 <= 总资产 5%。"""
    assert "leverage_cap" in _ids(ig.discipline_checks(_portfolio(leverage=20_000.0), {}, "11:00"))
    assert "leverage_cap" not in _ids(ig.discipline_checks(_portfolio(leverage=10_000.0), {}, "11:00"))


def test_check_sector_concentration():
    """同板块多只票不是分散，是加杠杆——同日聚类下它们同涨同跌。"""
    snaps = {"300308": {"trend": "下跌"}, "300502": {"trend": "下跌"}}   # 都是光模块
    hit = [x for x in ig.discipline_checks(_portfolio(), snaps, "11:00") if x["id"] == "sector_concentration"]
    assert hit and hit[0]["status"] == "fail"
    assert "光模块" in hit[0]["detail"]


def test_check_returns_all_21_items():
    """21 条必须全部出现在结果里，包括通过的和只做提示的，方便逐条对照。"""
    r = ig.discipline_checks(_portfolio(), {}, "11:00")
    assert len(r) == 21
    assert {x["status"] for x in r} <= {"pass", "fail", "ask", "info"}
    assert sum(1 for x in r if x["status"] == "ask") == 5
    assert sum(1 for x in r if x["status"] == "info") == 4


# ---- 参数解析与快照组装 ---------------------------------------------------


def test_parse_amount_accepts_chinese_units():
    """持仓和现金允许写「100万」这种口语形式，也允许纯数字。"""
    assert ig.parse_amount("100万") == 1_000_000.0
    assert ig.parse_amount("1.5万") == 15_000.0
    assert ig.parse_amount("40w") == 400_000.0
    assert ig.parse_amount("500000") == 500_000.0
    assert ig.parse_amount("1亿") == 100_000_000.0


def test_parse_amount_rejects_garbage():
    """金额解析失败必须报错，不能当成 0 继续算——仓位上限会因此算错。"""
    with pytest.raises(ValueError):
        ig.parse_amount("很多")


def test_resolve_code_accepts_name_and_digits():
    """代码允许写六位数字、带前缀、或股票名。"""
    assert ig.resolve_code("300308") == "sz300308"
    assert ig.resolve_code("sz300308") == "sz300308"
    assert ig.resolve_code("中际旭创") == "sz300308"
    assert ig.resolve_code("688825") == "sh688825"      # 池外票也要能用
    assert ig.resolve_code("不存在的名字") is None


def test_parse_holdings():
    got = ig.parse_holdings(["300308=100万", "中际旭创=1万"])
    assert got == {"sz300308": 1_010_000.0}             # 同一只票累加


def test_stop_options_reads_both_tables():
    """止损候选：每档给出触发概率、触发后回到开盘上方、无效止损概率、仓位上限。

    无效止损概率 = 触发概率 × 触发后回到开盘上方。下跌 6~8% 的 −3% 档：
    37.6% × 20.5% = 7.7%，也就是每 100 个交易日有 7.7 天白止损一次。
    """
    ta = {"下跌": {"6~8%": {"n": 935, "down": {"3": 37.6, "5": 18.6}, "up": {}}}}
    tb = {"下跌": {"6~8%": {"down": {"3": {"back_above": 20.5}, "5": {"back_above": 7.5}},
                            "up": {}}}}
    opts = ig.stop_options("下跌", "6~8%", ta, tb, total=3_000_000, risk_pct=1.0)
    o3 = [o for o in opts if o["stop_pct"] == 3][0]
    assert o3["trigger"] == 37.6
    assert o3["back_above"] == 20.5
    assert round(o3["ineffective"], 1) == 7.7
    assert o3["cap"] == 1_000_000
    o5 = [o for o in opts if o["stop_pct"] == 5][0]
    assert o5["cap"] == 600_000


def test_stop_options_missing_baseline_returns_empty():
    """基准缺该格时返回空，不猜一个默认概率。"""
    assert ig.stop_options("下跌", "6~8%", {}, {}, total=3_000_000, risk_pct=1.0) == []


def test_downtrend_high_amp_warning_pairs_two_facts():
    """「下跌 + 预测振幅 >8%」这一格必须同时说两件事，否则会误导：
    表 C 说挂 −3% 限价比开盘市价买好 +0.817%；
    表 B 扩展说成交后到收盘中位仍是 −0.88%。
    限价比市价好，但绝对上仍然亏。
    """
    w = ig.special_cell_note("下跌", ">8%")
    assert w is not None
    assert "+0.817%" in w and "-0.88%" in w
    assert ig.special_cell_note("下跌", "6~8%") is None
    assert ig.special_cell_note("震荡", ">8%") is None


def test_daily_bars_request_size_is_800_not_more():
    """腾讯日线接口要得多反而给得少：请求 1000 根只回 641 根（起点 2023-12），
    请求 800 根回 801 根（起点 2023-04）。2026-08-03 实测。

    这直接影响表 A 的样本量和分格概率，改大之前先用真实接口验证。
    """
    assert ig.DAILY_BARS == 800


def test_position_cap_check_asks_when_stop_not_chosen():
    """没指定止损宽度时，仓位上限算不出来——必须标「需回答」而不是「通过」。

    2026-08-03 端到端跑出来的问题：snapshot 里没有 stop_pct，检查静默报通过，
    看起来像是仓位合规，其实是根本没查。计算类代码不允许这种静默降级。
    """
    snaps = {"300308": {"trend": "下跌", "amp_cell": ">8%"}}      # 没有 stop_pct
    hit = [x for x in ig.discipline_checks(_portfolio(), snaps, "11:00")
           if x["id"] == "position_cap"][0]
    assert hit["status"] == "ask"
    assert "止损" in hit["detail"] or "止损" in hit["title"]
    # 给了止损宽度就正常判定
    snaps["300308"]["stop_pct"] = 5.0
    hit = [x for x in ig.discipline_checks(_portfolio(), snaps, "11:00")
           if x["id"] == "position_cap"][0]
    assert hit["status"] == "fail"          # 10万 > 30万×1%÷5% = 6000


# ---- 2026-08-03 实盘调用暴露的两处改进 ------------------------------------


def test_final_slot_has_no_remaining_room_information():
    """最后一个时段（14:30-15:00）的「已走%」按定义就是 100%，剩余空间必然是 0。

    2026-08-03 14:51 实际调用时输出「剩余空间中位 0.00%，25分位 100.0% / 75分位 100.0%」，
    看着像「今天不会再动了」，其实是这个指标在收盘段结构上就没有信息量——
    累计到最后一根 K 线，当然等于全天。必须标注出来而不是照常输出一个 0。
    """
    assert ig.is_final_slot("15:00") is True
    assert ig.is_final_slot("14:30") is False
    assert ig.is_final_slot("10:00") is False
    assert ig.is_final_slot(None) is False


def test_group_by_trend_merges_same_trend_stocks():
    """表 D / 表 E 只按「趋势 × 时段」查，同趋势同时段的票查到的是同一格。

    2026-08-03 实际调用时三只票里有两只都是下跌趋势，时点结构那一段一字不差
    输出了两遍。应该按趋势合并，每组只输出一次。
    """
    snaps = {"a": {"trend": "下跌"}, "b": {"trend": "下跌"}, "c": {"trend": "上涨"},
             "d": {"error": "取数失败"}}
    g = ig.group_by_trend(snaps)
    assert list(g) == ["下跌", "上涨"]          # 保持首次出现的顺序
    assert g["下跌"] == ["a", "b"]
    assert g["上涨"] == ["c"]
    assert "d" not in [x for v in g.values() for x in v]   # 取数失败的不参与分组


def test_now_slot_uses_beijing_time_not_local():
    """时段必须按北京时间判定，不能用本机时间。

    2026-08-03 实盘调用时踩到：本机时区是 JST，比北京快 1 小时。
    本机 14:51 实际是北京 13:51，正确时段是 14:00（13:30-14:00 那根），
    而工具用本机时间算出 15:00，**查表查错了两格**——表 D/表 E 是按时段索引的，
    时段错了整段结论都错。

    A 股按北京时间开收盘，与本机时区无关。
    """
    import datetime, zoneinfo
    bj = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Shanghai"))
    assert ig.now_slot() == ig.slot_of(bj.strftime("%H:%M"))
    # 显式传入时间时按传入的算，方便回溯
    assert ig.now_slot("13:51") == "14:00"
    assert ig.now_slot("14:51") == "15:00"
    assert ig.now_slot("09:20") is None


def test_build_reads_accumulated_store_not_api(tmp_path):
    """build 的 30 分钟数据必须从 intraday_collector 积累的仓库读，不能每次重抓接口。

    2026-08-03 发现的设计缺陷：build 有自己的 fetch_m30，每次向新浪要 datalen=1023，
    永远只能拿到接口给的 128 个交易日；而 intraday_bars.jsonl 已经攒到 137 天且还在长。
    结果是 intraday_collector 每天积累的数据【从来没被用上】，随着时间推移差距只会拉大。

    读仓库之后，攒多少就能用多少，这才是 intraday_collector 存在的意义。
    """
    store = tmp_path / "bars.jsonl"
    bars = [{"t": t, "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.5, "v": 100.0}
            for t in ("10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00")]
    with open(store, "w", encoding="utf-8") as fh:
        for d in ("2026-07-30", "2026-07-31"):
            fh.write(json.dumps({"code": "sz300308", "date": d, "bars": bars}) + "\n")
        fh.write(json.dumps({"code": "sz300502", "date": "2026-07-31", "bars": bars}) + "\n")
        fh.write(json.dumps({"code": "sz300394", "date": "2026-07-31", "bars": bars[:4]}) + "\n")

    got = ig.load_m30_store(str(store))
    assert set(got) == {("sz300308", "2026-07-30"), ("sz300308", "2026-07-31"),
                        ("sz300502", "2026-07-31")}      # 不足 8 根的被丢掉
    assert len(got[("sz300308", "2026-07-31")]) == 8
    assert got[("sz300308", "2026-07-31")][0]["t"] == "10:00"


def test_load_m30_store_missing_file_returns_empty():
    """仓库不存在时返回空字典，让 build 明确报「没有 30 分钟数据」，不静默去抓接口。"""
    assert ig.load_m30_store("/nonexistent/path/bars.jsonl") == {}


def test_price_level_label_states_basis_and_absolute_price():
    """档位标签必须写明「距今开」并给出绝对价位。

    2026-08-03 用户指出：表格里 −1% / +3% 这些档位没说清是相对什么算的。
    它们全部相对【今日开盘价】，而涨跌幅相对【昨收】——两个基准完全不同。
    中际旭创 2026-08-03 今开 891.00：「−3%」指 864.27 这个价位，
    而当天涨跌幅是 +0.05%（相对昨收 902.01）。同一天两个数毫不相干。

    光写百分比会让人拿去和行情软件的涨跌幅对照，直接看错价位。
    """
    assert ig.level_label(3, 891.0, down=True) == "距今开 −3%（864.27 元）"
    assert ig.level_label(3, 891.0, down=False) == "距今开 +3%（917.73 元）"
    assert ig.level_label(5, 891.0, down=True) == "距今开 −5%（846.45 元）"
    # 拿不到开盘价时只给档位，不编价格
    assert ig.level_label(3, None, down=True) == "距今开 −3%"


# ---- 表 F：剩余触及概率（2026-08-03 新增，修正盘中用全天概率这个错误）------


def test_need_ratio():
    """还需再跌 = (此刻最低 − 目标价) ÷ 今开 ÷ 预测振幅。

    用预测振幅归一化，是为了把「预测振幅格」这个维度压掉——
    否则 趋势×时点×振幅格×距离 四个维度会把 4826 个股票日切碎。
    归一化后每格 1100~3000 个样本。
    """
    # 今开 100，此刻最低 98，目标 97 → 还需再跌 1%；预测振幅 5% → 比值 0.2
    assert round(ig.need_ratio(cur_low=98.0, target=97.0, day_open=100.0, pred_amp=5.0), 3) == 0.2
    # 已经触及，返回 0
    assert ig.need_ratio(cur_low=96.0, target=97.0, day_open=100.0, pred_amp=5.0) == 0.0
    assert ig.need_ratio(cur_low=97.0, target=97.0, day_open=100.0, pred_amp=5.0) == 0.0
    # 缺参数返回 None，不猜
    assert ig.need_ratio(98.0, 97.0, 100.0, None) is None
    assert ig.need_ratio(98.0, 97.0, 0.0, 5.0) is None


def test_need_bucket_boundaries():
    """还需再跌分四档，左闭右开。"""
    assert ig.need_bucket(0.0) == "已触及"
    assert ig.need_bucket(0.05) == "<0.15"
    assert ig.need_bucket(0.15) == "0.15~0.35"
    assert ig.need_bucket(0.35) == "0.35~0.6"
    assert ig.need_bucket(0.6) == ">0.6"
    assert ig.need_bucket(2.0) == ">0.6"
    assert ig.need_bucket(None) is None


def test_remaining_touch_prob_replaces_full_day_prob_intraday():
    """盘中必须用剩余触及概率，不能用表 A 的全天概率。

    2026-08-03 用户质疑「14 点的建议有帮助么」时查出来的错误：
    表 A 的「当日最低 < 今开−3%」是**全天**口径，假设这一天还没开始。
    而 14:00 时当日最低已经基本定型，剩下只有最后一小时。

    实测（下跌 × >8% 格）按 14:00 时最低已到哪里分组，全天最终跌破 −3% 的比例：
      还没跌破 −1%       0.0%
      跌破 −1% 未破 −2%  4.9%
      跌破 −2% 未破 −3%  31.1%
      已经跌破 −3%       100%
    而表 A 对这四种情况给同一个数 73.2%。中际旭创 2026-08-03 14:00 时
    最低距开 −1.70%，真实剩余概率 4.9%，工具给 64.2%，**高估近 70 个百分点**。
    """
    tf = {"下跌": {"14:00": {"<0.15": {"n": 1150, "p": 18.3},
                             "0.15~0.35": {"n": 1447, "p": 4.1},
                             "0.35~0.6": {"n": 1420, "p": 0.4},
                             ">0.6": {"n": 2529, "p": 0.0}}}}
    # 今开 891，此刻最低 875.87，目标 −3%（864.27），预测振幅 8.51
    r = ig.remaining_touch("下跌", "14:00", cur_low=875.87, target=864.27,
                           day_open=891.0, pred_amp=8.51, table_f=tf)
    assert r["bucket"] == "0.15~0.35"          # 还需再跌 1.30/8.51 = 0.153
    assert r["p"] == 4.1
    assert r["n"] == 1447
    # 已经触及的目标，概率 100%
    r2 = ig.remaining_touch("下跌", "14:00", cur_low=860.0, target=864.27,
                            day_open=891.0, pred_amp=8.51, table_f=tf)
    assert r2["bucket"] == "已触及" and r2["p"] == 100.0
    # 基准缺该格，返回 None 不猜
    assert ig.remaining_touch("下跌", "09:00", 875.87, 864.27, 891.0, 8.51, tf) is None


def test_need_ratio_up_and_remaining_touch_up():
    """表 F 的向上版本：还需再涨多少才够得着，以及剩余触及概率。

    2026-08-04 11:18 用户指出的缺口：中际旭创现价 990.71、最高 996.00、
    距今开 +5.39%，贴着当日高点，而 [5B] 全是向下档位，向上一个都没有。
    卖单成交率、止盈能不能够到，盘中同样需要条件化，不能用表 A 的全天值。
    """
    # 今开 100，此刻最高 105，目标 107 → 还需再涨 2%；预测振幅 8% → 比值 0.25
    assert round(ig.need_ratio_up(cur_high=105.0, target=107.0, day_open=100.0, pred_amp=8.0), 3) == 0.25
    # 已经触及
    assert ig.need_ratio_up(cur_high=108.0, target=107.0, day_open=100.0, pred_amp=8.0) == 0.0
    assert ig.need_ratio_up(107.0, 107.0, 100.0, 8.0) == 0.0
    assert ig.need_ratio_up(105.0, 107.0, 0.0, 8.0) is None

    tf_up = {"下跌": {"11:30": {"<0.15": {"n": 900, "p": 40.0},
                                "0.15~0.35": {"n": 1100, "p": 18.0},
                                "0.35~0.6": {"n": 1000, "p": 5.0},
                                ">0.6": {"n": 2000, "p": 0.5}}}}
    r = ig.remaining_touch_up("下跌", "11:30", cur_high=996.0, target=996.4,
                              day_open=940.0, pred_amp=7.88, table_f_up=tf_up)
    assert r["bucket"] == "<0.15" and r["p"] == 40.0
    r2 = ig.remaining_touch_up("下跌", "11:30", cur_high=996.0, target=990.0,
                               day_open=940.0, pred_amp=7.88, table_f_up=tf_up)
    assert r2["bucket"] == "已触及" and r2["p"] == 100.0
    assert ig.remaining_touch_up("下跌", "09:00", 996.0, 996.4, 940.0, 7.88, tf_up) is None


# ---- 向上档位扩到 +10%/+12%（2026-08-04）------------------------------------


def test_up_touch_levels_extended():
    """向上档位必须覆盖到 +12%。

    2026-08-04 11:19 暴露的缺口：国际复材当天已从今开涨 8.00%，
    而表 A 向上最高只到 +7%，五档全部「已触及」，给不出任何参考。
    向下保持 (1,2,3,5) 不动——止损位放到 −10% 没有实际意义。
    """
    assert ig.UP_TOUCH_LEVELS == (1, 2, 3, 5, 7, 10, 12)
    assert ig.TOUCH_LEVELS == (1, 2, 3, 5)      # 向下不变


def test_build_table_a_up_covers_extended_levels():
    """表 A 的 up 字典要含 10 和 12 两个新档，down 保持四档。"""
    # 每条给不同日期：表 A 的聚类稳健区间要按日期分组，日期是必需字段
    recs = [{"date": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
             "trend": "下跌", "amp_cell": ">8%", "o": 100.0, "h": 100.0 + i * 0.4,
             "l": 100.0 - i * 0.3, "c": 100.0}
            for i in range(200)]
    a = ig.build_table_a(recs, min_n=150)["下跌"][">8%"]
    assert sorted(a["up"], key=int) == ["1", "2", "3", "5", "7", "10", "12"]
    assert sorted(a["down"], key=int) == ["1", "2", "3", "5"]
    # i*0.4 > 12 需要 i>30，200 条里 169 条满足 → 84.5%
    assert abs(a["up"]["12"] - 84.5) < 0.6


# ---- 收盘位置（2026-08-04 新增描述性字段）------------------------------------


def test_close_position():
    """收盘位置 =（今收 − 当日最低）÷（当日最高 − 当日最低），0~1。

    1 = 收在当日最高，0 = 收在当日最低，0.5 = 正中间。
    算例（2026-08-03 中际旭创，30 分钟 K 线核实）：
      今开 891.00 收 902.50 最高 933.61 最低 875.87
      收盘位置 =(902.50 − 875.87) ÷ (933.61 − 875.87) = 26.63 ÷ 57.74 = 0.461
    """
    assert round(ig.close_position(902.50, 933.61, 875.87), 3) == 0.461
    assert ig.close_position(110.0, 110.0, 100.0) == 1.0     # 收在最高
    assert ig.close_position(100.0, 110.0, 100.0) == 0.0     # 收在最低
    assert ig.close_position(105.0, 110.0, 100.0) == 0.5
    # 一字板（最高=最低）无定义，返回 None 而不是 0 或 0.5
    assert ig.close_position(100.0, 100.0, 100.0) is None


def test_build_close_position_by_trend():
    """按趋势汇总收盘位置。

    实测（739 天）：下跌 0.430、震荡 0.470、上涨 0.513，单调递增。
    这是当日结束才知道的量，只能做事后复盘和预期设定，不能进挂单决策。
    """
    recs = ([{"trend": "下跌", "amp_cell": ">8%", "o": 100.0, "h": 110.0, "l": 100.0, "c": 102.0}] * 200
            + [{"trend": "上涨", "amp_cell": ">8%", "o": 100.0, "h": 110.0, "l": 100.0, "c": 108.0}] * 200)
    out = ig.build_close_position(recs, min_n=150)
    assert abs(out["下跌"]["mean"] - 0.20) < 1e-6
    assert abs(out["上涨"]["mean"] - 0.80) < 1e-6
    assert out["下跌"]["n"] == 200
    # 样本不足的趋势不出格
    assert ig.build_close_position(recs[:10], min_n=150) == {}


# ---- 板块共振维度（2026-08-04）---------------------------------------------


def test_resonance_count_and_bucket():
    """板块共振 = 当天有几只票落在同一个（趋势 × 预测振幅格）。

    2026-08-04 检验出来的真实条件变量。「下跌 × >8%」格里，触及 −3% 的概率：
      当天 1~3 只（孤立）  45.9%  聚类稳健区间 [34.4%, 57.4%]
      当天 4~7 只          36.8%  [23.3%, 50.8%]
      当天 8 只以上（共振） 77.8%  [66.4%, 87.5%]
    最低组上限 57.4% 低于最高组下限 66.4%，两区间不重叠，差异成立。
    """
    recs = ([{"code": f"A{i}", "date": "2026-08-04", "trend": "下跌", "amp_cell": ">8%"}
             for i in range(9)]
            + [{"code": "B1", "date": "2026-08-04", "trend": "上涨", "amp_cell": "4~6%"}]
            + [{"code": "C1", "date": "2026-08-03", "trend": "下跌", "amp_cell": ">8%"}])
    ig.attach_resonance(recs)
    assert recs[0]["reso_n"] == 9 and recs[0]["reso"] == "8只以上"
    assert recs[9]["reso_n"] == 1 and recs[9]["reso"] == "1~3只"
    assert recs[10]["reso_n"] == 1 and recs[10]["reso"] == "1~3只"
    assert ig.resonance_bucket(3) == "1~3只"
    assert ig.resonance_bucket(4) == "4~7只"
    assert ig.resonance_bucket(7) == "4~7只"
    assert ig.resonance_bucket(8) == "8只以上"


def test_build_table_a_reso():
    """表 A 的共振分档表：趋势 × 振幅格 × 共振档 → 触及概率。"""
    recs = []
    for d in range(60):                      # 60 个日期，每天 8 只票（共振档）
        for i in range(8):
            recs.append({"code": f"A{i}", "date": f"2026-0{d//30+1}-{d%30+1:02d}",
                         "trend": "下跌", "amp_cell": ">8%", "o": 100.0,
                         "h": 105.0, "l": 96.0 if i < 6 else 98.0, "c": 100.0})
    ig.attach_resonance(recs)
    out = ig.build_table_a_reso(recs, min_n=100)
    cell = out["下跌"][">8%"]["8只以上"]
    assert cell["n"] == 480
    assert abs(cell["down"]["3"] - 75.0) < 0.1     # 8 只里 6 只跌破 −3%
    assert cell["dates"] == 60


# ---- 聚类稳健置信区间（2026-08-04）-----------------------------------------


def test_cluster_bootstrap_ci_keeps_point_estimate():
    """聚类稳健区间：点估计仍按股票交易日，区间按日期整块重抽。

    2026-08-04 的教训：点估计和区间必须是同一个口径。
    此前我用「日期等权」算区间去比「股票交易日加权」的点估计，
    结果区间不包含点估计（[41.0%,57.4%] vs 63.8%），那是两个不同的量。

    正确做法保持点估计不变，只把「同一天内多只票相关」计入不确定性，
    实测表 A 各格区间宽度变成原来的 1.4~2.2 倍。
    """
    # 30 个日期，每天 10 只票；15 个日期全部命中，15 个日期全部不命中
    per_date = [(10, 10) if i < 15 else (0, 10) for i in range(30)]
    lo, hi, pt = ig.cluster_bootstrap_ci(per_date, b=400, seed=7)
    assert abs(pt - 50.0) < 1e-9              # 点估计 = 150/300
    # 同一天内完全相关 → 区间必须很宽（远宽于二项公式的 ±5.7%）
    assert lo < 35.0 and hi > 65.0
    # 反例：每天 10 只里恰好 5 只命中（天内无相关）→ 区间应很窄
    lo2, hi2, pt2 = ig.cluster_bootstrap_ci([(5, 10)] * 30, b=400, seed=7)
    assert abs(pt2 - 50.0) < 1e-9
    assert hi2 - lo2 < 1.0
    # 边界：空输入返回 None
    assert ig.cluster_bootstrap_ci([], b=10, seed=1) is None


def test_build_pool_cells_and_lookup():
    """全池格子表：日期 → {代码: [趋势, 振幅格]}，用来数当天的板块共振只数。

    关键性质：趋势用的是前一日收盘和前 60 日高点，预测振幅用的是前 10 日振幅，
    两者都只依赖【当天之前】的数据。所以某一天的格子在开盘前就已经确定，
    可以在 build 时离线算好，advise 不必再抓 38 只票。
    """
    recs = [{"date": "2026-08-04", "code": "sz300308", "trend": "下跌", "amp_cell": ">8%",
             "o": 1, "h": 1, "l": 1, "c": 1},
            {"date": "2026-08-04", "code": "sz301526", "trend": "下跌", "amp_cell": ">8%",
             "o": 1, "h": 1, "l": 1, "c": 1},
            {"date": "2026-08-04", "code": "sh688256", "trend": "上涨", "amp_cell": "4~6%",
             "o": 1, "h": 1, "l": 1, "c": 1},
            {"date": "2026-08-03", "code": "sz300308", "trend": "震荡", "amp_cell": "6~8%",
             "o": 1, "h": 1, "l": 1, "c": 1}]
    pc = ig.build_pool_cells(recs, keep_days=2)
    assert sorted(pc) == ["2026-08-03", "2026-08-04"]
    assert pc["2026-08-04"]["sz300308"] == ["下跌", ">8%"]
    # 数共振：08-04 有 2 只票同处「下跌 × >8%」
    assert ig.count_resonance(pc, "2026-08-04", "下跌", ">8%") == 2
    assert ig.count_resonance(pc, "2026-08-04", "上涨", "4~6%") == 1
    # 该日期不在表里 → None，不拿别的日期顶替
    assert ig.count_resonance(pc, "2026-08-05", "下跌", ">8%") is None
    # keep_days 只留最近 N 天
    assert sorted(ig.build_pool_cells(recs, keep_days=1)) == ["2026-08-04"]
