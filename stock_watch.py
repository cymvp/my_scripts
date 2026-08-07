"""A 股自选盯盘工具 — 常驻置顶悬浮小窗，显示自选股名称与涨跌幅。

数据源：新浪 L1 行情（约 3 秒快照）。
运行：/usr/bin/python3 stock_watch.py
"""
import datetime
import json
import os
import re
import urllib.request
import zoneinfo

SINA_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_watch.json")
REFRESH_MS = 3000  # 3 秒刷新，贴合新浪 L1 快照周期

_SINA_LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')


def normalize_code(raw):
    """股票代码加市场前缀。

    6 位数字 -> A 股(sh/sz/bj)；5 位数字 -> 港股(hk)；纯字母 -> 美股(gb_ 小写)。
    非法返回 None。
    """
    code = raw.strip()
    if code.isalpha():                      # 美股按代号，如 MU / SNDK / SKHY
        return "gb_" + code.lower()
    if not code.isdigit():
        return None
    if len(code) == 5:
        return "hk" + code
    if len(code) == 6:
        head = code[0]
        if head in "695":  # 5 开头: 沪市基金/ETF
            return "sh" + code
        if head in "0231":  # 1 开头: 深市基金/ETF/LOF
            return "sz" + code
        if head in "48":
            return "bj" + code
    return None


def parse_sina_response(text):
    """新浪原始响应 -> [{code, name, change_pct, ok}]。

    ok=False 表示数据无效（停牌、未开盘、空响应、解析失败）。
    change_pct = (现价 - 昨收) / 昨收 * 100，保留两位小数。
    """
    quotes = []
    for code, payload in _SINA_LINE.findall(text):
        fields = payload.split(",")
        # 港股与 A 股字段位置不同：名称 / 昨收 / 现价
        if code.startswith("hk"):
            name_i, prev_i, cur_i = 1, 3, 6
        else:
            name_i, prev_i, cur_i = 0, 2, 3
        if len(fields) <= cur_i or not fields[name_i]:
            quotes.append({"code": code, "name": code, "change_pct": None, "ok": False})
            continue
        name = fields[name_i]
        try:
            prev_close = float(fields[prev_i])
            current = float(fields[cur_i])
        except ValueError:
            quotes.append({"code": code, "name": name, "change_pct": None, "ok": False})
            continue
        if prev_close == 0 or current == 0:
            quotes.append({"code": code, "name": name, "change_pct": None, "ok": False})
            continue
        change_pct = round((current - prev_close) / prev_close * 100, 2)
        q = {"code": code, "name": name, "change_pct": change_pct, "ok": True}
        # A 股快照附加字段（做 T 助手用）：现价/昨收/累计量额（HK 字段位不同，跳过）
        if not code.startswith("hk") and len(fields) > 9:
            try:
                q.update(current=current, prev_close=prev_close,
                         vol=float(fields[8]), amount=float(fields[9]))
            except ValueError:
                pass
        quotes.append(q)
    return quotes


_TENCENT_URL = "https://qt.gtimg.cn/q="
_TENCENT_LINE = re.compile(r'v_(hk\d+)="([^"]*)"')
_US_LINE = re.compile(r'hq_str_(gb_\w+)="([^"]*)"')
# 美股成交时间形如 "Jul 30 04:00PM EDT"，据此判断这笔价格属于哪个时段
_US_TIME = re.compile(r"(\d{1,2}):(\d{2})(AM|PM)")


def _us_session(stamp):
    """按**成交时间**（f[25]，不是 f[24] 查询时间）判断 f[1] 那个价格属于哪个时段。

    盘前 4:00-9:30 / 盘中 9:30-16:00 / 盘后 16:00-20:00，正好 16:00 记「收盘」。

    注意语义：这里判的是 f[1] 的归属，**不是"现在是不是盘前"**。收盘后到次日盘中
    之间 f[1] 一直是上一次收盘价（时间戳 04:00PM），所以会一直标「收盘」；此时真正
    的盘前实时价在 ext_price/ext_pct 里，两者分开显示。
    """
    m = _US_TIME.search(stamp or "")
    if not m:
        return "—"
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    t = h * 60 + mi
    if 4 * 60 <= t < 9 * 60 + 30:
        return "盘前"
    if 9 * 60 + 30 <= t < 16 * 60:
        return "盘中"
    if t == 16 * 60:
        return "收盘"
    if 16 * 60 < t <= 20 * 60:
        return "盘后"
    return "—"


