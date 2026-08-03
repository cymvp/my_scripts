#!/usr/bin/env python3
"""日内分钟数据积累器。

**它解决的是数据源的历史深度限制，不是当天给建议。**

背景：分钟级数据的历史深度被接口卡死，2026-08-02 实测：
  新浪 30 分钟线  datalen 上限 1023 根 = 128 个交易日
  新浪 60 分钟线  datalen 上限 1023 根 = 256 个交易日
  东方财富分钟线  只保留 31 个交易日
而按趋势状态（下跌/震荡/上涨）拆分后，128 个交易日只剩几十天，任何细分都不够。

唯一能长期突破的办法是自己每天抓、慢慢存。一年后有 250 个交易日，两年 500 个。

每天收盘后抓一次 38 只票的 30 分钟 K 线，只追加当天新增的日期，不重复写。

**容错很好，不必担心漏跑**：新浪给的是最近 128 个交易日的**滚动窗口**，
加上按 (code, date) 去重后追加，所以漏跑的日子只要还在取数窗口内，下次运行自动补齐。
日常取 DAILY_DATALEN=240 根覆盖 30 个交易日，漏 29 天以内自愈；
漏更多跑 --backfill 能补到 128 天；只有连续漏超过 128 个交易日才会永久丢。

**真正的隐患是写错而不是漏抓**，见 should_write() 的说明。
数据存 intraday_bars.jsonl，一行一条 {code,date,bars:[{t,o,h,l,c,v}×8]}。

它同时是「板块普涨/普跌」的数据来源。2026-08-03 合并了原 breadth_recorder.py——
那个脚本每天单独记一行，而它记的七个字段全部能从本文件的 30 分钟数据精确重算出来
（实测 2026-07-31 那一行，7 个字段一个小数点都不差）。合并后立刻有 136 天历史可用，
不必再从头攒两三年。

用法：
  python3 intraday_collector.py            抓取并追加今天（收盘后跑）
  python3 intraday_collector.py --stat     打印已积累的覆盖情况
  python3 intraday_collector.py --breadth  算出全部历史的板块普涨/普跌
"""
import datetime
import json
import os
import statistics as st
import sys
import time
import urllib.request
import zoneinfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE_DIR, "intraday_bars.jsonl")

try:
    from intraday_guide import POOL
except ImportError:
    POOL = {}


MARKET_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
CLOSE_HHMM = (15, 5)      # 北京 15:00 收盘，留 5 分钟给数据源结算
DAILY_DATALEN = 240       # 30 个交易日（8 根/天）。自愈窗口 29 天，见 should_write 的说明


def should_write(date_str, now_bj=None):
    """这个日期的数据现在能不能写进仓库。

    **收盘前抓到的当天数据，末根 K（14:30-15:00）是残缺的。**
    而脚本唯一的校验是「len(bars) != 8 就跳过」——新浪在盘中就会建好第 8 根，
    只是收盘价还是当时的价，len==8 成立，残缺值就被写进去；又因为按 (code, date)
    去重，第二天再跑也不会覆盖，**错值永久留下且不报错**。

    比漏抓严重得多：漏抓能自动补（新浪给的是最近 128 个交易日的滚动窗口，
    日常取 DAILY_DATALEN 根就覆盖 30 个交易日），写错不能自动改。
    """
    now = now_bj or datetime.datetime.now(MARKET_TZ)
    today = now.date().isoformat()
    if date_str > today:            # 时钟异常
        return False
    if date_str < today:            # 往期数据早就定型
        return True
    return (now.hour, now.minute) >= CLOSE_HHMM


def fetch_m30(code, datalen=DAILY_DATALEN):
    """新浪 30 分钟 K 线。

    日常增量取 DAILY_DATALEN 根（30 个交易日）；首次回填用 1023 根，
    把接口能给的 128 个交易日一次性灌进来。
    """
    u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
         f"?symbol={code}&scale=30&datalen={datalen}")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=25).read()
    return json.loads(raw.decode("utf-8", "ignore"))


