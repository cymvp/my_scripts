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
