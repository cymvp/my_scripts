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


# --- 追踪网格（roll / recenter）------------------------------------------

def test_recenter_moves_levels_and_keeps_only_overlapping_triggered():
    """重锚重算档位；已触发标记只保留「与刚触发价位重叠的新档」，其余清空。

    2026-07-29 修正前的行为是无条件清空，导致重锚后新档落在刚成交的旧档价位上
    会立刻反向触发（卖了立刻买回，白付两笔手续费）。
    """
    e = make_engine(prev_close=1000.0, step=0.009)
    e.check(prev_px=992.0, px=990.5, hhmm="1000")   # 触发买1档（价位 991.0）
    assert ("B", 0) in e.triggered
    e.recenter(1010.0)                               # 中枢上移
    assert abs(e.levels["buy"][0] - 1010.0 * 0.991) < 1e-6
    # 新买2档 = 1010*0.982 = 991.82，与刚触发的 991.0 相差 0.82 < 半档(4.55)，保留标记
    assert ("B", 1) in e.triggered
    # 新买1档 1000.91 离 991.0 超过半档，应已清空
    assert ("B", 0) not in e.triggered


def test_roll_price_driven_threshold():
    # 价格驱动，默认 2 档阈值：step 0.9% → 阈值 1.8%（=18 元 @1000）
    e = ta.GridEngine(prev_close=1000.0, step=0.009, n_levels=5, t_pool=550_000,
                      recenter_steps=2)
    assert e.center == 1000.0
    e.roll(1010.0)                                   # 漂移 1.0% < 1.8%，不动
    assert e.center == 1000.0
    e.roll(1020.0)                                   # 漂移 2.0% >= 1.8%，中枢移到现价
    assert e.center == 1020.0


def test_roll_recenters_to_price_and_enables_buy():
    # 价格拉到 1020 触发重定心 → 中枢=1020，买1档≈1010.8，回调即可买
    e = ta.GridEngine(prev_close=1000.0, step=0.009, n_levels=5, t_pool=550_000,
                      recenter_steps=2)
    e.roll(1020.0)
    assert e.center == 1020.0
    sigs = e.check(prev_px=1012.0, px=1010.0, hhmm="1030")
    assert sigs and sigs[0].side == "B"


# --- 盘中状态持久化（重启恢复，不重建）----------------------------------

def test_engine_restore_center_and_triggered():
    e = make_engine(prev_close=1000.0, step=0.009)
    e.restore(center=1010.0, triggered=[["B", 0], ["S", 1]])
    assert e.center == 1010.0
    assert abs(e.levels["buy"][0] - 1010.0 * 0.991) < 1e-6
    assert ("B", 0) in e.triggered and ("S", 1) in e.triggered
    # 恢复后该档不再重复触发
    assert e.check(prev_px=1002.0, px=1000.0, hhmm="1030") == []


def test_book_persists_grid_state(tmp_path):
    p = str(tmp_path / "t.json")
    b = make_book()
    b.grid_center = 1060.2
    b.triggered_levels = [["B", 0], ["B", 1]]
    ta.save_books({"sz300308": b}, path=p)
    b2 = ta.load_books(path=p)["sz300308"]
    assert b2.grid_center == 1060.2
    assert b2.triggered_levels == [["B", 0], ["B", 1]]


def test_rollover_clears_grid_state():
    b = make_book()
    b.grid_center = 1060.2
    b.triggered_levels = [["B", 0]]
    b.rollover("2026-07-25")
    assert b.grid_center is None and b.triggered_levels == []


# --- 浮动盈亏 / 熔断 ------------------------------------------------------

def test_floating_pnl_open_buy_leg():
    b = make_book()
    b.apply_fill("B", 100, 1000.0, ts="10:00")       # 未配对买腿
    # 现价 990：浮亏 (990-1000)*100 = -1000（不计费，浮动口径）
    assert abs(b.floating_pnl(990.0) - (-1000.0)) < 1e-6


