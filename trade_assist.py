"""做 T 助手核心逻辑（纯逻辑，不含 GUI，可独立单测）。

设计见 docs/stock_watch/spec/2026-07-24-trade-assist-spec.md。
关键前提：A 股 T+1，做 T 依赖底仓；用户手动下单，本模块只产生信号与记账。
"""
import json
import os
import time

COMMISSION = 0.00025   # 佣金，双边各收
STAMP = 0.0005         # 印花税，卖出
DEFAULT_STEP = 0.006   # 回测选定：0.6% + 趋势停手（21天日均+1.26%/手）
N_LEVELS = 5
PAIR_STEPS = 2         # 配对目标 = 2 个档距
SIGNAL_TTL = 300       # 信号 5 分钟未回报则过期、档位重新武装
EXPIRE_COOLDOWN = 600  # 超时过期的档位冷却 10 分钟再武装（防价格贴档抖动反复刷同一信号）
CUTOFF_HHMM = "1445"   # 之后不开新 T

# A 股交易时段：上午 09:30-11:30、下午 13:00-15:00。
# 午间休市行情冻结，若不排除会用一个半小时前的陈旧价触发假信号（2026-07-29 实际发生过）。
SESSIONS = (("0930", "1130", "am"), ("1300", "1500", "pm"))


def session_of(hhmm):
    """返回 'am' / 'pm' / 'break'（午间休市）/ 'closed'（盘前盘后）。"""
    for start, end, name in SESSIONS:
        if start <= hhmm <= end:
            return name
    if SESSIONS[0][1] < hhmm < SESSIONS[1][0]:
        return "break"
    return "closed"


def in_trading_session(hhmm):
    """是否在可交易时段内（午间休市与盘前盘后均为 False）。"""
    return session_of(hhmm) in ("am", "pm")
DAILY_LIMIT_RATIO = 0.30
FUSE_RATIO = 0.02      # 日内最大亏损熔断线 = 资金池 × 2%（回测校准）
RECENTER_STEPS = 2     # 价格驱动中枢：价格偏离中枢达 N 档就把中枢移到现价（回测选 2 档）
TRADE_CONFIG_PATH = os.path.expanduser("~/.stock_watch_trade.json")


def grid_levels(prev_close, step=DEFAULT_STEP, n=N_LEVELS):
    """昨收中轴的对称网格：买档在下、卖档在上，各 n 档。"""
    return {
        "buy": [prev_close * (1 - step * k) for k in range(1, n + 1)],
        "sell": [prev_close * (1 + step * k) for k in range(1, n + 1)],
    }


def plan_levels(t_pool, price, n=N_LEVELS):
    """资金池能布几档、每档多少股（整百）。千元股 1 手≈10 万，资金不足即禁用。"""
    lots_total = int(t_pool / (price * 100))
    if lots_total <= 0:
        return 0, 0
    n_arm = min(n, lots_total)
    qty = lots_total // n_arm * 100
    return n_arm, qty


def pair_target(side, price, step=DEFAULT_STEP):
    """T 腿的配对目标价：买腿向上 2 档，卖腿向下 2 档。"""
    if side == "B":
        return price * (1 + PAIR_STEPS * step)
    return price * (1 - PAIR_STEPS * step)


class Signal:
    def __init__(self, side, level_idx, price, qty, ts=None):
        self.side = side          # "B" / "S"
        self.level_idx = level_idx
        self.price = price        # 建议限价 = 档位价
        self.qty = qty
        self.ts = ts if ts is not None else time.time()

    @property
    def key(self):
        return (self.side, self.level_idx)

    def __repr__(self):
        act = "买" if self.side == "B" else "卖"
        return f"{act}{self.qty}股@{self.price:.2f}"


