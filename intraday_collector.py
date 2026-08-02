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
数据存 intraday_bars.jsonl，一行一条 {code,date,bars:[{t,o,h,l,c,v}×8]}。

用法：
  python3 intraday_collector.py          抓取并追加今天（收盘后跑）
  python3 intraday_collector.py --stat   打印已积累的覆盖情况
"""
import json
import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE_DIR, "intraday_bars.jsonl")

try:
    from intraday_guide import POOL
except ImportError:
    POOL = {}


def fetch_m30(code, datalen=40):
    """新浪 30 分钟 K 线。

    日常增量取 40 根（覆盖最近 5 个交易日）即可，不必每次拉满。
    首次回填用 datalen=1023，把接口能给的 128 个交易日一次性灌进来。
    """
    u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
         f"?symbol={code}&scale=30&datalen={datalen}")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=25).read()
    return json.loads(raw.decode("utf-8", "ignore"))


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


def collect(datalen=40):
    if not POOL:
        sys.exit("样本池为空：intraday_guide.POOL 导入失败")
    seen = load_existing()
    added = 0
    failed = []
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
    if failed:
        print(f"取数失败：{', '.join(failed)}")
    stat(brief=True)


def stat(brief=False):
    if not os.path.exists(STORE):
        sys.exit(f"还没有数据：{STORE}")
    codes, dates, n = set(), set(), 0
    with open(STORE, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            codes.add(r["code"])
            dates.add(r["date"])
            n += 1
    ds = sorted(dates)
    size = os.path.getsize(STORE) / 1024 / 1024
    print(f"已积累：{len(codes)} 只票 × {len(ds)} 个交易日 = {n} 条，{size:.1f} MB")
    print(f"  区间 {ds[0]} ~ {ds[-1]}")
    if not brief:
        print(f"  参考：新浪 30 分钟接口一次最多给 128 个交易日，"
              f"当前已{'超过' if len(ds) > 128 else '未超过'}该上限")
        print(f"  按每年约 244 个交易日算，攒满 2 年需要再等 "
              f"{max(0, 488 - len(ds))} 个交易日")


if __name__ == "__main__":
    if "--stat" in sys.argv:
        stat()
    elif "--backfill" in sys.argv:
        # 首次回填：把接口能给的全部历史（约 128 个交易日）一次性灌进来
        collect(datalen=1023)
    else:
        collect()
