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
CONFIG_PATH = os.path.expanduser("~/.stock_watch.json")
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
        if head in "69":
            return "sh" + code
        if head in "023":
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
        quotes.append({"code": code, "name": name, "change_pct": change_pct, "ok": True})
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


def _build_app():
    """构建并返回 tkinter 应用。延迟 import，使纯函数测试无需 Tk。"""
    import time
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    class StockWatch(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("")
            self.attributes("-topmost", True)
            self.attributes("-transparent", True)  # 透明背景，只剩文字
            self.configure(bg=BG)
            self.resizable(False, False)

            self.codes = load_config()
            self.quotes = {}          # code -> 最近一次有效行情
            self.rows = {}            # code -> (name_label, pct_label)
            self._drag_code = None    # 正在拖动的股票代码

            # 股票行容器（横向铺开，末尾带 + 按钮）
            self.body = tk.Frame(self, bg=BG)
            self.body.pack(fill="both", padx=6, pady=(6, 2))

            # 底部状态
            self.status = tk.Label(self, text="", bg=BG, fg=FLAT_COLOR,
                                   font=("Menlo", 9), anchor="w")
            self.status.pack(fill="x", padx=6, pady=(0, 4))

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

        def _on_remove(self, code):
            self.codes.remove(code)
            self.quotes.pop(code, None)
            save_config(self.codes)
            self._render_rows()

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
            for code in self.codes:
                cell = tk.Frame(self.body, bg=BG)
                cell.pack(side="left", padx=6, pady=1)
                name = tk.Label(cell, bg=BG, fg=NAME_COLOR, font=("Menlo", 12),
                                cursor="pointinghand")
                name.pack(side="left")
                # 拖动名称调整顺序
                name.bind("<ButtonPress-1>", lambda e, c=code: self._drag_start(c))
                name.bind("<ButtonRelease-1>", lambda e, c=code: self._drag_drop())
                pct = tk.Label(cell, bg=BG, fg=FLAT_COLOR,
                               font=("Menlo", 12, "bold"))
                pct.pack(side="left", padx=(3, 0))
                close = tk.Label(cell, text="✕", bg=BG, fg="#555",
                                 font=("Menlo", 8), cursor="pointinghand")
                close.pack(side="left", padx=(2, 0))
                close.bind("<Button-1>", lambda e, c=code: self._on_remove(c))
                self.rows[code] = (name, pct)
            # 末尾：+ 添加（用 Label 避免 macOS 按钮自带的方框）
            plus = tk.Label(self.body, text="+", bg=BG, fg=NAME_COLOR,
                            font=("Menlo", 15), cursor="pointinghand")
            plus.pack(side="left", padx=(8, 2))
            plus.bind("<Button-1>", lambda e: self._prompt_add())
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
                quotes = fetch_quotes(self.codes)
                self.quotes = {q["code"]: q for q in quotes}
                self.status.config(text=f"● {time.strftime('%H:%M:%S')} 已更新",
                                   fg=DOWN_COLOR)
            except Exception:
                # 网络/接口失败：保留上次价格，仅标记未更新
                self.status.config(text=f"⚠ {time.strftime('%H:%M:%S')} 未更新",
                                   fg=UP_COLOR)
            self._update_labels()
            self.after(REFRESH_MS, self.refresh)

    return StockWatch()


def main():
    _build_app().mainloop()


if __name__ == "__main__":
    main()