class GridEngine:
    """网格触发引擎。喂入相邻两次快照价，产出触档信号。"""

    def __init__(self, prev_close, step=DEFAULT_STEP, n_levels=N_LEVELS,
                 t_pool=0.0, recenter_steps=RECENTER_STEPS):
        self.prev_close = prev_close
        self.step = step
        self.n_levels = n_levels
        self.recenter_steps = recenter_steps
        self.center = prev_close          # 网格中枢（价格驱动：偏离达阈值就移到现价）
        self.levels = grid_levels(prev_close, step, n_levels)
        self.n_arm, self.qty = plan_levels(t_pool, prev_close, n_levels)
        self.triggered = set()   # 已触发（今日不再触发，除非 expire）
        self.cooldown = {}       # key -> 冷却截止时间戳（超时过期的档位不立即武装）

    def _triggered_prices(self):
        """当前已触发档位对应的价格，供重锚时判定重叠。"""
        out = []
        for side, i in self.triggered:
            lvs = self.levels["buy" if side == "B" else "sell"]
            if i < len(lvs):
                out.append(lvs[i])
        return out

    def recenter(self, new_center):
        """把中枢移到 new_center：重算档位并清空已触发（新价格区间是新战场）。

        但与刚触发过的旧档位重叠的新档位要保留「已触发」标记——否则重锚后新买档
        可能正好落在刚成交的旧卖档上，产生卖了立刻买回、白付两笔手续费的信号。
        """
        old_prices = self._triggered_prices()
        self.center = new_center
        self.levels = grid_levels(new_center, self.step, self.n_levels)
        self.triggered = set()
        tol = self.step * new_center / 2      # 半档以内视为同一价位
        for side, key in (("buy", "B"), ("sell", "S")):
            for i, lv in enumerate(self.levels[side]):
                if any(abs(lv - p) <= tol for p in old_prices):
                    self.triggered.add((key, i))

    def roll(self, price):
        """价格驱动追踪：现价偏离中枢达 recenter_steps 档，就把中枢移到现价。
        阈值内不动，给价格在网格里震荡的空间（防止贴着价格抖动、无法触发）。"""
        if abs(price - self.center) >= self.recenter_steps * self.step * self.center:
            self.recenter(price)

    def restore(self, center, triggered):
        """重启后恢复当日盘中状态（中枢 + 已触发档），不重建。"""
        if center:
            self.center = center
            self.levels = grid_levels(center, self.step, self.n_levels)
        self.triggered = {tuple(t) for t in (triggered or [])}

    def check(self, prev_px, px, hhmm, no_buy=False, no_sell=False, ts=None,
              no_buy_above=None):
        """价格从 prev_px 走到 px：返回触发的信号列表（通常 0 或 1 个）。

        ts 为当前时间戳，用于跳过超时过期后仍在冷却期的档位；不传则不做冷却判断。

        no_buy_above 传当日最近一次卖出成交价：高于它的买档一律不武装。网格只有
        「中枢在哪」的空间记忆，没有「今天在什么价位卖过」的时间记忆，缺了这条约束
        会在趋势行情里卖出后又更高价买回（2026-07-30 实测四卖四买倒亏约 7.6 万）。
        """
        if hhmm > CUTOFF_HHMM or self.qty == 0 or not in_trading_session(hhmm):
            return []
        out = []
        for i, lv in enumerate(self.levels["buy"][: self.n_arm]):
            key = ("B", i)
            if key in self.triggered or no_buy or self._cooling(key, ts):
                continue
            if no_buy_above is not None and lv > no_buy_above:
                continue          # 高于最近卖出价，买回来必然是亏损的往返
            if prev_px > lv >= px:       # 下穿
                self.triggered.add(key)
                out.append(Signal("B", i, lv, self.qty))
        for i, lv in enumerate(self.levels["sell"][: self.n_arm]):
            key = ("S", i)
            if key in self.triggered or no_sell or self._cooling(key, ts):
                continue
            if prev_px < lv <= px:       # 上穿
                self.triggered.add(key)
                out.append(Signal("S", i, lv, self.qty))
        return out

    def _cooling(self, key, ts):
        return ts is not None and self.cooldown.get(key, 0) > ts

    def expire(self, signal, ts=None):
        """信号超时未成交：档位重新武装，但进入冷却期，避免贴档抖动反复刷同一信号。"""
        self.triggered.discard(signal.key)
        if ts is not None:
            self.cooldown[signal.key] = ts + EXPIRE_COOLDOWN