# --- 板块普涨/普跌（原 breadth_recorder，纯计算，不联网）--------------------

MAX_ABS_GAP = 25.0     # 跳空绝对值超过它就当除权，剔除
UP_GAP, DN_GAP = 3.0, -2.0
STRONG, WEAK = 80.0, 50.0


def _oc(rec):
    """从一条记录取 (今开, 今收) = (首根 K 的开, 末根 K 的收)。"""
    b = rec["bars"]
    return b[0]["o"], b[-1]["c"]


def breadth_event(up3_pct, dn2_pct):
    """事件标签。占比 >=80% 记「强」，>=50% 记普通，都不到留空。"""
    if up3_pct >= STRONG:
        return "普涨-强"
    if up3_pct >= WEAK:
        return "普涨"
    if dn2_pct >= STRONG:
        return "普跌-强"
    if dn2_pct >= WEAK:
        return "普跌"
    return ""


def breadth_of_day(cur, prev):
    """算某一天的板块宽度。cur/prev 是 {code: 记录}，prev 提供昨收。

    没有前一交易日就算不出跳空，返回 None——不拿今开当昨收顶替。
    """
    gaps, chgs, o2cs, high, dropped = [], [], [], 0, 0
    for code, rec in cur.items():
        if code not in prev:
            continue
        pc = prev[code]["bars"][-1]["c"]
        o, c = _oc(rec)
        if not pc or not o:
            continue
        gap = (o - pc) / pc * 100
        if abs(gap) > MAX_ABS_GAP:      # 除权日，不复权价算出的假跳空
            dropped += 1
            continue
        gaps.append(gap)
        chgs.append((c - pc) / pc * 100)
        o2cs.append((c - o) / o * 100)
        high += c > o
    if not gaps:
        return None
    n = len(gaps)
    up3 = sum(1 for g in gaps if g >= UP_GAP) / n * 100
    dn2 = sum(1 for g in gaps if g <= DN_GAP) / n * 100
    return {"n": n, "dropped": dropped, "up3_pct": up3, "dn2_pct": dn2,
            "median_gap": st.median(gaps), "median_chg": st.median(chgs),
            "median_o2c": st.median(o2cs), "close_high_pct": high / n * 100,
            "event": breadth_event(up3, dn2)}


