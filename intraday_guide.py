#!/usr/bin/env python3
"""盘中位置参考工具。

它回答的问题是「当前价格在历史同类日子里处于什么位置」，
**不回答「该买还是该卖」**——127~1000 个交易日的统计给不出择时信号，
硬给一个只会制造确定性的错觉。

三层结构（2026-08-01 定，2026-08-02 样本池换成 38 只 / 14 赛道）：
  基础层：38 只科技股合并样本建统计基准，样本量几百到几千，相对可靠。
  个股层：用该股自己的历史做对照，**样本少于 MIN_N 就明确标注不可用**。
          实测中际旭创 4 年 579 个交易日里「下跌趋势+高开>=6%」只出现 2 次，
          单只股票在细分条件下必然样本归零，所以个股层只能是参考不能是依据。
  实时层：抓当前价，报它在历史分布里的分位数。

以及一条硬性纪律：每次输出统计结论，必须同时检查「最近一个月是否偏离历史」。
2026-08-01 实测发现 2026H2 的当日开→收比前五个半年期整体低约 2 个百分点，
不检查就会拿历史均值给出系统性偏乐观的结论。

用法：
  python3 intraday_guide.py build            重建统计基准（慢，抓 38 只票）
  python3 intraday_guide.py brief sz300308   盘前/盘后：该股当前状态与历史分布
  python3 intraday_guide.py live sz300308    盘中：当前价在历史分布的分位
"""
import json
import os
import statistics as st
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE_DIR, "intraday_baseline.json")

POOL = {
    # 38 只 A 股科技股 / 14 个细分赛道（2026-08-02 定版，与 my_data/trading/回测依据.md 一致）
    # 成交额低于 10 亿的已剔除；长鑫科技 688825 上市不足 60 个交易日暂不纳入
    "sz300308": "中际旭创", "sz300502": "新易盛", "sz300394": "天孚通信",
    "sh603986": "兆易创新", "sz301308": "江波龙", "sh688525": "佰维存储",
    "sh688256": "寒武纪", "sh688041": "海光信息", "sh688521": "芯原股份",
    "sz002371": "北方华创", "sh688012": "中微公司", "sh688072": "拓荆科技",
    "sh688126": "沪硅产业", "sh688019": "安集科技", "sz300054": "鼎龙股份",
    "sh688981": "中芯国际", "sh688347": "华虹宏力", "sh688249": "晶合集成",
    "sh600584": "长电科技", "sz002156": "通富微电", "sz002185": "华天科技",
    "sz300661": "圣邦股份",
    "sz300782": "卓胜微", "sh603501": "豪威集团", "sh688008": "澜起科技",
    "sz002463": "沪电股份", "sh600183": "生益科技", "sz002916": "深南电路",
    "sh601138": "工业富联", "sz000977": "浪潮信息", "sh603019": "中科曙光",
    "sz301526": "国际复材", "sh603256": "宏和科技", "sz002080": "中材科技",
    "sh605376": "博迁新材", "sz300285": "国瓷材料",
    "sz002837": "英维克", "sz002851": "麦格米特",
}

