#!/usr/bin/env python3
"""板块普涨/普跌日记录器。

**它解决的是样本量问题，不是当天给建议。**

背景：2026-07-31 那天 38 只科技股里 37 只开盘跳空 >=5%，按「股票-交易日」统计
会算成 37 条样本，实际只有 1 次独立的板块级事件。要研究这类事件，统计单位必须是
「交易日」而不是「股票-交易日」。而按交易日算，2025-06-24~2026-07-31 的 270 个
交易日里，普涨日（开盘跳空>=+3% 的占比>=50%）只有 6 天、普跌日（<=-2% 的占比>=50%）
15 天。**样本太少，做不出统计结论。**

所以这个脚本每天收盘后记一行，让样本自己长起来。按当前频率，
攒到 50 个独立事件大概需要两到三年。

指标口径与 my_data/trading/回测依据.md 一致：
  开盘跳空   = (今开 - 昨收) / 昨收
  当日涨跌幅 = (今收 - 昨收) / 昨收
  开盘→收盘  = (今收 - 今开) / 今开
  收高概率   = 今收 > 今开 的比例（不是「收红」）

用法：
  python3 breadth_recorder.py            记录今天（收盘后跑）
  python3 breadth_recorder.py --show     打印已积累的记录
"""
import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "breadth_log.csv")

# 与 intraday_guide.POOL 同一批 38 只
try:
    from intraday_guide import POOL
except ImportError:                      # 独立运行时的退路
    POOL = {}

CSV_HEADER = ("date,n,up3_pct,dn2_pct,median_gap,median_chg,median_o2c,"
              "close_high_pct,event")

UP_GAP = 3.0        # 普涨判据：开盘跳空 >= +3%
DN_GAP = -2.0       # 普跌判据：开盘跳空 <= -2%
STRONG = 50.0       # 占比 >= 50% 记为强事件
WEAK = 30.0         # 占比 >= 30% 记为弱事件


# ============ 纯函数（单元测试覆盖这一段） ============

def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def breadth(quotes):
    """从当日报价算板块宽度指标。

    quotes 每项需含 prev(昨收)、open(今开)，可选 close(今收)。
    昨收或今开为 0 的（停牌、数据缺失）直接剔除，不能当 0% 跳空混进分母。
    """
    v = [q for q in quotes if q.get("prev") and q.get("open")]
    if not v:
        return None
    gaps = [(q["open"] - q["prev"]) / q["prev"] * 100 for q in v]
    out = {"n": len(v),
           "up3_pct": sum(1 for g in gaps if g >= UP_GAP) / len(v) * 100,
           "dn2_pct": sum(1 for g in gaps if g <= DN_GAP) / len(v) * 100,
           "median_gap": _median(gaps)}
    closed = [q for q in v if q.get("close")]
    if closed:
        out["median_chg"] = _median([(q["close"] - q["prev"]) / q["prev"] * 100
                                     for q in closed])
        o2c = [(q["close"] - q["open"]) / q["open"] * 100 for q in closed]
        out["median_o2c"] = _median(o2c)
        out["close_high_pct"] = sum(1 for x in o2c if x > 0) / len(o2c) * 100
    return out


def classify_event(up3_pct, dn2_pct):
    """判定当天是不是板块级事件。两边都超阈值时取占比更高的一边。"""
    side, pct = ("普涨", up3_pct) if up3_pct >= dn2_pct else ("普跌", dn2_pct)
    if pct >= STRONG:
        return f"{side}-强"
    if pct >= WEAK:
        return f"{side}-弱"
    return None


def format_row(date, b, event):
    def f(k):
        return f"{b[k]:.2f}" if k in b and b[k] is not None else ""
    return ",".join([date, str(b.get("n", "")), f("up3_pct"), f("dn2_pct"),
                     f("median_gap"), f("median_chg"), f("median_o2c"),
                     f("close_high_pct"), event or ""])


# ============ 取数与记录 ============

def fetch_quotes(codes):
    u = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(u, headers={
        "Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=25).read().decode("gbk", "ignore")
    out = []
    for line in raw.split("\n"):
        if '"' not in line:
            continue
        payload = line.split('"')[1]
        if not payload:
            continue
        f = payload.split(",")
        try:
            out.append({"code": line.split("_")[-1].split("=")[0],
                        "open": float(f[1]), "prev": float(f[2]),
                        "close": float(f[3]), "date": f[30] if len(f) > 30 else ""})
        except (ValueError, IndexError):
            continue
    return out


def record():
    if not POOL:
        sys.exit("样本池为空：intraday_guide.POOL 导入失败")
    q = fetch_quotes(list(POOL))
    b = breadth(q)
    if not b:
        sys.exit("没有取到有效报价，未记录")
    date = next((x["date"] for x in q if x.get("date")), time.strftime("%Y-%m-%d"))
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8") as fh:
            if any(ln.startswith(date + ",") for ln in fh):
                print(f"{date} 已记录过，跳过")
                return
    event = classify_event(b["up3_pct"], b["dn2_pct"])
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8") as fh:
        if new:
            fh.write(CSV_HEADER + "\n")
        fh.write(format_row(date, b, event) + "\n")
    print(f"{date}  覆盖 {b['n']} 只   高开>=3% 占比 {b['up3_pct']:.0f}%   "
          f"低开<=-2% 占比 {b['dn2_pct']:.0f}%")
    if "median_o2c" in b:
        print(f"  板块中位：开盘跳空 {b['median_gap']:+.2f}%  当日涨跌幅 {b['median_chg']:+.2f}%  "
              f"开盘→收盘 {b['median_o2c']:+.2f}%  收高概率 {b['close_high_pct']:.0f}%")
    print(f"  事件判定：{event or '非事件（两侧占比均未达 30%）'}")


def show():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"还没有记录：{CSV_PATH}")
    with open(CSV_PATH, encoding="utf-8") as fh:
        rows = [ln.rstrip("\n").split(",") for ln in fh]
    hdr, data = rows[0], rows[1:]
    print(f"{'日期':12s}{'覆盖':>5s}{'高开>=3%':>9s}{'低开<=-2%':>10s}"
          f"{'中位跳空':>10s}{'中位涨跌':>10s}{'中位开→收':>11s}{'收高概率':>9s}  事件")
    print("-" * 92)
    for r in data:
        if len(r) < 9:
            continue
        print(f"{r[0]:12s}{r[1]:>5s}{r[2]+'%':>9s}{r[3]+'%':>10s}"
              f"{r[4]+'%':>10s}{r[5]+'%':>10s}{r[6]+'%':>11s}{r[7]+'%':>9s}  {r[8]}")
    ev = [r for r in data if len(r) > 8 and r[8]]
    print(f"\n共 {len(data)} 天，其中事件 {len(ev)} 天")
    for tag in ("普涨-强", "普涨-弱", "普跌-强", "普跌-弱"):
        c = sum(1 for r in ev if r[8] == tag)
        if c:
            print(f"  {tag}: {c} 天")


if __name__ == "__main__":
    (show if "--show" in sys.argv else record)()