class RiskGuard:
    """趋势停手 + 涨跌停带静默。采样间隔 >=3 分钟，最近 3 点单调判趋势。"""

    SAMPLE_GAP = 180          # 秒
    DEVIATION = 0.015         # 价格偏离 VWAP 阈值
    LIMIT_BAND = 0.01         # 距涨跌停 1% 内全静默

    def __init__(self, prev_close, limit_ratio=0.20):
        self.prev_close = prev_close
        self.limit_up = prev_close * (1 + limit_ratio)
        self.limit_down = prev_close * (1 - limit_ratio)
        self.samples = []     # [(ts, vwap)]
        self.no_buy = False
        self.no_sell = False
        self.silence_all = False

    def update(self, ts, px, vwap):
        if not self.samples or ts - self.samples[-1][0] >= self.SAMPLE_GAP:
            self.samples.append((ts, vwap))
            self.samples = self.samples[-3:]
        self.silence_all = (px >= self.limit_up * (1 - self.LIMIT_BAND) or
                            px <= self.limit_down * (1 + self.LIMIT_BAND))
        self.no_buy = self.no_sell = False
        if len(self.samples) == 3 and vwap > 0:
            v1, v2, v3 = (s[1] for s in self.samples)
            rising, falling = v1 < v2 < v3, v1 > v2 > v3
            if px > vwap * (1 + self.DEVIATION) and rising:
                self.no_sell = True      # 上行趋势：不高卖踏空
            if px < vwap * (1 - self.DEVIATION) and falling:
                self.no_buy = True       # 下行趋势：不接飞刀


def _fee(side, qty, price):
    fee = qty * price * COMMISSION
    if side == "S":
        fee += qty * price * STAMP
    return fee