SECTOR = {
    "sz300308": "光模块", "sz300502": "光模块", "sz300394": "光模块",
    "sh603986": "存储芯片", "sz301308": "存储芯片", "sh688525": "存储芯片",
    "sh688256": "AI算力芯片", "sh688041": "AI算力芯片", "sh688521": "AI算力芯片",
    "sz002371": "半导体设备", "sh688012": "半导体设备", "sh688072": "半导体设备",
    "sh688126": "半导体材料", "sh688019": "半导体材料", "sz300054": "半导体材料",
    "sh688981": "晶圆制造", "sh688347": "晶圆制造", "sh688249": "晶圆制造",
    "sh600584": "封装测试", "sz002156": "封装测试", "sz002185": "封装测试",
    "sz300661": "模拟/功率",
    "sz300782": "芯片设计", "sh603501": "芯片设计", "sh688008": "芯片设计",
    "sz002463": "PCB/覆铜板", "sh600183": "PCB/覆铜板", "sz002916": "PCB/覆铜板",
    "sh601138": "AI服务器", "sz000977": "AI服务器", "sh603019": "AI服务器",
    "sz301526": "电子布", "sh603256": "电子布", "sz002080": "电子布",
    "sh605376": "MLCC", "sz300285": "MLCC",
    "sz002837": "温控/电源", "sz002851": "温控/电源",
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
MIN_N = 20          # 少于这个样本数的格子不给结论
TRIM = 0.10         # 截尾比例


# ============ 纯函数（单元测试覆盖这一段） ============

def trim_mean(values, ratio=TRIM):
    """截尾均值：排序后去掉两端各 ratio 比例再取平均。

    比原始均值抗极端值，又比中位数多用了数据。样本太少时截尾会削光数据，
    退回普通均值。
    """
    if not values:
        return None
    s = sorted(values)
    k = int(len(s) * ratio)
    kept = s[k:len(s) - k] if len(s) - 2 * k >= 3 else s
    return st.mean(kept)


def classify_trend(drawdown_pct):
    """按「昨收距近60日最高收盘的回撤」判趋势。边界：<=-15 下跌，(-15,-5] 震荡。"""
    if drawdown_pct <= -15:
        return "下跌"
    if drawdown_pct <= -5:
        return "震荡"
    return "上涨"


def classify_gap(gap_pct):
    if gap_pct >= 3:
        return "高开>=3%"
    if gap_pct >= 0:
        return "高开0~3%"
    if gap_pct > -3:
        return "低开0~3%"
    return "低开>=3%"


def classify_volume(ratio):
    """今日成交量 ÷ 前20日均量。边界 0.85 归平量、1.6 归巨量。"""
    if ratio < 0.85:
        return "缩量"
    if ratio < 1.15:
        return "平量"
    if ratio < 1.6:
        return "放量"
    return "巨量"


def percentile_of(value, history):
    """value 在 history 里的分位（0-100）：有多少比例的历史值小于它。"""
    if not history:
        return None
    return round(sum(1 for x in history if x < value) / len(history) * 100)


def slot_of(hhmm):
    """把时刻映射到它所属的 30 分钟 K 线。休市时段返回 None，不猜。"""
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, IndexError):
        return None
    t = h * 60 + m
    for s in SLOTS:
        end = int(s[:2]) * 60 + int(s[3:5])
        start = end - 30
        if s == "13:30":
            start = 13 * 60          # 午后从 13:00 开始
        if start < t <= end or (t == 9 * 60 + 30 and s == "10:00"):
            return s
    if 9 * 60 + 30 <= t <= 9 * 60 + 30:
        return "10:00"
    return None


def recent_drift(history, recent, z_threshold=2.0):
    """最近样本的**均值**是否偏离历史**均值**。

    分母必须是均值的标准误（历史标准差 ÷ √最近样本数），不是单日观测值的标准差。
    个股单日涨跌本身就有好几个百分点的波动，拿它做分母永远报不出警——
    2026-08-01 实测中际旭创「下跌×低开>=3%」历史 +0.44%、最近 -4.40%，
    差了近 5 个百分点却被判为未偏离，就是这个错误造成的。

    最近样本 <3 个或历史 <10 个时返回 deviated=None（不下结论，
    而不是默认判成「没偏离」）。
    """
    n = len(recent)
    if n < 3 or len(history) < 10:
        return {"deviated": None, "hist_mean": None, "recent_mean": None,
                "z": None, "n_recent": n}
    hm, hs = st.mean(history), st.pstdev(history)
    rm = st.mean(recent)
    se = hs / (n ** 0.5) if hs else 0.0
    z = (rm - hm) / se if se else 0.0
    return {"deviated": abs(z) >= z_threshold, "hist_mean": hm,
            "recent_mean": rm, "z": z, "n_recent": n}


# ============ 取数 ============

def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=25).read()


def fetch_daily(code, n=1000):
    u = (f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
         f"param={code},day,,,{n},qfq")
    d = json.loads(_get(u))["data"][code]
    k = d.get("qfqday") or d.get("day")
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in k]


def fetch_m30(code):
    """30 分钟 K 线。新浪上限 1023 根，A股每日 8 根，约 128 个交易日。"""
    u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
         f"?symbol={code}&scale=30&datalen=1023")
    return json.loads(_get(u).decode("utf-8", "ignore"))


