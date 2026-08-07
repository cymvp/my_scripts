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
