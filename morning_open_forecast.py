#!/usr/bin/env python3
"""次日早盘开盘方向预测 — 中际旭创/寒武纪/澜起科技。

用法：每个交易日 9:10-9:25 运行（集合竞价期间，9:25出竞价结果前）
    python3 ~/projects/my_scripts/morning_open_forecast.py

原理（经2026-01~07共117个交易日回测，见 ~/projects/ai-berkshire/reports/A股科技股开盘预测-research-20260723.md）：
  此时点昨夜美股实际收盘已知，它是A股科技股开盘跳空的主导因子：
    NDX昨夜涨跌 -> 三只票开盘方向一致率 70%/72%/74%，r=0.39~0.56
  辅助信号：KOSPI早盘（8:00 BJT开盘，控制共同因子后偏相关+0.57）。
  与收盘前时点（准确率≈50%）的本质区别：决定性信息此刻已经发生。

输出：每只票的开盘方向判断 + 按NDX涨跌幅分层的历史条件概率 + 预期跳空。
决策提示：跳空之后盘中无延续（隔夜NDX对A股全天收益r=+0.01），
  所以"低开后开盘卖出"与"前日收盘卖出"期望等价——按今早信号决策不吃亏。
"""
import subprocess, json, math, datetime
import requests
from zoneinfo import ZoneInfo

BJT = ZoneInfo("Asia/Shanghai"); KST = ZoneInfo("Asia/Seoul"); NYT = ZoneInfo("America/New_York")
STOCKS = [("中际旭创", "sz300308"), ("寒武纪", "sh688256"), ("澜起科技", "sh688008")]

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

