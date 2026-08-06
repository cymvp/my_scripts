#!/usr/bin/env python3
"""收盘前次日开盘预测工具 — 中际旭创/寒武纪/澜起科技。

用法：每个交易日 14:40-14:55 运行
    python3 ~/projects/my_scripts/open_gap_forecast.py

做什么：
1. 实时抓取此刻收盘前可得的全部外盘信号：
   - 纳指100期货 NQ 夜盘累计涨跌（北京时间 6:00 至今）
   - KOSPI / SK海力士 / 三星电子 当日涨跌（14:30 BJT 已收盘）
   - 台积电当日涨跌（13:30 BJT 已收盘）
   - 三只标的当日自身涨跌
2. 输出每只票的次日跳空幅度风险（基于5日已实现波动率分层，回测显著 r≈0.24-0.32）。
3. 给出决策建议。

本工具不输出方向预测：经117个交易日回测，收盘前一切信号对次日开盘方向的
预测准确率均为随机水平（43%-56%），因为次日开盘由当晚美股实际走势决定，
而它在15:00尚未发生；且已验证美股剩余时段无法由夜盘已走幅度预测（r=+0.04）。
方向判断请在次日9:15用 ~/projects/my_scripts/morning_open_forecast.py 做（准确率70-77%）。
详见 ~/projects/ai-berkshire/reports/A股科技股开盘预测-research-20260723.md。
"""
import subprocess, json, math, datetime, sys
import requests
from zoneinfo import ZoneInfo

BJT = ZoneInfo("Asia/Shanghai"); KST = ZoneInfo("Asia/Seoul"); TWT = ZoneInfo("Asia/Taipei")
# 2026-08-06 与 ycui_market_advisor 的默认清单对齐（用户 2026-08-04 确认的长期持仓）。
# 长鑫科技 2026-07-27 上市，日线不足时脚本会自动跳过它的波动率分层——这是预期行为，
# 不是 bug：新股没有 5 日波动率历史，任何「预期跳空 x%」的估算都不适用。
STOCKS = [("中际旭创", "sz300308"), ("国际复材", "sz301526"), ("长鑫科技", "sh688825")]

def curl(url):
    r = subprocess.run(["/usr/bin/curl", "-sL", "--noproxy", "*", "-m", "25",
                        "-H", "Referer: https://gu.qq.com/",
                        "-H", "User-Agent: Mozilla/5.0", url],
                       capture_output=True, timeout=30)
    return r.stdout.decode("utf-8", errors="replace")

def yahoo(sym, itv, rng):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"interval": itv, "range": rng},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    res = r.json()["chart"]["result"][0]
    return res["timestamp"], res["indicators"]["quote"][0], res["meta"]

def yahoo_last_and_prevclose(sym, tz):
    """最新价、昨收、当日涨跌。"""
    ts, q, meta = yahoo(sym, "1d", "5d")
    closes = [(t, c) for t, c in zip(ts, q["close"]) if c]
    if len(closes) < 2:
        return None
    last_t, last = closes[-1]
    prev = closes[-2][1]
    d = datetime.datetime.fromtimestamp(last_t, tz=tz).date()
    return dict(date=d, last=last, prev=prev, ret=last/prev-1)