def fetch_realtime(code):
    """新浪实时行情。返回 (名称, 今开, 昨收, 现价, 最高, 最低, 成交量)。"""
    u = f"https://hq.sinajs.cn/list={code}"
    raw = _get(u, {"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
    p = raw.split('"')[1]
    if not p:
        return None
    f = p.split(",")
    return (f[0], float(f[1]), float(f[2]), float(f[3]),
            float(f[4]), float(f[5]), float(f[8]))


# ============ 建统计基准 ============

def collect(code):
    """把一只票的日线和 30 分钟线对齐，产出每个交易日的一条记录。"""
    daily = {r[0]: r[1:] for r in fetch_daily(code)}
    bars = fetch_m30(code)
    byday = {}
    for b in bars:
        byday.setdefault(b["day"][:10], []).append(b)
    dates = sorted(daily)
    out = []
    for i in range(60, len(dates)):
        d = dates[i]
        if d not in byday or len(byday[d]) != 8:
            continue
        o, cl, h, l, v = daily[d]
        pc = daily[dates[i - 1]][1]
        if not pc or not o:
            continue
        hi60 = max(daily[x][1] for x in dates[i - 60:i])
        av20 = st.mean([daily[x][4] for x in dates[i - 20:i]])
        bb = sorted(byday[d], key=lambda x: x["day"])
        out.append({
            "date": d,
            "trend": classify_trend((pc - hi60) / hi60 * 100),
            "gap": classify_gap((o - pc) / pc * 100),
            "gap_pct": (o - pc) / pc * 100,
            "vol": classify_volume(v / av20 if av20 else 1),
            "path": [(float(x["close"]) - o) / o * 100 for x in bb],
            "o2c": (cl - o) / o * 100,
        })
    return out


def build():
    """重建统计基准并缓存到 JSON。慢（20 次双接口取数）。"""
    allrec, per_stock = [], {}
    for code, name in POOL.items():
        try:
            rs = collect(code)
        except Exception as e:                       # 单只失败不影响整体
            print(f"  {name} 取数失败：{e}")
            continue
        for r in rs:
            r["code"] = code
        per_stock[code] = rs
        allrec += rs
        print(f"  {name:8s} {len(rs):4d} 天")
        time.sleep(0.2)
    data = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pool": POOL, "all": allrec,
            "per_stock": {k: v for k, v in per_stock.items()}}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"\n基准已保存：{CACHE}（合计 {len(allrec)} 个交易日）")
    return data


def load():
    if not os.path.exists(CACHE):
        sys.exit(f"统计基准不存在，先跑：python3 {sys.argv[0]} build")
    with open(CACHE, encoding="utf-8") as f:
        return json.load(f)


# ============ 统计与输出 ============

def cell(records, trend, gap):
    return [r for r in records if r["trend"] == trend and r["gap"] == gap]


def slot_stats(rows, j):
    v = [r["path"][j] for r in rows]
    if not v:
        return None
    return {"n": len(v), "median": st.median(v), "trim": trim_mean(v),
            "above": sum(1 for x in v if x > 0) / len(v) * 100, "raw": v}


def drift_report(data, trend, gap):
    """最近一个月 vs 历史：当日开→收 是否偏离。"""
    rows = cell(data["all"], trend, gap)
    if len(rows) < 15:
        return None
    rows = sorted(rows, key=lambda r: r["date"])
    dates = sorted({r["date"] for r in data["all"]})
    recent_dates = set(dates[-22:])          # 约一个月
    hist = [r["o2c"] for r in rows if r["date"] not in recent_dates]
    rec = [r["o2c"] for r in rows if r["date"] in recent_dates]
    return recent_drift(hist, rec)


