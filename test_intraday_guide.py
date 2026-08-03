"""intraday_guide 纯函数的单元测试（不联网、不依赖行情）。"""
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


def _rec(trend, cell, o, h, l, c):
    return {"trend": trend, "amp_cell": cell, "o": o, "h": h, "l": l, "c": c}


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


def test_build_table_b_bought_high_and_false_stop():
    """表 B 扩展：以【成交价】为基准算「买高了」，以【开盘价】为基准算「止损虚惊」。

    两条触发 −3% 的记录（成交价 97）：
      收盘 96 → 低于成交价，买高了；也低于开盘价，不是虚惊
      收盘 101 → 高于成交价，没买高；也高于开盘价，是虚惊
    买高了 50%，虚惊 50%
    """
    recs = [_rec("下跌", "4~6%", 100, 100, 95, 96),
            _rec("下跌", "4~6%", 100, 102, 96, 101),
            _rec("下跌", "4~6%", 100, 101, 99, 100)]   # 未触发 −3%，不计入分母
    b = ig.build_table_b(recs, min_n=3)
    d3 = b["下跌"]["4~6%"]["down"]["3"]
    assert d3["triggered"] == 2
    assert d3["bought_high"] == 50.0
    assert d3["false_stop"] == 50.0


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
    """止损候选：每档给出被打掉概率、虚惊率、白挨刀占比、仓位上限。

    白挨刀占比 = 被打掉概率 × 虚惊率。下跌 6~8% 的 −3% 档：37.6% × 20.5% = 7.7%
    """
    ta = {"下跌": {"6~8%": {"n": 935, "down": {"3": 37.6, "5": 18.6}, "up": {}}}}
    tb = {"下跌": {"6~8%": {"down": {"3": {"false_stop": 20.5}, "5": {"false_stop": 7.5}},
                            "up": {}}}}
    opts = ig.stop_options("下跌", "6~8%", ta, tb, total=3_000_000, risk_pct=1.0)
    o3 = [o for o in opts if o["stop_pct"] == 3][0]
    assert o3["hit"] == 37.6
    assert o3["false_stop"] == 20.5
    assert round(o3["wasted"], 1) == 7.7
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