def test_floating_pnl_paired_is_zero():
    b = make_book()
    b.apply_fill("B", 100, 1000.0, ts="10:00")
    b.apply_fill("S", 100, 1018.0, ts="11:00")       # 已配对，无敞口
    assert abs(b.floating_pnl(990.0)) < 1e-6


def test_fuse_triggers_on_total_loss():
    b = make_book(t_pool=110_000)                    # 熔断线 = 2% = -2200
    b.apply_fill("B", 100, 1000.0, ts="10:00")
    # 现价 970：浮亏 -3000 < -2200 -> 熔断
    assert b.fused(970.0, ratio=0.02) is True
    # 现价 995：浮亏 -500 -> 不熔断
    assert b.fused(995.0, ratio=0.02) is False


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


# ---- 2026-07-29 自检发现的四个缺陷（先写失败测试，再修）----

def test_expire_then_same_level_has_cooldown():
    """同一档位超时后不应立即重新武装，否则价格在档位附近抖动会反复刷同一信号。
    真实案例 2026-07-29：14:24:09 出 B@946.01，14:29 超时，14:30:14 又出 B@946.01。"""
    eng = ta.GridEngine(1000.0, t_pool=500000.0)
    lv = eng.levels["buy"][0]
    sigs = eng.check(lv + 1, lv - 1, "1000", ts=1000.0)
    assert len(sigs) == 1
    eng.expire(sigs[0], ts=1300.0)          # 5 分钟后超时
    # 冷却期内再次下穿同一档，不应再出信号
    assert eng.check(lv + 1, lv - 1, "1000", ts=1310.0) == []
    # 冷却期过后可以恢复
    assert len(eng.check(lv + 1, lv - 1, "1000", ts=1300.0 + ta.EXPIRE_COOLDOWN + 1)) == 1


def test_recenter_skips_new_level_overlapping_old_triggered():
    """重锚后，与刚触发过的旧档位重叠的新档位应视为已触发——否则会出现
    「卖 936.38 → 重锚 → 立刻买 936.35」这种卖了又买回、必亏手续费的信号。
    真实案例 2026-07-28 10:27→10:28。"""
    eng = ta.GridEngine(930.80, t_pool=5000000.0)
    sell0 = eng.levels["sell"][0]
    sigs = eng.check(sell0 - 1, sell0 + 1, "1027")     # 上穿第一卖档
    assert len(sigs) == 1 and sigs[0].side == "S"
    eng.roll(sell0 * 1.0121)                           # 价格续涨触发重锚
    # 重锚后若有新买档落在刚成交的旧卖档附近，不得立刻反向触发
    for i, lv in enumerate(eng.levels["buy"][: eng.n_arm]):
        if abs(lv - sell0) <= eng.step * eng.center / 2:
            assert ("B", i) in eng.triggered, f"新买档{lv:.2f}与旧卖档{sell0:.2f}重叠却未标记已触发"


def test_lunch_break_is_not_trading():
    """A 股 11:30-13:00 午间休市，行情冻结，此时不得出信号。
    真实案例 2026-07-29：12:59:59 用冻结价出 B@912.74，13:00:15 立刻反向 S@911.19，
    两笔都做每股亏 1.55 元。"""
    assert ta.in_trading_session("0930") is True
    assert ta.in_trading_session("1129") is True
    assert ta.in_trading_session("1131") is False      # 休市
    assert ta.in_trading_session("1200") is False
    assert ta.in_trading_session("1259") is False
    assert ta.in_trading_session("1300") is True
    assert ta.in_trading_session("1459") is True
    assert ta.in_trading_session("1501") is False
    assert ta.in_trading_session("0925") is False      # 集合竞价未开盘


def test_session_of_distinguishes_morning_and_afternoon():
    """需要区分上午/下午场，才能在 13:00 首个 tick 只重锚、不触发跨休市的假信号。"""
    assert ta.session_of("1000") == "am"
    assert ta.session_of("1200") == "break"
    assert ta.session_of("1400") == "pm"
    assert ta.session_of("1530") == "closed"