class TradeBook:
    """持仓与成交记账。T+1：当日买入不可卖。"""

    def __init__(self, stock, base_shares, cash, date, t_pool=None,
                 fills=None, history=None, grid_center=None,
                 triggered_levels=None):
        self.stock = stock
        self.base_shares = base_shares   # 今日开盘前的持仓（可卖）
        self.cash = cash
        # t_pool 默认等于 cash（合并语义：一笔钱既是买入上限也是网格布档规模）
        self.t_pool = cash if t_pool is None else t_pool
        self.date = date
        self.fills = fills or []         # 当日成交 [{ts,side,qty,price}]
        self.history = history or []     # 往日归档
        # 盘中网格状态（持久化以便重启恢复，不重建）
        self.grid_center = grid_center
        self.triggered_levels = triggered_levels or []

    # -- 当日聚合 --
    def _sold_today(self):
        return sum(f["qty"] for f in self.fills if f["side"] == "S")

    def _bought_today(self):
        return sum(f["qty"] for f in self.fills if f["side"] == "B")

    def sellable(self):
        """可卖 = 底仓 − 今日已卖（今日买入 T+1 不可卖）。"""
        return self.base_shares - self._sold_today()

    def daily_turnover(self):
        return self._sold_today() + self._bought_today()

    def daily_limit_hit(self):
        return self.daily_turnover() >= self.base_shares * DAILY_LIMIT_RATIO

    def apply_fill(self, side, qty, price, ts):
        """回报成交。违反约束返回错误字符串（不记账），成功返回 None。"""
        if side == "S" and qty > self.sellable():
            return f"拒绝：卖出 {qty} 超过当前可卖 {self.sellable()} 股(T+1)"
        if side == "B" and qty * price + _fee("B", qty, price) > self.cash:
            return f"拒绝：买入需 {qty * price:.0f} 元，超过可用资金 {self.cash:.0f}"
        fee = _fee(side, qty, price)
        if side == "B":
            self.cash -= qty * price + fee
        else:
            self.cash += qty * price - fee
        self.fills.append({"ts": ts, "side": side, "qty": qty, "price": price})
        return None

    def last_sell_price(self):
        """当日最近一次卖出成交价；没卖过返回 None。

        供 GridEngine.check 的 no_buy_above 用——禁止在高于它的价位买回。
        取「最近一次」而非「最低一次」：语义是买回刚卖掉的那批，与 FIFO 配对一致。
        """
        for f in reversed(self.fills):
            if f["side"] == "S":
                return f["price"]
        return None

    def realized_pnl(self):
        """当日已实现盈亏：已配对部分（先进先出配对买卖），含费用。"""
        buys = [[f["qty"], f["price"]] for f in self.fills if f["side"] == "B"]
        sells = [[f["qty"], f["price"]] for f in self.fills if f["side"] == "S"]
        pnl, bi, si = 0.0, 0, 0
        while bi < len(buys) and si < len(sells):
            q = min(buys[bi][0], sells[si][0])
            bp, sp = buys[bi][1], sells[si][1]
            pnl += (sp - bp) * q
            pnl -= (bp + sp) * q * COMMISSION + sp * q * STAMP
            buys[bi][0] -= q
            sells[si][0] -= q
            if buys[bi][0] == 0:
                bi += 1
            if sells[si][0] == 0:
                si += 1
        return pnl

    def floating_pnl(self, price):
        """未配对腿的浮动盈亏（FIFO 配对后剩余敞口，不计费用）。"""
        buys = [[f["qty"], f["price"]] for f in self.fills if f["side"] == "B"]
        sells = [[f["qty"], f["price"]] for f in self.fills if f["side"] == "S"]
        bi = si = 0
        while bi < len(buys) and si < len(sells):
            q = min(buys[bi][0], sells[si][0])
            buys[bi][0] -= q
            sells[si][0] -= q
            if buys[bi][0] == 0:
                bi += 1
            if sells[si][0] == 0:
                si += 1
        floating = 0.0
        for q, p in buys[bi:]:
            floating += (price - p) * q          # 未平买腿
        for q, p in sells[si:]:
            floating += (p - price) * q          # 未平卖腿
        return floating

    def fused(self, price, ratio=FUSE_RATIO):
        """当日总亏损（已实现+浮动）是否触发熔断线（资金池×ratio）。"""
        return self.realized_pnl() + self.floating_pnl(price) <= -ratio * self.t_pool

    def rollover(self, new_date):
        """跨日：当日买卖并入底仓，归档当日成交。"""
        self.base_shares += self._bought_today() - self._sold_today()
        if self.fills:
            self.history.append({"date": self.date, "fills": self.fills,
                                 "pnl": round(self.realized_pnl(), 2)})
        self.fills = []
        self.date = new_date
        self.grid_center = None          # 新交易日：网格状态清零
        self.triggered_levels = []


def save_book(book, path=None):
    path = path or TRADE_CONFIG_PATH
    data = {k: getattr(book, k) for k in
            ("stock", "base_shares", "cash", "t_pool", "date",
             "fills", "history")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)


def load_book(path=None):
    path = path or TRADE_CONFIG_PATH
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return TradeBook(**json.load(f))


_BOOK_FIELDS = ("stock", "base_shares", "cash", "t_pool", "date",
                "fills", "history", "grid_center", "triggered_levels")


def save_books(books, path=None):
    """多股：{code: TradeBook} 存为 {"books": {code: {...}}}。"""
    path = path or TRADE_CONFIG_PATH
    data = {"books": {code: {k: getattr(b, k) for k in _BOOK_FIELDS}
                      for code, b in books.items()}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)


def load_books(path=None):
    """返回 {code: TradeBook}。兼容旧的单 book 平铺格式（自动迁移）。"""
    path = path or TRADE_CONFIG_PATH
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "books" in data:
        return {code: TradeBook(**bd) for code, bd in data["books"].items()}
    if "stock" in data:                       # 旧格式：单 book 平铺
        return {data["stock"]: TradeBook(**data)}
    return {}