def breadth_all(path=None):
    """把已积累的全部数据算成逐日的板块宽度。返回 [(日期, 结果), ...]。"""
    byday = {}
    with open(path or STORE, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if len(r.get("bars") or []) == 8:
                byday.setdefault(r["date"], {})[r["code"]] = r
    days = sorted(byday)
    out = []
    for i in range(1, len(days)):
        r = breadth_of_day(byday[days[i]], byday[days[i - 1]])
        if r:
            out.append((days[i], r))
    return out


def show_breadth():
    rows = breadth_all()
    print(f"{'日期':<12}{'n':>4}{'跳空>=3%':>10}{'跳空<=-2%':>11}{'跳空中位':>10}"
          f"{'涨跌中位':>10}{'开→收中位':>11}{'收高率':>8}  事件")
    for d, r in rows:
        print(f"{d:<12}{r['n']:>4}{r['up3_pct']:>9.2f}%{r['dn2_pct']:>10.2f}%"
              f"{r['median_gap']:>9.2f}%{r['median_chg']:>9.2f}%{r['median_o2c']:>10.2f}%"
              f"{r['close_high_pct']:>7.2f}%  {r['event']}")
    ev = [(d, r) for d, r in rows if r["event"]]
    print(f"\n共 {len(rows)} 个交易日，其中普涨/普跌事件 {len(ev)} 天：")
    for d, r in ev:
        print(f"  {d}  {r['event']}（跳空中位 {r['median_gap']:+.2f}%，"
              f"开→收中位 {r['median_o2c']:+.2f}%，收高率 {r['close_high_pct']:.0f}%）")


def load_existing():
    """返回已存在的 (code, date) 集合，用于去重。"""
    seen = set()
    if not os.path.exists(STORE):
        return seen
    with open(STORE, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
                seen.add((r["code"], r["date"]))
            except (ValueError, KeyError):
                continue
    return seen


def collect(datalen=DAILY_DATALEN):
    if not POOL:
        sys.exit("样本池为空：intraday_guide.POOL 导入失败")
    seen = load_existing()
    added = 0
    failed = []
    skipped_open = set()
    with open(STORE, "a", encoding="utf-8") as fh:
        for code, name in POOL.items():
            try:
                bars = fetch_m30(code, datalen)
            except Exception as e:
                failed.append(f"{name}({str(e)[:20]})")
                continue
            byday = {}
            for b in bars:
                byday.setdefault(b["day"][:10], []).append(b)
            for d, bb in byday.items():
                if len(bb) != 8 or (code, d) in seen:
                    continue
                if not should_write(d):
                    skipped_open.add(d)
                    continue
                bb = sorted(bb, key=lambda x: x["day"])
                try:
                    rec = {"code": code, "name": name, "date": d,
                           "bars": [{"t": x["day"][11:16], "o": float(x["open"]),
                                     "h": float(x["high"]), "l": float(x["low"]),
                                     "c": float(x["close"]), "v": float(x["volume"])}
                                    for x in bb]}
                except (ValueError, KeyError):
                    continue
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seen.add((code, d))
                added += 1
            time.sleep(0.15)
    print(f"新增 {added} 条（股票-交易日）")
    if skipped_open:
        print(f"跳过未收盘的日期：{'、'.join(sorted(skipped_open))}"
              f"（北京时间 {CLOSE_HHMM[0]}:{CLOSE_HHMM[1]:02d} 之后才写，"
              f"避免把残缺的末根 K 存进去；下次运行会自动补）")
    if failed:
        print(f"取数失败：{', '.join(failed)}")
    stat(brief=True)


def stat(brief=False):
    if not os.path.exists(STORE):
        sys.exit(f"还没有数据：{STORE}")
    codes, n = set(), 0
    byday = {}
    with open(STORE, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            codes.add(r["code"])
            byday.setdefault(r["date"], set()).add(r["code"])
            n += 1
    ds = sorted(byday)
    size = os.path.getsize(STORE) / 1024 / 1024
    full = [d for d in ds if len(byday[d]) >= len(POOL)]
    part = [(d, len(byday[d])) for d in ds if len(byday[d]) < len(POOL)]
    print(f"已积累：{len(codes)} 只票 × {len(ds)} 个交易日 = {n} 条，{size:.1f} MB")
    print(f"  区间 {ds[0]} ~ {ds[-1]}")
    print(f"  **{len(POOL)} 只全齐的交易日：{len(full)} 天**"
          + (f"（{full[0]} ~ {full[-1]}）" if full else ""))
    if part and not brief:
        print(f"  不齐的 {len(part)} 天（回填时各票的接口回溯深度不同，或当天有停牌）：")
        for d, c in part[:6]:
            print(f"    {d}: {c} 只")
        if len(part) > 6:
            print(f"    …… 其余 {len(part)-6} 天")
    if not brief:
        print(f"  参考：新浪 30 分钟接口一次最多给 128 个交易日，"
              f"当前已{'超过' if len(ds) > 128 else '未超过'}该上限")
        print(f"  按每年约 244 个交易日算，攒满 2 年需要再等 "
              f"{max(0, 488 - len(ds))} 个交易日")


if __name__ == "__main__":
    if "--breadth" in sys.argv:
        show_breadth()
    elif "--stat" in sys.argv:
        stat()
    elif "--backfill" in sys.argv:
        # 首次回填：把接口能给的全部历史（约 128 个交易日）一次性灌进来
        collect(datalen=1023)
    else:
        collect()