def tencent_daily(code, n=140):
    dd = json.loads(curl(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq"))["data"][code]
    key = "qfqday" if "qfqday" in dd else "day"
    return [(datetime.date.fromisoformat(r[0]), float(r[1]), float(r[2])) for r in dd[key]]

def main():
    now = datetime.datetime.now(BJT)
    today = now.date()
    print(f"===== 今日开盘方向预测  运行时间 {now:%Y-%m-%d %H:%M} 北京时间 =====\n")

    # ---- 昨夜美股实际收盘 ----
    ts, q, _ = yahoo("^NDX", "1d", "8mo")
    nd = [(datetime.datetime.fromtimestamp(t, tz=NYT).date(), c)
          for t, c in zip(ts, q["close"]) if c]
    ndx_ret = {nd[i][0]: nd[i][1]/nd[i-1][1]-1 for i in range(1, len(nd))}
    last_us_date, last_us_close = nd[-1]
    ndx_last = last_us_close/nd[-2][1]-1
    ts, q, _ = yahoo("^SOX", "1d", "5d")
    sox = [(datetime.datetime.fromtimestamp(t, tz=NYT).date(), c)
           for t, c in zip(ts, q["close"]) if c]
    sox_last = sox[-1][1]/sox[-2][1]-1 if len(sox) >= 2 else None
    expected_us_date = today - datetime.timedelta(days=1)
    warn = f" ⚠美股数据日期{last_us_date}，请确认是昨夜收盘" if last_us_date != expected_us_date else ""
    print(f"【昨夜美股实际收盘】")
    print(f"  纳指100(NDX): {ndx_last:+.2%}{warn}")
    if sox_last is not None:
        print(f"  费城半导体(SOX): {sox_last:+.2%}")

    # ---- 美股收盘后的期货变化（捕捉盘后财报/突发事件：收盘价里没有的信息）----
    ts, q, _ = yahoo("NQ=F", "60m", "5d")
    nq = sorted((datetime.datetime.fromtimestamp(t, tz=BJT), c)
                for t, c in zip(ts, q["close"]) if c)
    # 美股现货收盘=16:00 ET（夏令时=BJT 4:00，冬令时=BJT 5:00）；取4:00保证捕捉盘后第一波
    us_close_bjt = datetime.datetime(today.year, today.month, today.day, 4, 0, tzinfo=BJT)
    p_close = None
    for bt, c in nq:
        if bt <= us_close_bjt: p_close = c
        else: break
    if p_close and nq:
        after_hours = nq[-1][1]/p_close - 1
        note = ""
        if abs(after_hours) >= 0.005:
            note = "  ⚠盘后有大动作（财报/突发事件），方向以此为准，收盘价信号已过时"
        elif (after_hours >= 0) != (ndx_last >= 0) and abs(after_hours) >= 0.003:
            note = "  ⚠与昨夜收盘方向相反，信号可信度降低"
        print(f"  纳指期货 收盘后至今: {after_hours:+.2%}（最新bar {nq[-1][0]:%H:%M}）{note}")

    # ---- KOSPI 今日早盘（8:00 BJT开盘，此刻已交易约1小时+）----
    ts, q, meta = yahoo("^KS11", "5m", "1d")
    kbars = [(t, c) for t, c in zip(ts, q["close"]) if c]
    ts2, q2, _ = yahoo("^KS11", "1d", "5d")
    kdaily = [(datetime.datetime.fromtimestamp(t, tz=KST).date(), c)
              for t, c in zip(ts2, q2["close"]) if c]
    kospi_early = None
    if kbars and len(kdaily) >= 2:
        last_t, last_px = kbars[-1]
        bar_date = datetime.datetime.fromtimestamp(last_t, tz=KST).date()
        prev_close = kdaily[-2][1] if kdaily[-1][0] == bar_date else kdaily[-1][1]
        kospi_early = last_px/prev_close - 1
        fresh = "" if bar_date == today else f" ⚠数据日期{bar_date}"
        print(f"  KOSPI早盘(昨收→现在): {kospi_early:+.2%}{fresh}")
    print()

    # ---- 每只票：NDX分层条件概率 + 方向判断 ----
    # 分层阈值：|NDX|<0.5% 弱信号；>=0.5% 中；>=1.5% 强
    for name, code in STOCKS:
        days = tencent_daily(code)
        dates = [d for d, o, c in days]
        O = {d: o for d, o, c in days}; C = {d: c for d, o, c in days}
        nxt = {dates[i]: dates[i+1] for i in range(len(dates)-1)}
        # 历史：NDX当晚涨跌 -> 次日开盘跳空（A股日d的当晚=美东日历日d）
        hist = []
        for d in dates:
            if d not in nxt or d not in ndx_ret: continue
            hist.append((ndx_ret[d], O[nxt[d]]/C[d]-1))
        def layer(lo, hi):
            sub = [(x, y) for x, y in hist if lo <= abs(x) < hi]
            same = sum(1 for x, y in sub if x*y > 0); nz = sum(1 for x, y in sub if x*y != 0)
            return sub, same, nz
        a = abs(ndx_last)
        if a < 0.005: lo, hi, tier = 0, 0.005, "弱(|NDX|<0.5%)"
        elif a < 0.015: lo, hi, tier = 0.005, 0.015, "中(0.5%~1.5%)"
        else: lo, hi, tier = 0.015, 9, "强(>=1.5%)"
        sub, same, nz = layer(lo, hi)
        direction = "高开" if ndx_last >= 0 else "低开"
        # 该层内、与今夜同向的历史平均跳空
        same_side = [y for x, y in sub if (x >= 0) == (ndx_last >= 0)]
        exp = sum(same_side)/len(same_side) if same_side else 0.0
        conf = same/nz*100 if nz else 50
        kospi_note = ""
        if kospi_early is not None and (kospi_early >= 0) != (ndx_last >= 0) and abs(kospi_early) > 0.005:
            kospi_note = "（⚠KOSPI早盘与美股方向相反，可信度降低）"
        elif kospi_early is not None and (kospi_early >= 0) == (ndx_last >= 0):
            kospi_note = "（KOSPI早盘同向确认）"
        print(f"── {name}")
        print(f"   预测: {direction}  信号强度{tier}，该层历史方向一致率 {same}/{nz} = {conf:.0f}%")
        print(f"   同类情形历史平均跳空 {exp:+.2%}  {kospi_note}")
    print()
    print("【决策提示】")
    print("  0. 若'收盘后至今'期货变化超±0.5%（盘后财报等），以期货方向为主信号，")
    print("     上面基于收盘价的分层概率不适用。")
    print("  1. 信号为'强'且KOSPI同向确认时，方向可信度最高（历史70-77%）。")
    print("  2. 信号为'弱'时（美股平收），开盘方向接近随机，按持仓计划行事即可。")
    print("  3. 已验证跳空后盘中无延续：若决定卖出，开盘竞价或开盘即卖，")
    print("     不必等盘中反弹（外盘信息已在开盘价中兑现完毕）。")

if __name__ == "__main__":
    main()