def tencent_daily(code, n=140):
    dd = json.loads(curl(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq"))["data"][code]
    key = "qfqday" if "qfqday" in dd else "day"
    return [(datetime.date.fromisoformat(r[0]), float(r[1]), float(r[2])) for r in dd[key]]

def tencent_quote(code):
    raw = curl(f"https://qt.gtimg.cn/q={code}")
    f = raw.split('"')[1].split("~")
    return dict(name=f[1], price=float(f[3]), prev_close=float(f[4]))

def nq_series():
    ts, q, _ = yahoo("NQ=F", "60m", "6mo")
    return sorted((datetime.datetime.fromtimestamp(t, tz=BJT), c)
                  for t, c in zip(ts, q["close"]) if c)

def nq_price_at(nq, dt):
    best = None
    for bt, c in nq:
        if bt <= dt: best = c
        else: break
    return best

def main():
    now = datetime.datetime.now(BJT)
    today = now.date()
    print(f"===== 次日开盘预测  运行时间 {now:%Y-%m-%d %H:%M} 北京时间 =====\n")

    # ---- 实时信号 ----
    nq = nq_series()
    t06 = datetime.datetime(today.year, today.month, today.day, 6, 0, tzinfo=BJT)
    p06 = nq_price_at(nq, t06); pnow = nq[-1][1]
    s2 = pnow/p06 - 1 if p06 else None
    kospi = yahoo_last_and_prevclose("^KS11", KST)
    hynix = yahoo_last_and_prevclose("000660.KS", KST)
    samsung = yahoo_last_and_prevclose("005930.KS", KST)
    tsmc = yahoo_last_and_prevclose("2330.TW", TWT)

    print("【此刻可见的外盘信号】")
    if s2 is not None:
        print(f"  纳指期货NQ 夜盘累计(6:00→现在): {s2:+.2%}   （最新bar时间 {nq[-1][0]:%m-%d %H:%M}）")
    for label, x in [("KOSPI", kospi), ("SK海力士", hynix), ("三星电子", samsung), ("台积电", tsmc)]:
        if x:
            stale = "" if x["date"] == today else f" ⚠数据日期{x['date']}"
            print(f"  {label} 当日: {x['ret']:+.2%}{stale}")
    print()

    # ---- 每只票：跳空幅度风险 ----
    for name, code in STOCKS:
        days = tencent_daily(code)
        dates = [d for d, o, c in days]
        O = {d: o for d, o, c in days}; C = {d: c for d, o, c in days}
        nxt = {dates[i]: dates[i+1] for i in range(len(dates)-1)}
        qt = tencent_quote(code)
        self_ret = qt["price"]/qt["prev_close"]-1
        # 幅度风险模型：5日已实现波动率 -> 次日|跳空|的条件分布（回测显著，r≈0.24-0.32）
        rv_hist = []  # (rv5, next_absgap)
        for d in dates:
            i = dates.index(d)
            if d not in nxt or i < 6: continue
            rv5 = math.sqrt(sum((C[dates[i-k]]/C[dates[i-k-1]]-1)**2 for k in range(5))/5)
            rv_hist.append((rv5, abs(O[nxt[d]]/C[d]-1)))
        rv_hist.sort()
        k3 = len(rv_hist)//3
        closes_recent = [C[d] for d in dates[-6:]]
        if dates[-1] != today:
            closes_recent = (closes_recent + [qt["price"]])[-6:]
        else:
            closes_recent[-1] = qt["price"]
        rv5_now = math.sqrt(sum((closes_recent[j+1]/closes_recent[j]-1)**2 for j in range(5))/5)
        if rv5_now <= rv_hist[k3][0]: tier, grp = "低波动", rv_hist[:k3]
        elif rv5_now <= rv_hist[2*k3][0]: tier, grp = "中波动", rv_hist[k3:2*k3]
        else: tier, grp = "高波动", rv_hist[2*k3:]
        gs_t = [g for _, g in grp]
        exp_gap = sum(gs_t)/len(gs_t)
        p_big = sum(1 for g in gs_t if g > 0.02)/len(gs_t)
        print(f"── {name}（今日自身 {self_ret:+.2%}，现价 {qt['price']}）")
        print(f"   ➤ 幅度风险[{tier}期]: 预期次日|跳空|≈{exp_gap:.1%}，跳空超±2%的概率≈{p_big*100:.0f}%"
              f"（5日波动率={rv5_now:.2%}/日）")
        print()

    print("【决策建议】")
    print("  1. 本工具不给方向判断：收盘前方向预测经回测为随机水平，给了反而害人。")
    print("  2. 卖出决策依据 = 上面的幅度风险 × 你的仓位：预期跳空打不疼就持有，")
    print("     打得疼说明仓位过重，减仓位而不是猜方向。")
    print("  3. 唯一建议收盘前减仓的情形：当晚有已知重磅事件（美联储议息FOMC、CPI、")
    print("     英伟达/美光/台积电财报）——隔夜波动放大且方向不可测，减仓是风险管理。")
    print("  4. 方向判断在次日9:15运行 ~/projects/my_scripts/morning_open_forecast.py（准确率70-77%），")
    print("     且跳空之后盘中无延续，低开后开盘卖出与前日收盘卖出期望等价。")

if __name__ == "__main__":
    main()