def brief(code):
    data = load()
    name = POOL.get(code, code)
    rt = fetch_realtime(code)
    daily = fetch_daily(code, 200)
    pc = daily[-1][2]
    hi60 = max(x[2] for x in daily[-61:-1])
    dd = (pc - hi60) / hi60 * 100
    trend = classify_trend(dd)
    av20 = st.mean([x[5] for x in daily[-21:-1]])

    print(f"\n{'='*72}")
    print(f"{name}（{code}）盘中位置参考   基准建于 {data['built_at']}")
    print(f"{'='*72}")
    print(f"最新收盘 {pc:.2f}   近60日最高 {hi60:.2f}   回撤 {dd:+.2f}%   "
          f"趋势状态：{trend}")
    print(f"最新成交量 {daily[-1][5]:,.0f} 手，前20日均量 {av20:,.0f} 手，"
          f"量能 {daily[-1][5]/av20:.2f} 倍（{classify_volume(daily[-1][5]/av20)}）")
    if rt:
        print(f"实时：{rt[3]:.2f}（今开 {rt[1]:.2f} 昨收 {rt[2]:.2f}）")

    for gap in ["高开>=3%", "高开0~3%", "低开0~3%", "低开>=3%"]:
        pool_rows = cell(data["all"], trend, gap)
        own_rows = cell(data["per_stock"].get(code, []), trend, gap)
        print(f"\n—— {trend}趋势 × {gap} ——")
        print(f"   合并样本 {len(pool_rows)} 天 / {name}自己 {len(own_rows)} 天", end="")
        if len(own_rows) < MIN_N:
            print(f"（个股样本 <{MIN_N}，只能看合并样本）")
        else:
            print()
        if len(pool_rows) < MIN_N:
            print(f"   合并样本也不足 {MIN_N} 天，不给统计结论。")
            continue
        print(f"   {'':8s}" + "".join(f"{s:>9s}" for s in SLOTS))
        for lab, key in (("中位数", "median"), ("截尾均值", "trim")):
            print(f"   {lab:8s}" + "".join(
                f"{slot_stats(pool_rows,j)[key]:+8.2f}%" for j in range(8)))
        print(f"   {'高于开盘':8s}" + "".join(
            f"{slot_stats(pool_rows,j)['above']:7.0f}% " for j in range(8)))
        d = drift_report(data, trend, gap)
        if d and d["deviated"] is not None:
            flag = "⚠ 已偏离，下面的数字要打折看" if d["deviated"] else "未偏离"
            print(f"   最近一个月检查：{flag}（历史当日开→收 {d['hist_mean']:+.2f}%，"
                  f"最近 {d['recent_mean']:+.2f}%，n={d['n_recent']}，z={d['z']:+.2f}）")
        else:
            print("   最近一个月检查：样本不足，无法判断是否偏离")
    print(f"\n本工具只报位置，不给买卖建议。统计基于历史，环境切换后会失效。")


def live(code):
    data = load()
    name = POOL.get(code, code)
    rt = fetch_realtime(code)
    if not rt or not rt[1]:
        sys.exit("实时行情取不到或今日未开盘")
    _, o, pc, cur, _, _, _ = rt
    hhmm = time.strftime("%H:%M")
    slot = slot_of(hhmm)
    daily = fetch_daily(code, 200)
    hi60 = max(x[2] for x in daily[-61:-1])
    trend = classify_trend((pc - hi60) / hi60 * 100)
    gap = classify_gap((o - pc) / pc * 100)
    now = (cur - o) / o * 100

    print(f"\n{name} {hhmm}  现价 {cur:.2f}  今开 {o:.2f}  昨收 {pc:.2f}")
    print(f"开盘涨幅 {(o-pc)/pc*100:+.2f}%（{gap}）  趋势 {trend}")
    print(f"开盘买入至今 {now:+.2f}%")
    if slot is None:
        print("当前不在交易时段（或处于午间休市），不做对照。")
        return
    j = SLOTS.index(slot)
    rows = cell(data["all"], trend, gap)
    if len(rows) < MIN_N:
        print(f"该情形合并样本只有 {len(rows)} 天，不足 {MIN_N}，不给对照。")
        return
    s = slot_stats(rows, j)
    p = percentile_of(now, s["raw"])
    print(f"\n对照「{trend}趋势 × {gap}」的 {len(rows)} 个历史交易日，{slot} 这个时点：")
    print(f"  历史中位数 {s['median']:+.2f}%   截尾均值 {s['trim']:+.2f}%   "
          f"高于开盘的比例 {s['above']:.0f}%")
    print(f"  >>> 当前 {now:+.2f}% 位于历史分布的第 {p} 分位")
    d = drift_report(data, trend, gap)
    if d and d["deviated"]:
        print(f"  ⚠ 最近一个月已偏离历史（历史 {d['hist_mean']:+.2f}% vs "
              f"最近 {d['recent_mean']:+.2f}%），上面的分位参考价值下降")
    print("\n这是位置，不是信号。不构成买卖建议。")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd in ("brief", "live"):
        if len(sys.argv) < 3:
            sys.exit(f"用法：python3 {sys.argv[0]} {cmd} <代码，如 sz300308>")
        (brief if cmd == "brief" else live)(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
