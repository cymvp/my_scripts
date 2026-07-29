#!/usr/bin/env python3
"""板块多日趋势 — 涨跌幅 + 量能趋势，判断板块是真趋势还是一日游。

为什么用代表股篮子而不是板块指数接口：东方财富的板块 K 线与资金流接口在本机
不可达（实测空响应），新浪的板块接口只有当日快照、没有历史。腾讯个股 K 线可用，
所以按板块取 1-3 只代表股（大市值龙头），用前复权数据聚合出板块的多日走势。

量能趋势是核心判据：涨跌幅只说方向，量能才说明是不是主力资金在动。
  放量上涨 = 资金进场确认      缩量上涨 = 避险漂移/小资金，不构成趋势
  放量下跌 = 主力出货          缩量下跌 = 恐慌盘释放完毕

用法：
    python3 sector_trend.py              # 全部板块，按近3日涨跌排序
    python3 sector_trend.py 光模块 存储   # 只看指定板块
"""
import json
import sys
import urllib.request

# 代表股篮子：全部代码已实测有效（qt.gtimg.cn 能返回名称）
BASKETS = {
    "光模块": ["sz300308", "sz300502", "sz300394"],
    "存储芯片": ["sh603986", "sz301308", "sh688825"],
    "AI芯片": ["sh688256", "sh688008"],
    "军工": ["sh600760"],
    "钢铁": ["sh600019"],
    "煤炭": ["sh601088"],
    "石油": ["sh600938"],
    "有色黄金": ["sh601899", "sh600547"],
    "白酒": ["sh600519"],
    "食品": ["sh600887"],
    "家电": ["sz000333"],
    "建筑建材": ["sh600585", "sh601668"],
    "银行": ["sh601288"],
    "医药": ["sh600276"],
    "电力": ["sh600900"],
    "船舶": ["sh600150"],
    "券商": ["sh600030"],
    "房地产": ["sh600048"],
    "酒店旅游": ["sh600754"],
    "农业化肥": ["sz000792", "sz002714"],
}

KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
         "?param={code},day,,,12,qfq")


def fetch(code):
    """取前复权日 K，返回 [(日期, 收盘, 成交量), ...] 按时间正序。"""
    req = urllib.request.Request(KLINE.format(code=code),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    node = d["data"][code]
    rows = node.get("qfqday") or node.get("day") or []
    return [(x[0], float(x[2]), float(x[5])) for x in rows]


def pct(series, n):
    """近 n 个交易日累计涨跌幅（%）。"""
    if len(series) < n + 1:
        return None
    return (series[-1][1] - series[-1 - n][1]) / series[-1 - n][1] * 100


def vol_ratio(series):
    """近3日均量 / 前3日均量。>1 放量，<1 缩量。"""
    if len(series) < 6:
        return None
    recent = sum(v for _, _, v in series[-3:]) / 3
    prior = sum(v for _, _, v in series[-6:-3]) / 3
    return recent / prior if prior else None


def verdict(chg3, vr):
    """量价配合的定性判断。"""
    if chg3 is None or vr is None:
        return "数据不足"
    up, heavy = chg3 > 0, vr > 1.15
    light = vr < 0.85
    if up and heavy:
        return "放量上涨·资金进场确认"
    if up and light:
        return "缩量上涨·避险漂移,趋势未确认"
    if up:
        return "平量上涨·中性"
    if not up and heavy:
        return "放量下跌·主力出货"
    if not up and light:
        return "缩量下跌·恐慌盘趋于释放完"
    return "平量下跌·中性"


def main():
    want = sys.argv[1:]
    names = [n for n in BASKETS if not want or n in want]
    rows = []
    for name in names:
        series_list, failed = [], []
        for code in BASKETS[name]:
            try:
                s = fetch(code)
                if len(s) >= 6:
                    series_list.append(s)
                else:
                    failed.append(code)
            except Exception:
                failed.append(code)
        if not series_list:
            rows.append((name, None, None, None, None, "取数失败", failed))
            continue
        # 篮子内等权平均
        def avg(f):
            vals = [v for v in (f(s) for s in series_list) if v is not None]
            return sum(vals) / len(vals) if vals else None
        c1, c3, c5 = avg(lambda s: pct(s, 1)), avg(lambda s: pct(s, 3)), avg(lambda s: pct(s, 5))
        vr = avg(vol_ratio)
        rows.append((name, c1, c3, c5, vr, verdict(c3, vr), failed))

    rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    print("板块多日趋势（代表股篮子等权，前复权）")
    # 盘中运行时，K 线最后一根是当日实时数据，所以第一列是「当日至今」而非昨日
    print(f"{'板块':<10}{'当日':>8}{'近3日':>9}{'近5日':>9}{'量比3/3':>9}  判断")
    print("-" * 74)
    for name, c1, c3, c5, vr, vd, failed in rows:
        if c3 is None:
            print(f"{name:<10}{'—':>8}{'—':>9}{'—':>9}{'—':>9}  {vd} {failed}")
            continue
        mark = " ⚠部分取数失败" if failed else ""
        print(f"{name:<10}{c1:>+7.2f}%{c3:>+8.2f}%{c5:>+8.2f}%{vr:>8.2f}  {vd}{mark}")
    print("\n量比 = 近3日均量 ÷ 前3日均量；>1.15 放量，<0.85 缩量。")
    print("代表股篮子是近似，不等于板块指数；只用于看趋势方向和量能变化。")


if __name__ == "__main__":
    main()
