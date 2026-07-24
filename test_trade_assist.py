"""trade_assist 单元测试（按 spec 测试用例表）。"""
import json

import trade_assist as ta


# --- 档位与建议量 --------------------------------------------------------

def test_grid_levels():
    g = ta.grid_levels(prev_close=1000.0, step=0.009, n=5)
    assert g["buy"][0] == 991.0
    assert abs(g["buy"][1] - 982.0) < 1e-9  # 1000*(1-0.018)
    assert abs(g["sell"][0] - 1009.0) < 1e-9
    assert len(g["buy"]) == len(g["sell"]) == 5


def test_plan_qty_one_lot_only():
    # 10 万资金池、991 元/股：只买得起 1 手 -> 布 1 档、每档 100 股
    n_arm, qty = ta.plan_levels(t_pool=100_000, price=991.0, n=5)
    assert (n_arm, qty) == (1, 100)


def test_plan_qty_full():
    # 55 万：5 手 -> 5 档、每档 100 股
    n_arm, qty = ta.plan_levels(t_pool=550_000, price=991.0, n=5)
    assert (n_arm, qty) == (5, 100)


def test_plan_qty_insufficient():
    # 不足 1 手 -> 禁用
    n_arm, qty = ta.plan_levels(t_pool=50_000, price=991.0, n=5)
    assert (n_arm, qty) == (0, 0)


# --- GridEngine 触发 -----------------------------------------------------

def make_engine(prev_close=1000.0, step=0.009, t_pool=550_000):
    return ta.GridEngine(prev_close=prev_close, step=step, n_levels=5,
                         t_pool=t_pool)


def test_cross_down_triggers_buy():
    e = make_engine()
    sigs = e.check(prev_px=992.0, px=990.5, hhmm="1000")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side == "B" and s.price == 991.0 and s.qty == 100


def test_same_level_not_retriggered():
    e = make_engine()
    e.check(prev_px=992.0, px=990.5, hhmm="1000")
    assert e.check(prev_px=992.0, px=990.5, hhmm="1001") == []


def test_expire_rearms_level():
    e = make_engine()
    sigs = e.check(prev_px=992.0, px=990.5, hhmm="1000")
    e.expire(sigs[0])
    sigs2 = e.check(prev_px=992.0, px=990.5, hhmm="1010")
    assert len(sigs2) == 1


def test_cross_up_triggers_sell():
    e = make_engine()
    sigs = e.check(prev_px=1008.0, px=1009.5, hhmm="1000")
    assert len(sigs) == 1 and sigs[0].side == "S" and abs(sigs[0].price - 1009.0) < 1e-9


def test_late_session_silent():
    e = make_engine()
    assert e.check(prev_px=992.0, px=990.5, hhmm="1446") == []


def test_pair_target():
    e = make_engine(step=0.009)
    assert abs(ta.pair_target("B", 991.0, 0.009) - 991.0 * 1.018) < 1e-6
    assert abs(ta.pair_target("S", 1009.0, 0.009) - 1009.0 * 0.982) < 1e-6


def test_risk_no_buy_blocks_buy_side():
    e = make_engine()
    assert e.check(prev_px=992.0, px=990.5, hhmm="1000", no_buy=True) == []
    # 卖侧不受影响
    sigs = e.check(prev_px=1008.0, px=1009.5, hhmm="1000", no_buy=True)
    assert len(sigs) == 1 and sigs[0].side == "S"


# --- RiskGuard -----------------------------------------------------------

def test_trend_up_pauses_sell():
    r = ta.RiskGuard(prev_close=1000.0)
    # 三个间隔>=3分钟的采样，VWAP 单调升，价格偏离 +1.6%
    for i, (ts, vwap) in enumerate([(0, 1000.0), (200, 1002.0), (400, 1004.0)]):
        r.update(ts=ts, px=vwap * 1.016, vwap=vwap)
    assert r.no_sell is True and r.no_buy is False


def test_trend_down_pauses_buy():
    r = ta.RiskGuard(prev_close=1000.0)
    for ts, vwap in [(0, 1000.0), (200, 998.0), (400, 996.0)]:
        r.update(ts=ts, px=vwap * 0.984, vwap=vwap)
    assert r.no_buy is True and r.no_sell is False


def test_no_trend_no_pause():
    r = ta.RiskGuard(prev_close=1000.0)
    for ts, vwap in [(0, 1000.0), (200, 1001.0), (400, 1000.5)]:
        r.update(ts=ts, px=vwap * 1.001, vwap=vwap)
    assert r.no_buy is False and r.no_sell is False