def _us_ext_label(now=None):
    """当前美东时刻处在盘前/盘后时给出「前」/「后」标签，正常交易时段给空串。

    ext_pct 这个字段本身不区分盘前还是盘后（两个时段共用 f[21]/f[22]/f[23]），
    所以标签只能由当前时钟决定。正常时段返回空串，此时 f[1] 就是实时价，
    不需要另外显示 ext。
    """
    now = now or datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    t = now.hour * 60 + now.minute
    if 4 * 60 <= t < 9 * 60 + 30:
        return "前"
    if 16 * 60 <= t <= 20 * 60:
        return "后"
    return ""


def parse_sina_us_response(text):
    """新浪美股 gb_ 响应 -> [{code, name, change_pct, session, ok}]。

    字段（2026-07-31 逐字段实测）：
      f[0] 名称
      f[1] **上一个正常交易时段的收盘价**（不是盘前价！）
      f[2] 该收盘价对前一日的涨跌幅%
      f[21] **盘前/盘后实时价**   f[22] **盘前/盘后实时涨跌幅%**   f[23] 涨跌额
      f[24] 查询时间（跟着当前时刻走，不是成交时间）
      f[25] **成交时间**（判断时段要用这个）
      f[26] 上一交易日收盘价

    踩过的坑（务必别再犯）：
      1. 把 f[1]/f[2] 当盘前价——错。腾讯同源的时间戳明确写 2026-07-30 16:00:01，
         证明 874.66 是周四收盘价。盘前真实数据在 f[21]/f[22]。
      2. 把 f[24] 当成交时间——错，它是查询时间。用 f[24] 判时段会把周四收盘
         的数据标成「盘前」。
      3. f[21]/f[22]/f[23] 已验证是实时的：50 秒内两次取数三只全部跳动
         （美光 +4.33%→+4.46%、闪迪 +5.99%→+6.38%、海力士 +7.49%→+7.57%）。
    无盘前数据时 ext_pct 给 None，不拿收盘价冒充。
    """
    quotes = []
    for code, payload in _US_LINE.findall(text):
        f = payload.split(",")
        if len(f) <= 2 or not f[0]:
            quotes.append({"code": code, "name": code, "change_pct": None,
                           "session": "—", "ext_pct": None, "ok": False})
            continue
        try:
            pct = round(float(f[2]), 2)
        except ValueError:
            quotes.append({"code": code, "name": f[0], "change_pct": None,
                           "session": "—", "ext_pct": None, "ok": False})
            continue
        q = {"code": code, "name": f[0], "change_pct": pct, "ok": True,
             "ext_pct": None, "ext_price": None,
             "quote_time": f[25] if len(f) > 25 else "",
             "session": _us_session(f[25] if len(f) > 25 else "")}
        try:
            q["close"] = float(f[1])
            ext_pct = float(f[22])
            if ext_pct:                       # 0 表示当前无盘前/盘后成交
                q["ext_pct"] = round(ext_pct, 2)
                q["ext_price"] = float(f[21])
        except (IndexError, ValueError):
            pass                              # 字段不全：保留 ext_pct=None
        quotes.append(q)
    return quotes


def parse_tencent_hk_response(text):
    """腾讯港股响应 -> [{code, name, change_pct, ok}]。字段以 ~ 分隔。

    为什么港股不用新浪：新浪的港股行情实测延迟十几分钟（2026-07-31 中际旭创 H 股
    新浪报 1059、时间戳停在 10:22，腾讯同刻 1023，差 3.75 个百分点）。A 股侧新浪
    是准实时的，所以只把港股切到腾讯。
    腾讯字段：f[1]=名称 f[3]=现价 f[4]=昨收。
    """
    quotes = []
    for code, payload in _TENCENT_LINE.findall(text):
        f = payload.split("~")
        if len(f) <= 4 or not f[1]:
            quotes.append({"code": code, "name": code, "change_pct": None, "ok": False})
            continue
        try:
            current, prev_close = float(f[3]), float(f[4])
        except ValueError:
            quotes.append({"code": code, "name": f[1], "change_pct": None, "ok": False})
            continue
        if prev_close == 0 or current == 0:
            quotes.append({"code": code, "name": f[1], "change_pct": None, "ok": False})
            continue
        quotes.append({"code": code, "name": f[1], "ok": True,
                       "change_pct": round((current - prev_close) / prev_close * 100, 2)})
    return quotes


def _fetch(url, codes, headers=None):
    req = urllib.request.Request(url + ",".join(codes), headers=headers or {})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("gbk", errors="replace")


