"""market_pulse 纯函数的单元测试（不联网、不读盘）。"""
import pytest

import market_pulse as mp


# --- 常量 -----------------------------------------------------------------

def test_pool_codes_has_38():
    """池子固定 38 只，与 intraday_guide.POOL 同源同序。"""
    import intraday_guide as ig
    assert len(mp.POOL_CODES) == 38
    assert list(mp.POOL_CODES) == list(ig.POOL)


def test_windows_are_15_60_300():
    assert mp.WINDOWS == (15, 60, 300)


# --- merge_codes 合并去重 --------------------------------------------------

def test_merge_codes_dedups_overlap():
    """自选里已有的池子票不能重复出现。

    实际场景：自选里有中际旭创和国际复材，它们同时也在 38 只池子里。
    """
    watch = ["sz300308", "hk03308", "sh601288"]
    out = mp.merge_codes(watch, pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "hk03308", "sh601288", "sz300502", "sz399006"]


def test_merge_codes_keeps_watch_order_first():
    """自选顺序必须原样保留在最前，界面按这个顺序渲染。"""
    watch = ["sh601288", "sz300308"]
    out = mp.merge_codes(watch, pool=("sz300308",), idx=())
    assert out[:2] == ["sh601288", "sz300308"]


def test_merge_codes_empty_watch():
    out = mp.merge_codes([], pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "sz300502", "sz399006"]


def test_merge_codes_real_sizes():
    """真实规模：自选 10 个 A 股 + 池子 38 + 指数 3，重叠 6 个，去重后 45。"""
    watch = ["sz300308", "sz301526", "sh688825", "sh601288", "sh588170",
             "sz002463", "sh688256", "sh603986", "sh688008", "sh513120"]
    out = mp.merge_codes(watch)
    assert len(out) == 45
    assert len(set(out)) == 45


# --- split_result 分拣 -----------------------------------------------------

def test_split_result_separates_watch_and_pool():
    """重叠的票要同时出现在两边，不是二选一。"""
    quotes = {"sz300308": {"r": 2.35}, "sh601288": {"r": -0.4},
              "sz300502": {"r": 5.96}, "sz399006": {"r": 1.75}}
    watch_part, pool_part = mp.split_result(
        quotes, watch=["sz300308", "sh601288"], pool=("sz300308", "sz300502"))
    assert set(watch_part) == {"sz300308", "sh601288"}
    assert set(pool_part) == {"sz300308", "sz300502"}
    assert watch_part["sz300308"] is pool_part["sz300308"]


def test_split_result_ignores_missing():
    """请求里有、返回里没有的代码，两边都不出现，不塞 None 占位。"""
    quotes = {"sz300308": {"r": 2.35}}
    watch_part, pool_part = mp.split_result(
        quotes, watch=["sz300308", "sh601288"], pool=("sz300308", "sz300502"))
    assert set(watch_part) == {"sz300308"}
    assert set(pool_part) == {"sz300308"}


# --- in_session 交易时段 ---------------------------------------------------

@pytest.mark.parametrize("hhmm,expected", [
    ("09:14", False),   # 竞价前
    ("09:20", False),   # 集合竞价——虚拟撮合价，混进速度序列会造假信号
    ("09:29", False),
    ("09:30", True),    # 开盘首笔
    ("10:30", True),
    ("11:29", True),
    ("11:30", True),    # 上午收盘那一笔算
    ("11:31", False),   # 午休
    ("12:00", False),
    ("12:59", False),
    ("13:00", True),    # 下午开盘
    ("14:57", True),
    ("15:00", True),    # 收盘那一笔算
    ("15:01", False),   # 盘后
    ("23:00", False),
])
def test_in_session(hhmm, expected):
    assert mp.in_session(hhmm) is expected


def test_in_session_accepts_seconds():
    """带秒的时间戳也要认，落盘里存的是 HH:MM:SS。"""
    assert mp.in_session("10:30:15") is True
    assert mp.in_session("12:00:01") is False


def test_in_session_rejects_garbage():
    """格式不对直接抛，不静默返回 False——那会让采集悄悄停掉。"""
    with pytest.raises(ValueError):
        mp.in_session("abc")


# --- speed 速度 ------------------------------------------------------------

def test_speed_normal():
    """13:24:00 价 979.00（r=+2.51%），13:24:15 价 977.00（r=+2.30%）。

    v = 2.30 − 2.51 = −0.21 百分点，读作「最近 15 秒涨跌幅掉了 0.21 个百分点」。
    """
    assert mp.speed(2.30, 2.51) == pytest.approx(-0.21)


def test_speed_zero_when_unchanged():
    assert mp.speed(2.30, 2.30) == 0.0


def test_speed_returns_none_when_past_missing():
    """缺历史返回 None，不返回 0——0 会被读成「没动」，那是完全不同的意思。"""
    assert mp.speed(2.30, None) is None


def test_speed_returns_none_when_now_missing():
    assert mp.speed(None, 2.51) is None


# --- aggregate 中位数聚合 --------------------------------------------------

def test_aggregate_odd_count():
    assert mp.aggregate([-0.5, -0.1, 2.0]) == (pytest.approx(-0.1), 3)


def test_aggregate_even_count():
    """偶数个取中间两个的均值。"""
    val, n = mp.aggregate([-0.4, -0.2, 0.1, 0.3])
    assert val == pytest.approx(-0.05)
    assert n == 4


def test_aggregate_drops_none():
    val, n = mp.aggregate([-0.5, None, -0.1])
    assert val == pytest.approx(-0.3)
    assert n == 2


def test_aggregate_all_none():
    assert mp.aggregate([None, None]) == (None, 0)


def test_aggregate_empty():
    assert mp.aggregate([]) == (None, 0)


def test_aggregate_uses_median_not_mean():
    """必须是中位数。

    2026-08-07 实测：PCB/覆铜板赛道中位 +7.62%、MLCC 只有 +0.39%，
    平均数会被少数暴涨票拉偏。
    """
    val, _ = mp.aggregate([0.1, 0.1, 0.1, 20.0])
    assert val == pytest.approx(0.1)      # 中位数 0.1；平均数会是 5.075
