"""A 股自选盯盘工具 — 常驻置顶悬浮小窗，显示自选股名称与涨跌幅。

数据源：新浪 L1 行情（约 3 秒快照）。
运行：/usr/bin/python3 stock_watch.py
"""
import json
import os
import re
import urllib.request

SINA_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_watch.json")
REFRESH_MS = 3000  # 3 秒刷新，贴合新浪 L1 快照周期

_SINA_LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')


def normalize_code(raw):
    """股票代码加市场前缀。6 位 -> A 股(sh/sz/bj)，5 位 -> 港股(hk)。非法返回 None。"""
    code = raw.strip()
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


def fetch_quotes(codes):
    """批量拉取行情。codes 形如 ['sh600519', ...]。网络异常向上抛出，由界面层捕获。"""
    if not codes:
        return []
    req = urllib.request.Request(SINA_URL + ",".join(codes), headers=SINA_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as resp:
        text = resp.read().decode("gbk", errors="replace")
    return parse_sina_response(text)


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
    import threading
    import time
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    import trade_assist as ta

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
            self.t_sig = {}               # code -> 当前活动信号
            self.t_summary = {}           # code -> 已发日报日期
            self.t_ui = {}                # code -> {status,fill,skip} 该股信号栏控件

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

        # ---------------- 做 T 助手 ----------------

        def _build_trade_bar(self):
            if self.trade_bar:
                self.trade_bar.destroy()
            self.trade_bar = tk.Frame(self, bg=BG)
            self.trade_bar.pack(fill="x", padx=6, pady=(0, 4))
            self.t_status = tk.Label(self.trade_bar, bg=BG, fg=FLAT_COLOR,
                                     font=("Menlo", 10), anchor="w")
            self.t_status.pack(side="left")
            self.t_fill_btn = tk.Label(self.trade_bar, text="成交", bg=BG,
                                       fg=UP_COLOR, font=("Menlo", 10, "bold"),
                                       cursor="pointinghand")
            self.t_skip_btn = tk.Label(self.trade_bar, text="忽略", bg=BG,
                                       fg="#777", font=("Menlo", 10),
                                       cursor="pointinghand")
            self.t_fill_btn.bind("<Button-1>", lambda e: self._sig_fill())
            self.t_skip_btn.bind("<Button-1>", lambda e: self._sig_skip())
            self.t_status.config(text="T: 等待行情…")

        def _trade_setup(self):
            cur = self.book
            init = (f"{cur.base_shares} {cur.cash:.0f} {cur.t_pool:.0f}"
                    if cur else "300 200000 330000")
            raw = simpledialog.askstring(
                "做 T 设置", "底仓股数 可用资金 T资金池（输 0 0 0 停用）",
                initialvalue=init, parent=self)
            if not raw:
                return
            try:
                base, cash, pool = (float(x) for x in raw.split())
            except ValueError:
                messagebox.showwarning("格式错误", "示例：300 200000 330000")
                return
            if base <= 0:
                self.book = None
                if self.trade_bar:
                    self.trade_bar.destroy()
                    self.trade_bar = None
                if os.path.exists(ta.TRADE_CONFIG_PATH):
                    os.rename(ta.TRADE_CONFIG_PATH, ta.TRADE_CONFIG_PATH + ".off")
                return
            today = time.strftime("%Y-%m-%d")
            self.book = ta.TradeBook(stock="sz300308", base_shares=int(base),
                                     cash=cash, t_pool=pool, date=today)
            ta.save_book(self.book)
            self.engine = None
            self._build_trade_bar()

        def _trade_tick(self, q):
            """每次刷新喂入目标股行情。q 需含 current/prev_close/vol/amount。"""
            if not all(k in q for k in ("current", "prev_close", "vol", "amount")):
                return
            now = time.localtime()
            hhmm = time.strftime("%H%M", now)
            today = time.strftime("%Y-%m-%d", now)
            if self.book.date != today:
                self.book.rollover(today)
                ta.save_book(self.book)
                self.engine = None
            if self.engine is None:
                self.engine = ta.GridEngine(q["prev_close"], t_pool=self.book.t_pool)
                self.risk = ta.RiskGuard(q["prev_close"])
                if self.engine.qty == 0:
                    self.t_status.config(text="T: 资金池不足 1 手，已禁用", fg=UP_COLOR)
                    return
            px = q["current"]
            vwap = q["amount"] / q["vol"] if q["vol"] else px
            self.risk.update(ts=time.time(), px=px, vwap=vwap)
            # 触发检查（有活动信号或非交易时段则不出新信号）
            trading = "0930" <= hhmm <= "1500"
            if trading and self._sig is None and self._prev_px is not None:
                limit_hit = self.book.daily_limit_hit()
                sigs = self.engine.check(
                    self._prev_px, px, hhmm,
                    no_buy=self.risk.no_buy or self.risk.silence_all or limit_hit,
                    no_sell=self.risk.no_sell or self.risk.silence_all or limit_hit)
                if sigs:
                    self._sig_show(sigs[0])
            self._prev_px = px
            self._trade_status(px, hhmm)

        def _trade_status(self, px, hhmm):
            if self._sig:
                return  # 信号显示优先
            pnl = self.book.realized_pnl()
            flags = []
            if self.risk.no_sell:
                flags.append("涨势停卖")
            if self.risk.no_buy:
                flags.append("跌势停买")
            if self.book.daily_limit_hit():
                flags.append("日限额")
            lv = self.engine.levels
            n = self.engine.n_arm
            below = max((l for l in lv["buy"][:n] if l < px), default=None)
            above = min((l for l in lv["sell"][:n] if l > px), default=None)
            t = f"T: 盈亏{pnl:+.0f}"
            if below:
                t += f" ↓买档{(below / px - 1) * 100:+.1f}%"
            if above:
                t += f" ↑卖档{(above / px - 1) * 100:+.1f}%"
            if flags:
                t += " ⚠" + "/".join(flags)
            # 收盘日报（一次）
            today = time.strftime("%Y-%m-%d")
            if hhmm >= "1500" and self._summary_sent != today:
                self._summary_sent = today
                n_fill = len(self.book.fills)
                msg = (f":checkered_flag: 做T日报 {today}\n成交 {n_fill} 笔，"
                       f"已实现盈亏 *{pnl:+.0f}* 元\n"
                       f"底仓 {self.book.base_shares} 股，可用资金 {self.book.cash:.0f}")
                threading.Thread(target=_notify_slack, args=(msg,), daemon=True).start()
                t = f"T: 收盘 盈亏{pnl:+.0f} 已发日报"
            self.t_status.config(text=t, fg=UP_COLOR if pnl > 0
                                 else DOWN_COLOR if pnl < 0 else FLAT_COLOR)

        def _sig_show(self, sig):
            self._sig = sig
            color = DOWN_COLOR if sig.side == "B" else UP_COLOR
            arrow = "▼买" if sig.side == "B" else "▲卖"
            self.t_status.config(
                text=f"{arrow}{sig.qty}股 限价{sig.price:.2f}", fg=color)
            self.t_fill_btn.pack(side="left", padx=(8, 2))
            self.t_skip_btn.pack(side="left", padx=2)
            act = "买入" if sig.side == "B" else "卖出"
            text = f"{act} {sig.qty} 股 @ {sig.price:.2f}（网格触发）"
            threading.Thread(target=_notify_mac, args=("做T信号", text),
                             daemon=True).start()
            threading.Thread(target=_notify_slack,
                             args=(f":rotating_light: 做T信号：{text}",),
                             daemon=True).start()
            self.after(ta.SIGNAL_TTL * 1000, lambda s=sig: self._sig_expire(s))

        def _sig_hide(self):
            self._sig = None
            self.t_fill_btn.pack_forget()
            self.t_skip_btn.pack_forget()

        def _sig_expire(self, sig):
            if self._sig is sig:
                self.engine.expire(sig)
                self._sig_hide()

        def _sig_fill(self):
            sig = self._sig
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
            err = self.book.apply_fill(sig.side, qty, px,
                                       ts=time.strftime("%H:%M:%S"))
            if err:
                messagebox.showwarning("记账被拒", err)
                return
            ta.save_book(self.book)
            target = ta.pair_target(sig.side, px, self.engine.step)
            nxt = "目标卖出" if sig.side == "B" else "目标买回"
            threading.Thread(target=_notify_slack, args=(
                f"✅ 成交回报：{'买' if sig.side == 'B' else '卖'}{qty}股@{px:.2f}，"
                f"{nxt} {target:.2f}",), daemon=True).start()
            self._sig_hide()

        def _sig_skip(self):
            if self._sig:
                self._sig_hide()  # 档位保持已触发：该档今日不再提示

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
                cell = name_lbl.master
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
            self.status.pack(side="left", padx=(0, 10))
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
            plus.pack(side="left", padx=(10, 2))
            plus.bind("<Button-1>", lambda e: self._prompt_add())
            minus = tk.Label(self.body, text="−", bg=BG, fg=NAME_COLOR,
                             font=("Menlo", 15), cursor="pointinghand")
            minus.pack(side="left", padx=(2, 4))
            minus.bind("<Button-1>", lambda e: self._prompt_delete())
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
                    pct_lbl.config(text=f"{p:+.2f}%", fg=color)

        def refresh(self):
            try:
                codes = list(self.codes)
                if self.book and self.book.stock not in codes:
                    codes.append(self.book.stock)   # 做 T 标的不在自选也要拉行情
                quotes = fetch_quotes(codes)
                self.quotes = {q["code"]: q for q in quotes}
                self._set_status(f"● {time.strftime('%H:%M')}", DOWN_COLOR)
                if self.book and self.book.stock in self.quotes:
                    try:
                        self._trade_tick(self.quotes[self.book.stock])
                    except Exception:
                        self.t_status.config(text="T: 内部错误(见终端)", fg=UP_COLOR)
                        import traceback; traceback.print_exc()
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