def fetch_quotes(codes):
    """批量拉取行情。codes 形如 ['sh600519', 'hk03308', 'gb_mu', ...]。

    三个市场三条路径：A 股走新浪、港股走腾讯（新浪港股延迟十几分钟，见
    parse_tencent_hk_response）、美股走新浪 gb_（盘前价直接可见，见
    parse_sina_us_response）。网络异常向上抛出，由界面层捕获。返回顺序与传入一致。
    """
    if not codes:
        return []
    hk = [c for c in codes if c.startswith("hk")]
    us = [c for c in codes if c.startswith("gb_")]
    cn = [c for c in codes if not c.startswith(("hk", "gb_"))]
    got = {}
    if cn:
        for q in parse_sina_response(_fetch(SINA_URL, cn, SINA_HEADERS)):
            got[q["code"]] = q
    if hk:
        for q in parse_tencent_hk_response(_fetch(_TENCENT_URL, hk)):
            got[q["code"]] = q
    if us:
        for q in parse_sina_us_response(_fetch(SINA_URL, us, SINA_HEADERS)):
            got[q["code"]] = q
    return [got.get(c, {"code": c, "name": c, "change_pct": None, "ok": False})
            for c in codes]


def load_config(path=CONFIG_PATH):
    """读自选代码列表，文件不存在返回 []。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(codes, path=CONFIG_PATH):
    """写回自选代码列表。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False)


# --- 界面 ---------------------------------------------------------------

UP_COLOR = "#d62828"     # 涨：红
DOWN_COLOR = "#2a9d3a"   # 跌：绿
FLAT_COLOR = "#888888"   # 平/无效：灰
NAME_COLOR = "#2e9bff"   # 股票名：蓝
BG = "systemTransparent" # 透明背景（macOS Tk）


TRADE_LOG = os.path.expanduser("~/projects/logs/trade_assist.log")