def test_limit_band_silences_all():
    # 创业板 ±20%：涨停 1200；价格 1189 距涨停 <1% -> 全静默
    r = ta.RiskGuard(prev_close=1000.0, limit_ratio=0.20)
    r.update(ts=0, px=1189.0, vwap=1100.0)
    assert r.silence_all is True


# --- TradeBook -----------------------------------------------------------

def make_book(base=1000, cash=500_000, t_pool=550_000):
    return ta.TradeBook(stock="sz300308", base_shares=base, cash=cash,
                        t_pool=t_pool, date="2026-07-24")


def test_fill_updates_cash_and_sellable():
    b = make_book()
    assert b.apply_fill("B", 100, 991.0, ts="10:00") is None
    # 现金扣减含佣金
    assert abs(b.cash - (500_000 - 100 * 991.0 - 100 * 991.0 * ta.COMMISSION)) < 0.01
    # T+1: 当日买入不可卖，可卖仍是底仓
    assert b.sellable() == 1000


def test_sell_over_sellable_rejected():
    b = make_book(base=300)
    err = b.apply_fill("S", 400, 1009.0, ts="10:00")
    assert err is not None and "可卖" in err


def test_buy_over_cash_rejected():
    b = make_book(cash=50_000)
    err = b.apply_fill("B", 100, 991.0, ts="10:00")
    assert err is not None and "资金" in err


def test_daily_turnover_limit():
    b = make_book(base=1000)  # 30% = 300 股
    assert b.apply_fill("S", 300, 1009.0, ts="10:00") is None
    assert b.daily_limit_hit() is True


def test_realized_pnl_with_fees():
    b = make_book()
    b.apply_fill("B", 300, 991.0, ts="10:00")
    b.apply_fill("S", 300, 1009.0, ts="11:00")
    gross = (1009.0 - 991.0) * 300
    fee = (991.0 + 1009.0) * 300 * ta.COMMISSION + 1009.0 * 300 * ta.STAMP
    assert abs(b.realized_pnl() - (gross - fee)) < 0.01


def test_rollover_resets_day_keeps_capital():
    b = make_book()
    b.apply_fill("B", 100, 991.0, ts="10:00")
    cash_after = b.cash
    b.rollover("2026-07-25")
    assert b.date == "2026-07-25"
    assert b.fills == [] and b.cash == cash_after
    # 昨日买入的 100 股今天进入底仓可卖
    assert b.base_shares == 1100 and b.sellable() == 1100


def test_t_pool_defaults_to_cash():
    # 合并语义：不传 t_pool 时默认等于 cash
    b = ta.TradeBook(stock="sz300308", base_shares=300, cash=330_000,
                     date="2026-07-24")
    assert b.t_pool == 330_000


def test_books_roundtrip(tmp_path):
    p = str(tmp_path / "trade.json")
    b1 = ta.TradeBook(stock="sz300308", base_shares=300, cash=330_000,
                      date="2026-07-24")
    b2 = ta.TradeBook(stock="sh688256", base_shares=200, cash=250_000,
                      date="2026-07-24")
    b1.apply_fill("B", 100, 991.0, ts="10:00")
    ta.save_books({"sz300308": b1, "sh688256": b2}, path=p)
    loaded = ta.load_books(path=p)
    assert set(loaded) == {"sz300308", "sh688256"}
    assert loaded["sz300308"].cash == b1.cash
    assert len(loaded["sz300308"].fills) == 1
    assert loaded["sh688256"].base_shares == 200
    import os, stat
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_load_books_missing_returns_empty(tmp_path):
    assert ta.load_books(path=str(tmp_path / "nope.json")) == {}


def test_load_books_migrates_old_single_format(tmp_path):
    # 旧格式（单 book 平铺）应被迁移为 {code: book}
    import json
    p = tmp_path / "trade.json"
    p.write_text(json.dumps({"stock": "sz300308", "base_shares": 300,
                             "cash": 330000, "t_pool": 330000,
                             "date": "2026-07-24", "fills": [], "history": []}))
    loaded = ta.load_books(path=str(p))
    assert set(loaded) == {"sz300308"} and loaded["sz300308"].base_shares == 300


def test_config_roundtrip(tmp_path):
    p = str(tmp_path / "trade.json")
    b = make_book()
    b.apply_fill("B", 100, 991.0, ts="10:00")
    ta.save_book(b, path=p)
    b2 = ta.load_book(path=p)
    assert b2.cash == b.cash and b2.base_shares == b.base_shares
    assert len(b2.fills) == 1 and b2.date == b.date
    # 权限 600
    import os, stat
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
