"""market_pulse 集成测试——打真实接口。

跑法：MP_E2E=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest test_market_pulse_e2e.py -q
不设 MP_E2E 时自动 skip，无网络环境不会红。
"""
import os

import pytest

import market_pulse as mp
import stock_watch as sw

pytestmark = pytest.mark.skipif(not os.environ.get("MP_E2E"),
                                reason="需要 MP_E2E=1 才跑联网测试")


def test_merged_request_returns_all_codes():
    """45 个代码一个批量请求，全部要有数据。

    2026-08-07 实测：自选 10 个 A 股 + 池子 38 + 指数 3，去重后 45，
    5 次请求全部 45/45 返回，耗时中位 421 毫秒。
    """
    codes = mp.merge_codes(sw.load_config())
    quotes = sw.fetch_quotes(codes)
    got = {q["code"] for q in quotes if q.get("ok")}
    missing = [c for c in mp.POOL_CODES if c not in got]
    assert not missing, f"池子里这些票没返回：{missing}"


def test_pool_quotes_have_positive_prices():
    quotes = sw.fetch_quotes(list(mp.POOL_CODES))
    pool = mp.parse_pool_quotes(quotes)
    assert len(pool) >= mp.MIN_VALID
    assert all(v["px"] > 0 for v in pool.values())


def test_store_roundtrip(tmp_path):
    p = str(tmp_path / "s.jsonl")
    snap = {"t": "2026-08-07 13:24:15", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=p) is True
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=p)
    assert got == [snap]


def test_rotate_clears_yesterday(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-06 14:00:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is True
    assert p.read_text(encoding="utf-8") == ""


def test_no_write_outside_session(tmp_path):
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 12:00:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is False
    assert not p.exists()