def _trade_log(line):
    """把做 T 的每 tick 决策与事件写日志，供 `tail -f` 监控程序是否正常。"""
    try:
        os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
        import datetime as _dt
        from zoneinfo import ZoneInfo as _Z
        ts = _dt.datetime.now(_Z("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S')
        with open(TRADE_LOG, "a") as f:
            f.write(f"{ts}(BJT) {line}\n")
    except Exception:
        pass


def _notify_mac(title, text):
    """macOS 弹窗通知 + 提示音（失败静默，不影响主流程）。"""
    import subprocess
    try:
        subprocess.Popen(["osascript", "-e",
                          f'display notification "{text}" with title "{title}"'])
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
    except Exception:
        pass


def _notify_slack(text):
    """Slack 推送：env SLACK_WEBHOOK_URL → ~/.slack_webhook_url。失败静默。"""
    import urllib.request
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if not hook:
        p = os.path.expanduser("~/.slack_webhook_url")
        if os.path.exists(p):
            hook = open(p).read().strip()
    if not hook:
        return
    try:
        body = json.dumps({"blocks": [{"type": "section",
                "text": {"type": "mrkdwn", "text": text}}]}).encode()
        req = urllib.request.Request(hook, data=body,
                headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _build_app():
    """构建并返回 tkinter 应用。延迟 import，使纯函数测试无需 Tk。"""
    import datetime
    import threading
    import time
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    from zoneinfo import ZoneInfo

    import trade_assist as ta

    import market_pulse as mp

    class StockWatch(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("")
            self.overrideredirect(True)            # 去掉原生标题栏
            self.attributes("-transparent", True)  # 透明背景，只剩文字
            self.attributes("-topmost", True)
            self.configure(bg=BG)
            self.resizable(False, False)
            self.geometry("+200+100")              # 无边框时给个初始位置，避免落在 0,0
            self.bind("<Escape>", lambda e: self.destroy())

            self.codes = load_config()
            self.quotes = {}          # code -> 最近一次有效行情
            self.rows = {}            # code -> (name_label, pct_label)
            self._drag_code = None    # 正在拖动的股票代码
            self._win_off = (0, 0)    # 拖动窗口时的指针偏移
            self._status_text = ""    # 最近一次状态文字
            self._status_color = FLAT_COLOR
            self.status = None        # 最左边的时间标签，在 _render_rows 中创建

            # 股票行容器（横向铺开，末尾带 + 按钮）
            self.body = tk.Frame(self, bg=BG)
            self.body.pack(fill="both", padx=6, pady=(6, 2))

            # 底部状态
            # 右键菜单（挂在最左边的时间上）
            self._menu = tk.Menu(self, tearoff=0)
            self._menu.add_command(label="做 T 设置", command=self._trade_setup)
            self._menu.add_command(label="退出", command=self.destroy)

            # --- 做 T 助手状态（每股独立，均按股票代码 code 索引）---
            self.books = ta.load_books()  # {code: TradeBook}
            self.t_engine = {}            # code -> GridEngine（首次拿到行情后构建）
            self.t_risk = {}              # code -> RiskGuard
            self.t_prevpx = {}            # code -> 上一次价格
            self.t_session = {}           # code -> 上次所处交易场次（识别下午开盘首个 tick）
            self.t_sig = {}               # code -> 当前活动信号
            self.t_summary = {}           # code -> 已发日报日期
            self.t_ui = {}                # code -> {status,fill,skip} 该股信号栏控件

            # --- 市场脉搏（见 docs/market_pulse/spec/）---
            self.pulse_text = None      # 横条末尾那个格子的标签
            self._pulse_strip = ""      # 最近一次算出来的单行文案
            mp.rotate_store(mp.now_bj().strftime("%Y-%m-%d"))

            self._render_rows()
            self.refresh()

        def _prompt_add(self):
            raw = simpledialog.askstring("添加自选", "输入股票代码（6 位）", parent=self)
            if not raw:
                return
            code = normalize_code(raw)
            if code is None:
                messagebox.showwarning("无效代码", f"'{raw.strip()}' 不是有效的 6 位股票代码")
                return
            if code in self.codes:
                messagebox.showinfo("已存在", "该股票已在自选中")
                return
            self.codes.append(code)
            save_config(self.codes)
            self._render_rows()
            self.refresh()

        def _prompt_delete(self):
            """弹出列表，勾选要删除的股票，确认后批量删除。"""
            if not self.codes:
                return
            win = tk.Toplevel(self)
            win.title("删除自选")
            win.attributes("-topmost", True)
            win.configure(padx=14, pady=12)
            tk.Label(win, text="勾选要删除的股票：").pack(anchor="w", pady=(0, 6))
            checks = {}
            for code in self.codes:
                name = self.quotes.get(code, {}).get("name", code)
                var = tk.BooleanVar()
                checks[code] = var
                tk.Checkbutton(win, text=f"{name}  ({code})", variable=var,
                               anchor="w").pack(fill="x")

            def do_delete():
                remove = [c for c, v in checks.items() if v.get()]
                for c in remove:
                    self.codes.remove(c)
                    self.quotes.pop(c, None)
                if remove:
                    save_config(self.codes)
                    self._render_rows()
                win.destroy()

            tk.Button(win, text="确认删除", command=do_delete).pack(pady=(10, 0))

        # ---------------- 做 T 助手（每股独立，方法均带 code 参数）----------------

        def _build_trade_ui(self, cell, code):
            """在某股票 cell 下方建该股的信号栏。"""
            bar = tk.Frame(cell, bg=BG)
            bar.pack(anchor="w")
            status = tk.Label(bar, bg=BG, fg=FLAT_COLOR, font=("Menlo", 9),
                              anchor="w")
            status.pack(side="left")
            fill = tk.Label(bar, text="成交", bg=BG, fg=UP_COLOR,
                            font=("Menlo", 9, "bold"), cursor="pointinghand")
            skip = tk.Label(bar, text="忽略", bg=BG, fg="#777", font=("Menlo", 9),
                            cursor="pointinghand")
            fill.bind("<Button-1>", lambda e, c=code: self._sig_fill(c))
            skip.bind("<Button-1>", lambda e, c=code: self._sig_skip(c))
            status.config(text="T:等待行情…")
            self.t_ui[code] = {"status": status, "fill": fill, "skip": skip}

        def _stock_menu(self, event, code):
            m = tk.Menu(self, tearoff=0)
            name = self.quotes.get(code, {}).get("name", code)
            if code in self.books:
                m.add_command(label=f"{name} 做T设置",
                              command=lambda: self._trade_setup(code))
                m.add_command(label=f"重置 {name} 网格",
                              command=lambda: self._trade_reset(code))
                m.add_command(label=f"关闭 {name} 做T",
                              command=lambda: self._trade_disable(code))
            else:
                m.add_command(label=f"启用 {name} 做T",
                              command=lambda: self._trade_setup(code))
            m.tk_popup(event.x_root, event.y_root)

        def _trade_setup(self, code):
            cur = self.books.get(code)
            name = self.quotes.get(code, {}).get("name", code)
            init = f"{cur.base_shares} {cur.t_pool:.0f}" if cur else "300 330000"
            raw = simpledialog.askstring(
                f"{name} 做 T 设置", "底仓股数 资金池（资金池同时是买入上限；输 0 停用）",
                initialvalue=init, parent=self)
            if not raw:
                return
            try:
                parts = [float(x) for x in raw.split()]
                base = parts[0]
                pool = parts[1] if len(parts) > 1 else parts[0]
            except (ValueError, IndexError):
                messagebox.showwarning("格式错误", "示例：300 330000")
                return
            if base <= 0:
                self._trade_disable(code)
                return
            today = time.strftime("%Y-%m-%d")
            self.books[code] = ta.TradeBook(stock=code, base_shares=int(base),
                                            cash=pool, date=today)  # t_pool 默认=cash
            ta.save_books(self.books)
            self.t_engine.pop(code, None)   # 重新按昨收构建
            self._render_rows()

        def _trade_reset(self, code):
            """主动重置：清空当日已触发档与中枢，网格按当前行情重新武装。"""
            if code not in self.books:
                return
            self.books[code].grid_center = None
            self.books[code].triggered_levels = []
            ta.save_books(self.books)
            for d in (self.t_engine, self.t_risk, self.t_prevpx, self.t_sig):
                d.pop(code, None)
            self._render_rows()

        def _trade_disable(self, code):
            self.books.pop(code, None)
            for d in (self.t_engine, self.t_risk, self.t_prevpx,
                      self.t_sig, self.t_summary):
                d.pop(code, None)
            ta.save_books(self.books)
            self._render_rows()

        def _trade_tick(self, code, q):
            """喂入某股行情。q 需含 current/prev_close/vol/amount。"""
            book = self.books.get(code)
            ui = self.t_ui.get(code)
            if not book or not ui or not all(
                    k in q for k in ("current", "prev_close", "vol", "amount")):
                return
            # A 股交易时段必须用北京时间判断；本机是 JST(比北京快1h)，
            # 直接用 time.strftime 会让 14:45 尾盘停单等逻辑整体早触发 1 小时
            bjt = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
            hhmm = bjt.strftime("%H%M")
            today = bjt.strftime("%Y-%m-%d")
            if book.date != today:
                book.rollover(today)
                ta.save_books(self.books)
                self.t_engine.pop(code, None)
            if code not in self.t_engine:
                self.t_engine[code] = ta.GridEngine(q["prev_close"], t_pool=book.t_pool)
                self.t_risk[code] = ta.RiskGuard(q["prev_close"])
                # 重启恢复：当日已存的网格状态直接接续，不重建
                if book.grid_center or book.triggered_levels:
                    self.t_engine[code].restore(book.grid_center, book.triggered_levels)
                if self.t_engine[code].qty == 0:
                    ui["status"].config(text="T:资金池不足1手", fg=UP_COLOR)
                    return
            eng, risk = self.t_engine[code], self.t_risk[code]
            px = q["current"]
            vwap = q["amount"] / q["vol"] if q["vol"] else px
            # 午间休市（11:30-13:00）行情冻结，喂入陈旧价会用一个半小时前的价格触发
            # 假信号，且下午开盘瞬间立刻反向（2026-07-29 实测买 912.74 / 卖 911.19）。
            # 休市期间只刷新状态显示，不动引擎、不更新 prevpx。
            sess = ta.session_of(hhmm)
            if sess == "break":
                self._trade_status(code, px, hhmm, book.fused(px))
                return
            # 下午开盘首个 tick：跨休市的价格跳变不是可交易的连续走势，
            # 只重锚中枢并重置 prevpx，本 tick 不触发信号。
            reopen = sess == "pm" and self.t_session.get(code) == "am"
            self.t_session[code] = sess
            eng.roll(px)                 # 价格驱动追踪：中枢随现价跳格移动
            # 网格状态有变则回存（重启可恢复）
            trig = sorted(list(t) for t in eng.triggered)
            if eng.center != book.grid_center or trig != book.triggered_levels:
                book.grid_center = eng.center
                book.triggered_levels = trig
                ta.save_books(self.books)
            risk.update(ts=time.time(), px=px, vwap=vwap)
            fused = book.fused(px)       # 日内最大亏损熔断
            if (not reopen and not fused and code not in self.t_sig
                    and code in self.t_prevpx):
                limit_hit = book.daily_limit_hit()
                sigs = eng.check(
                    self.t_prevpx[code], px, hhmm, ts=time.time(),
                    no_buy=risk.no_buy or risk.silence_all or limit_hit,
                    no_sell=risk.no_sell or risk.silence_all or limit_hit,
                    # 卖出后不许在更高价买回（网格缺时间记忆，见 GridEngine.check）
                    no_buy_above=book.last_sell_price())
                if sigs:
                    self._sig_show(code, sigs[0])
            self.t_prevpx[code] = px
            self._trade_status(code, px, hhmm, fused)

        def _trade_status(self, code, px, hhmm, fused=False):
            book, eng, risk = self.books[code], self.t_engine[code], self.t_risk[code]
            ui = self.t_ui[code]
            pnl = book.realized_pnl()
            flags = []
            if risk.no_sell:
                flags.append("停卖")
            if risk.no_buy:
                flags.append("停买")
            if book.daily_limit_hit():
                flags.append("限额")
            n = eng.n_arm
            below = max((l for l in eng.levels["buy"][:n] if l < px), default=None)
            above = min((l for l in eng.levels["sell"][:n] if l > px), default=None)
            t = f"T{pnl:+.0f}"
            if below:
                t += f" 买{(below / px - 1) * 100:+.1f}%"
            if above:
                t += f" 卖{(above / px - 1) * 100:+.1f}%"
            if flags:
                t += " ⚠" + "/".join(flags)
            # 每 tick 决策日志（监控用）：价格/中枢/最近档距/风控/是否挂单。
            # 必须在下面两处 early return 之前写——否则挂单期间和熔断期间日志空白，
            # 自检会把这段空白误判成「引擎停顿/崩溃」（2026-07-29 前连续三天误报）。
            _trade_log(
                f"{code} px={px:.2f} center={eng.center:.2f} "
                f"买档{below and f'{below:.2f}({(below/px-1)*100:+.2f}%)' or '-'} "
                f"卖档{above and f'{above:.2f}({(above/px-1)*100:+.2f}%)' or '-'} "
                f"pnl={pnl:+.0f} flags={'/'.join(flags) or '-'} "
                f"sig={'挂单中' if code in self.t_sig else '-'}")
            if code in self.t_sig:
                return                    # 信号显示优先，不覆盖状态栏
            if fused:
                ui["status"].config(
                    text=f"🔴熔断 T{pnl:+.0f} 建议平T腿控损", fg=UP_COLOR)
                return
            today = time.strftime("%Y-%m-%d")
            if hhmm >= "1500" and self.t_summary.get(code) != today:
                self.t_summary[code] = today
                name = self.quotes.get(code, {}).get("name", code)
                msg = (f":checkered_flag: {name} 做T日报 {today}\n"
                       f"成交 {len(book.fills)} 笔，已实现盈亏 *{pnl:+.0f}* 元\n"
                       f"底仓 {book.base_shares} 股，可用 {book.cash:.0f}")
                threading.Thread(target=_notify_slack, args=(msg,), daemon=True).start()
                t = f"收盘 T{pnl:+.0f}"
            ui["status"].config(text=t, fg=UP_COLOR if pnl > 0
                                else DOWN_COLOR if pnl < 0 else FLAT_COLOR)

        def _sig_show(self, code, sig):
            self.t_sig[code] = sig
            ui = self.t_ui[code]
            color = DOWN_COLOR if sig.side == "B" else UP_COLOR
            arrow = "▼买" if sig.side == "B" else "▲卖"
            ui["status"].config(text=f"{arrow}{sig.qty}@{sig.price:.2f}", fg=color)
            ui["fill"].pack(side="left", padx=(6, 2))
            ui["skip"].pack(side="left", padx=2)
            name = self.quotes.get(code, {}).get("name", code)
            act = "买入" if sig.side == "B" else "卖出"
            text = f"{name} {act} {sig.qty}股 @ {sig.price:.2f}"
            _trade_log(f"{code} ★SIGNAL {sig.side} {sig.qty}@{sig.price:.2f}")
            threading.Thread(target=_notify_mac, args=("做T信号", text),
                             daemon=True).start()
            threading.Thread(target=_notify_slack,
                             args=(f":rotating_light: 做T信号：{text}",),
                             daemon=True).start()
            self.after(ta.SIGNAL_TTL * 1000, lambda: self._sig_expire(code, sig))

        def _sig_hide(self, code):
            self.t_sig.pop(code, None)
            ui = self.t_ui.get(code)
            if ui:
                ui["fill"].pack_forget()
                ui["skip"].pack_forget()

        def _sig_expire(self, code, sig):
            if self.t_sig.get(code) is sig:
                self.t_engine[code].expire(sig, ts=time.time())
                self._sig_hide(code)
                _trade_log(f"{code} 信号超时过期(5分钟未处理)，"
                           f"档位冷却 {ta.EXPIRE_COOLDOWN // 60} 分钟后重新武装")

        def _sig_fill(self, code):
            sig = self.t_sig.get(code)
            if not sig:
                return
            raw = simpledialog.askstring(
                "成交回报", "实际成交：数量 价格",
                initialvalue=f"{sig.qty} {sig.price:.2f}", parent=self)
            if not raw:
                return
            try:
                qty_s, px_s = raw.split()
                qty, px = int(qty_s), float(px_s)
            except ValueError:
                messagebox.showwarning("格式错误", "示例：100 991.50")
                return
            err = self.books[code].apply_fill(sig.side, qty, px,
                                              ts=time.strftime("%H:%M:%S"))
            if err:
                messagebox.showwarning("记账被拒", err)
                _trade_log(f"{code} 成交回报被拒: {err}")
                return
            ta.save_books(self.books)
            _trade_log(f"{code} ✅成交 {sig.side} {qty}@{px:.2f} pnl={self.books[code].realized_pnl():+.0f}")
            target = ta.pair_target(sig.side, px, self.t_engine[code].step)
            nxt = "目标卖出" if sig.side == "B" else "目标买回"
            threading.Thread(target=_notify_slack, args=(
                f"✅ 成交：{'买' if sig.side == 'B' else '卖'}{qty}股@{px:.2f}，"
                f"{nxt} {target:.2f}",), daemon=True).start()
            self._sig_hide(code)

        def _sig_skip(self, code):
            self._sig_hide(code)  # 档位保持已触发：该档今日不再提示

        def _win_press(self, event):
            self._win_off = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())

        def _win_move(self, event):
            ox, oy = self._win_off
            self.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

        def _drag_start(self, code):
            self._drag_code = code

        def _drag_drop(self):
            """松手时按指针横坐标重排自选顺序。"""
            if self._drag_code is None:
                return
            self.body.update_idletasks()
            px = self.body.winfo_pointerx() - self.body.winfo_rootx()
            # 目标位置 = 中心点在指针左侧的其它股票方块数量
            target = 0
            for code, (name_lbl, _) in self.rows.items():
                if code == self._drag_code:
                    continue
                cell = name_lbl                      # 上溯到 body 的直接子级
                while cell.master is not None and cell.master is not self.body:
                    cell = cell.master
                if cell.winfo_x() + cell.winfo_width() / 2 < px:
                    target += 1
            codes = [c for c in self.codes if c != self._drag_code]
            codes.insert(target, self._drag_code)
            self._drag_code = None
            if codes != self.codes:
                self.codes = codes
                save_config(self.codes)
                self._render_rows()

        def _render_rows(self):
            """按 self.codes 同步行控件（增删时调用）。股票横向铺开成小方块。"""
            for child in self.body.winfo_children():
                child.destroy()
            self.rows = {}
            # 最左边：时间（兼作拖动手柄 + 右键退出）
            self.status = tk.Label(self.body, bg=BG, font=("Menlo", 11),
                                   cursor="fleur", text=self._status_text,
                                   fg=self._status_color)
            self.status.pack(side="left", padx=(0, 10), anchor="n")
            self.status.bind("<ButtonPress-1>", self._win_press)
            self.status.bind("<B1-Motion>", self._win_move)
            for btn in ("<Button-2>", "<Button-3>"):
                self.status.bind(btn, lambda e: self._menu.tk_popup(e.x_root, e.y_root))
            self.t_ui = {}
            for code in self.codes:
                cell = tk.Frame(self.body, bg=BG)
                cell.pack(side="left", padx=3, pady=1, anchor="n")
                top = tk.Frame(cell, bg=BG)
                top.pack(anchor="w")
                name = tk.Label(top, bg=BG, fg=NAME_COLOR, font=("Menlo", 12),
                                cursor="pointinghand")
                name.pack(side="left")
                # 拖动名称调整顺序；右键该股 -> 做 T 菜单
                name.bind("<ButtonPress-1>", lambda e, c=code: self._drag_start(c))
                name.bind("<ButtonRelease-1>", lambda e, c=code: self._drag_drop())
                for btn in ("<Button-2>", "<Button-3>"):
                    name.bind(btn, lambda e, c=code: self._stock_menu(e, c))
                pct = tk.Label(top, bg=BG, fg=FLAT_COLOR,
                               font=("Menlo", 12, "bold"))
                pct.pack(side="left", padx=(3, 0))
                self.rows[code] = (name, pct)
                # 该股启用了做 T：在其下方建信号栏
                if code in self.books:
                    self._build_trade_ui(cell, code)
            # 末尾：+ 添加 / − 删除（无边框 Label，避免 macOS 按钮方框）
            plus = tk.Label(self.body, text="+", bg=BG, fg=NAME_COLOR,
                            font=("Menlo", 15), cursor="pointinghand")
            plus.pack(side="left", padx=(10, 2), anchor="n")
            plus.bind("<Button-1>", lambda e: self._prompt_add())
            minus = tk.Label(self.body, text="−", bg=BG, fg=NAME_COLOR,
                             font=("Menlo", 15), cursor="pointinghand")
            minus.pack(side="left", padx=(2, 4), anchor="n")
            minus.bind("<Button-1>", lambda e: self._prompt_delete())
            # 横条末尾：市场脉搏单行文案（内容在 _pulse_tick 里更新）
            self.pulse_text = tk.Label(self.body, text=self._pulse_strip,
                                       bg=BG, fg=FLAT_COLOR, font=("Menlo", 11))
            self.pulse_text.pack(side="left", padx=(12, 4), anchor="n")
            self._update_labels()

        def _update_labels(self):
            """用当前 self.quotes 刷新各行文字与颜色。"""
            for code, (name_lbl, pct_lbl) in self.rows.items():
                q = self.quotes.get(code)
                if q is None:
                    name_lbl.config(text="··")
                    pct_lbl.config(text="…", fg=FLAT_COLOR)
                    continue
                name_lbl.config(text=q["name"][:2])
                if not q["ok"] or q["change_pct"] is None:
                    pct_lbl.config(text="—", fg=FLAT_COLOR)
                else:
                    p = q["change_pct"]
                    color = UP_COLOR if p > 0 else DOWN_COLOR if p < 0 else FLAT_COLOR
                    # 美股每个时段只看该时段自己的波动，始终一个数字：
                    # 盘前看盘前涨跌幅、盘中看盘中涨跌幅、盘后看盘后涨跌幅。
                    # 盘前/盘后时 f[1]/change_pct 是上一次收盘的既成事实、不再跳动，
                    # 真正实时的是 ext_pct（相对上一次收盘），所以那两个时段用它。
                    ext = q.get("ext_pct")
                    lab = _us_ext_label()
                    if ext is not None and lab:
                        p = ext
                        color = (UP_COLOR if p > 0 else
                                 DOWN_COLOR if p < 0 else FLAT_COLOR)
                    else:
                        lab = ""
                    pct_lbl.config(text=f"{lab}{p:+.2f}%", fg=color)

        def _pulse_tick(self, quotes):
            """把池子部分落盘并重算单行文案。任何异常都不能影响自选渲染。"""
            try:
                now = mp.now_bj()      # 必须用北京时间，本机是 JST 快 1 小时
                pool = mp.parse_pool_quotes(quotes)
                snap = {"t": now.strftime(mp.TS_FMT),
                        "r": {c: v["r"] for c, v in pool.items()
                              if c in mp.POOL_CODES},
                        "idx": {c: v["r"] for c, v in pool.items()
                                if c in mp.IDX_CODES}}
                mp.append_store(snap)
                store = mp.load_store(max(mp.WINDOWS) * 2, now=now)
                live_r = {c: v["r"] for c, v in pool.items()}
                state = mp.build_state(store, now, live_r=live_r)
                self._pulse_strip = mp.render_strip(state)
            except Exception as exc:          # 脉搏出错不能连累自选行情
                self._pulse_strip = "脉搏异常"
                _trade_log(f"pulse error: {exc}")
            if self.pulse_text is not None:
                self.pulse_text.config(text=self._pulse_strip)

        def refresh(self):
            try:
                quotes = fetch_quotes(mp.merge_codes(self.codes))
                self.quotes = {q["code"]: q for q in quotes}
                self._set_status(f"● {time.strftime('%H:%M')}", DOWN_COLOR)
                for code in list(self.books):
                    if code in self.quotes:
                        try:
                            self._trade_tick(code, self.quotes[code])
                        except Exception:
                            import traceback
                            _trade_log(f"{code} ⚠异常: {traceback.format_exc().splitlines()[-1]}")
                            traceback.print_exc()
                self._pulse_tick(quotes)
            except Exception:
                # 网络/接口失败：保留上次价格，仅标记未更新
                self._set_status(f"⚠ {time.strftime('%H:%M')}", UP_COLOR)
            self._update_labels()
            self.after(REFRESH_MS, self.refresh)

        def _set_status(self, text, color):
            self._status_text = text
            self._status_color = color
            if self.status is not None:
                self.status.config(text=text, fg=color)

    return StockWatch()


def main():
    _build_app().mainloop()


if __name__ == "__main__":
    main()
