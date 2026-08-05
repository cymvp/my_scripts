#!/usr/bin/env python3
"""早盘板块播报 —— 北京 9:30 / 9:45 / 10:00 各跑一次，结果发 Slack。

设计要点（都是踩过坑之后定的）：

1. **必须由系统 cron 驱动，不能依赖 Claude 会话**。会话关掉任务就没了。
2. **cron 不继承 shell 环境变量**，所以 webhook 从 ~/.config/ycui/slack.json 读，
   不读 SLACK_WEBHOOK_URL。这是最容易静默失败的一环。
3. **本机时区是 JST，比北京快 1 小时**。cron 时刻要用本机时间写，
   北京 9:30 = 本机 10:30。脚本内部一律用 Asia/Shanghai 判断交易时段。
4. **非交易日直接退出**，不发消息（周末 + 手工维护的节假日表）。
5. **不做方向预测**。本会话用 40 多个检验测过：板块动量、板块量能、个股量能、
   连涨3天+放量，BH 校正后无一通过。所以播报只给【已发生的事实】
   （涨跌、成交额、连涨天数、量能变化、龙头股），不给「该买哪个」。
   唯一有数据支持的是【大盘量能】，但 r² 仅 1.06%，只用来标注是否虚涨。

用法：
    python3 sector_morning_slack.py            # 正常跑，发 Slack
    python3 sector_morning_slack.py --dry-run  # 只打印不发
"""
import datetime
import json
import re
import sys
import urllib.parse
import urllib.request
import zoneinfo

BJT = zoneinfo.ZoneInfo("Asia/Shanghai")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
# 两个分类源要一起用。2026-08-05 用户指出的问题：只用行业分类（49 个传统行业，
# 玻璃/船舶/造纸这种）会把半导体稀释进「电子器件」「电子信息」，
# 实测当天科创半导体ETF +4.74%、软件龙头ETF +2.99%，而脚本只报出「电子器件 +2.86%」，
# 软件更是整个没有。所以必须合并概念板块（175 个，含华为海思/国产软件/科创50）。
# 2026-08-05 换成同花顺。新浪的分类太粗（49 个传统行业，半导体被拆进「电子器件」
# 「电子信息」，软件没有独立板块）。东方财富整域不可达（push2 / 1.push2 / 7.push2 /
# 82.push2 / quote / datacenter-web 全部 HTTP 000），只有 fundf10 能通。
# 同花顺：行业 90 个 + 概念 361 个，「半导体」「软件开发」「人工智能」「算力租赁」
# 「数据中心(AIDC)」都是独立板块，列表页默认按涨跌幅降序，成分股也能取。
THS_LIST = ("http://q.10jqka.com.cn/{seg}/index/field/199112/order/desc/page/{page}/")
THS_HOME = "http://q.10jqka.com.cn/{seg}/"
THS_DETAIL = "http://q.10jqka.com.cn/{seg}/detail/code/{code}/"
THS_REF = "http://q.10jqka.com.cn/"
# 同花顺只有【行业】排行可用：概念页是「新概念资讯表」不是排行表，
# 概念代码（30xxxx）在 d.10jqka 分时接口上也是 404，只有行业码（88xxxx）能通。
# 所以概念主题继续用新浪那 175 个补充。
THS_SEGS = [("thshy", "行业")]
SINA_GN_URL = "http://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
SINA_NODE_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                 "Market_Center.getHQNodeData?page=1&num={n}&sort=amount&asc=0"
                 "&node={node}&symbol=&_s_r_a=page")
# 这些不是可交易主题，是事件/技术性分组，排名时要剔除，否则天天霸榜
EXCLUDE = ("ST板块", "送转潜力", "本月解禁", "次新股", "出口退税", "预亏预减",
           "预盈预增", "举牌概念", "融资融券", "股权转让", "参股银行", "参股券商",
           "高送转", "破净股", "机构重仓", "基金重仓", "QFII重仓", "社保重仓")
# 行业与概念两套分类都没有的主题，用 ETF 补。用户实际看的就是这些。
ETF_THEMES = [("sh588170", "科创半导体"), ("sh588200", "科创芯片"),
              ("sh512480", "半导体"), ("sz159995", "芯片"),
              ("sh512760", "半导体设备"), ("sz159852", "软件"),
              ("sh515230", "软件龙头"), ("sz159819", "人工智能"),
              ("sh515880", "通信"), ("sh512980", "传媒")]
NODE_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=1&num={n}&sort=amount&asc=0&node={node}"
            "&symbol=&_s_r_a=page")
KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
         "?param={code},day,,,{n},qfq")
TOP_N = 5            # 播报几个板块
LEADERS = 3          # 每个板块给几只龙头
# 板块入选门槛用「占全市场成交额的比例」而不是绝对值——因为盘口成交额随时间累积，
# 固定阈值在 9:30 会把所有板块都挡掉。2026-08-05 实测：9:25 全市场 172 亿、9:33 才 404 亿。
MIN_SHARE = 0.012    # 板块成交额需 ≥ 全市场的 1.2% 才进排名（49 个板块，均值约 2%）
MIN_MKT_AMT = 150e8  # 低于此值判为集合竞价或接口异常，才真正跳过
THIN_MKT = 700e8     # 低于此值只加「开盘初期，排名不稳」的警告，不跳过
# 2026 年 A 股休市日（春节等）。每年要手工补，漏了只会多发一条消息，不会出错。
HOLIDAYS = {
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-06", "2026-05-01", "2026-06-19", "2026-09-25", "2026-10-01",
    "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
}


def get(url, enc="utf-8", referer=None):
    """referer 必须按目标站点给。2026-08-05 踩过：天天基金 fundf10 用新浪的 Referer
    会直接 404，而 curl 测试时带对了 Referer 是 200——搬进脚本时漏了这一项。"""
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
        return r.read().decode(enc, "replace")


def is_trading_day(now):
    return now.weekday() < 5 and now.date().isoformat() not in HOLIDAYS


def _ths_codes(seg):
    """板块名 → 代码。从分类首页的字母索引里抓，链接形如 /thshy/detail/code/881121/"""
    try:
        d = get(THS_HOME.format(seg=seg), "gbk", referer=THS_REF)
    except Exception:
        return {}
    return {n: c for c, n in
            re.findall(rf'/{seg}/detail/code/(\d{{6}})/?"[^>]*>([^<]+)<', d)}


def _ths_rank(seg, pages=2):
    """板块排行。列表页默认按涨跌幅降序，一页 50 行。

    ⚠ 真实表头（2026-08-05 核对，之前列错位过，把成交量当成了成交额，
    算出「全市场 97637 亿」这种不可能的数）：
      [0]序号 [1]板块 [2]涨跌幅% [3]总成交量(万手) [4]总成交额(亿元)
      [5]净流入(亿元) [6]上涨家数 [7]下跌家数 [8]均价
      [9]领涨股 [10]领涨股最新价 [11]领涨股涨跌幅%
    """
    out = []
    for pg in range(1, pages + 1):
        url = THS_HOME.format(seg=seg) if pg == 1 else THS_LIST.format(seg=seg, page=pg)
        try:
            d = get(url, "gbk", referer=THS_REF)
        except Exception:
            break
        for r in re.findall(r"<tr>(.*?)</tr>", d, re.S):
            t = [re.sub(r"<[^>]+>", "", x).strip()
                 for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            if len(t) >= 12 and re.match(r"^\d+$", t[0]):
                try:
                    out.append({"name": t[1], "chg": float(t[2]),
                                "amount": float(t[4]) * 1e8,      # [4] 才是成交额
                                "inflow": float(t[5]) * 1e8,      # 净流入，同花顺独有
                                "n_up": int(t[6]), "n_down": int(t[7]),
                                "leader": t[9], "leader_chg": float(t[11])})
                except ValueError:
                    continue
    return out


def _sina_concepts():
    """新浪概念 175 个。同花顺概念排行取不到，用它补概念维度。
    字段：f[1]=名称 f[5]=平均涨跌幅% f[7]=成交额(元) f[12]=领涨股名"""
    try:
        d = get(SINA_GN_URL, "gbk", referer="https://finance.sina.com.cn")
    except Exception:
        return []
    out = []
    for node, payload in re.findall(r'"(gn_[A-Za-z0-9_]+)"\s*:\s*"([^"]+)"', d):
        f = payload.split(",")
        if len(f) < 13:
            continue
        try:
            out.append({"name": f[1], "chg": float(f[5]), "amount": float(f[7]),
                        "n_stock": int(float(f[2])), "kind": "概念",
                        "src": "sina", "node": node})
        except ValueError:
            continue
    return out


def fetch_sectors():
    """主榜用同花顺行业（90 个，分类准确），概念维度用新浪（175 个）补。"""
    out = []
    for seg, kind in THS_SEGS:
        codes = _ths_codes(seg)
        for row in _ths_rank(seg):
            row.update(kind=kind, seg=seg, src="ths", node=codes.get(row["name"]))
            out.append(row)
    out += _sina_concepts()
    return [s for s in out if not any(k in s["name"] for k in EXCLUDE)]


ETF_HOLD_URL = ("http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
                "?type=jjcc&code={code}&topline=10&year=&month=")
_HOLD_CACHE = {}


def fetch_etf_holdings(code, n=LEADERS):
    """ETF 前 n 大重仓股。数据来自天天基金的季报持仓。

    ⚠ 这是【季报】数据，不是实时持仓，最长会滞后一个季度。
    对宽基/主题 ETF 影响不大（成分调整不频繁），但要在消息里标明。
    东财的行情接口 push2 在本机不可达，但 fundf10 这个可以，实测 HTTP 200。
    """
    if code in _HOLD_CACHE:
        return _HOLD_CACHE[code]
    try:
        d = get(ETF_HOLD_URL.format(code=code[2:]),
                referer="http://fundf10.eastmoney.com/")
        m = re.search(r'content:"(.*?)",arryear', d, re.S)
        import html as _html
        c = _html.unescape(m.group(1)) if m else d
        out = []
        for r in re.findall(r"<tr>(.*?)</tr>", c, re.S):
            tds = [re.sub(r"<[^>]+>", "", x).strip()
                   for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
            if len(tds) < 7 or not re.match(r"^\d{6}$", tds[1]):
                continue
            out.append((tds[1], tds[2], tds[-3]))     # 代码, 名称, 占净值比
            if len(out) >= n:
                break
        _HOLD_CACHE[code] = out
        return out
    except Exception:
        _HOLD_CACHE[code] = []
        return []


def fetch_etf_themes():
    """主题 ETF 看板：补上行业/概念分类都没有的半导体、芯片、软件、AI 等。"""
    codes = ",".join(c for c, _ in ETF_THEMES)
    try:
        d = get(f"https://hq.sinajs.cn/list={codes}", "gbk")
    except Exception:
        return []
    px = {}
    for line in d.split(";"):
        if '="' not in line:
            continue
        code = line.split("hq_str_")[1].split("=")[0]
        f = line.split('"')[1].split(",")
        if len(f) < 4 or not f[3]:
            continue
        pc, cur = float(f[2]), float(f[3])
        if pc > 0 and cur > 0:
            px[code] = (cur - pc) / pc * 100
    return [(name, px[c]) for c, name in ETF_THEMES if c in px]


def fetch_leaders(sec, n=LEADERS):
    """板块成分股。两个源的取法不同，按 src 分流。

    同花顺：非 ajax 详情页，默认按涨跌幅降序。⚠ ajax 版返回 401。
    新浪：node API，按成交额降序。
    """
    if sec.get("src") == "sina":
        try:
            d = json.loads(get(SINA_NODE_URL.format(n=n, node=sec["node"]),
                               referer="https://finance.sina.com.cn"))
        except Exception:
            return []
        return [{"name": x["name"], "symbol": x["symbol"][2:],
                 "chg": float(x["changepercent"] or 0),
                 "price": float(x["trade"] or 0)} for x in d]
    if not sec.get("node"):
        return []
    try:
        d = get(THS_DETAIL.format(seg=sec["seg"], code=sec["node"]), "gbk",
                referer=THS_HOME.format(seg=sec["seg"]))
    except Exception:
        return []
    out = []
    for r in re.findall(r"<tr>(.*?)</tr>", d, re.S):
        t = [re.sub(r"<[^>]+>", "", x).strip()
             for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if len(t) >= 6 and re.match(r"^\d{6}$", t[1]):
            try:
                out.append({"name": t[2], "symbol": t[1],
                            "chg": float(t[4]), "price": float(t[3])})
            except ValueError:
                continue
        if len(out) >= n:
            break
    return out


def sector_history(sec, days=6):
    """板块近几日走势：用板块内成交额最大的 3 只票等权近似。

    新浪板块接口只有当日快照、没有历史；东财板块 K 线接口本机不可达。
    所以沿用 sector_trend.py 的代表股篮子做法，但代表股改为【当日成交额前三】，
    比写死的龙头名单更能跟上板块结构变化。
    """
    reps = fetch_leaders(sec, 3)
    if not reps:
        return None
    series = []
    for r in reps:
        try:
            code = r["symbol"]
            code = code if code[:2] in ("sh", "sz") else \
                ("sh" if code[0] == "6" else "sz") + code
            data = json.loads(get(KLINE.format(code=code, n=days + 6)))
            k = data["data"][code]
            bars = k.get("qfqday") or k.get("day")
            series.append([(x[0], float(x[2]), float(x[5])) for x in bars if float(x[5]) > 0])
        except Exception:
            continue
    if not series:
        return None
    m = min(len(s) for s in series)
    if m < days + 1:
        return None

    def agg(i):                       # i 从末尾往前数
        return sum(s[-1 - i][1] for s in series) / len(series)

    def vol(i):
        return sum(s[-1 - i][2] for s in series) / len(series)

    # 连涨天数：从最近一个已收盘交易日往前数
    streak = 0
    for i in range(0, min(6, m - 1)):
        if agg(i) > agg(i + 1):
            streak += 1
        else:
            break
    d3 = (agg(0) - agg(3)) / agg(3) * 100 if m > 3 else None
    d5 = (agg(0) - agg(5)) / agg(5) * 100 if m > 5 else None
    v_now = sum(vol(i) for i in range(3)) / 3
    v_prev = sum(vol(i) for i in range(3, 6)) / 3
    return {"streak": streak, "d3": d3, "d5": d5,
            "vr": v_now / v_prev if v_prev > 0 else None}


def market_volume():
    """**上一交易日**的大盘量能：两市成交量 ÷ 之前 20 日均量。

    ⚠ 口径说明（2026-08-05 试跑时连踩两个坑）：
    坑一：早盘不能拿今日盘中累计量去比全天均量——分母是全天、分子只有几十分钟。
    坑二：日 K 接口**会返回今日尚未完成的 K 线**。第一版以为最后一根是昨天，
          结果算出「昨日量能 0.08」这种不可能的值（实际是今天开盘 3 分钟的量）。
          所以必须按日期显式剔除今天，再取最后一根作为上一交易日。

    这是本会话唯一通过检验的量能信号（r=+0.103, p=0.0040），
    但 r² 仅 1.06%，所以只用来标注「是否虚涨」，不作方向依据。
    """
    today = datetime.datetime.now(BJT).date().isoformat()
    tot_now = tot_avg = 0.0
    for code in ("sh000001", "sz399001"):
        try:
            data = json.loads(get(KLINE.format(code=code, n=30)))
            k = data["data"][code]
            bars = k.get("qfqday") or k.get("day")
            # 显式剔除今天这根（盘中未完成），只用已收盘的交易日
            vols = [float(x[5]) for x in bars if float(x[5]) > 0 and x[0] < today]
            if len(vols) < 21:
                return None
            tot_now += vols[-1]                 # 上一交易日全天量
            tot_avg += sum(vols[-21:-1]) / 20   # 再往前 20 日均量
        except Exception:
            return None
    return tot_now / tot_avg if tot_avg > 0 else None


def build_message(now):
    secs = fetch_sectors()
    live = [s for s in secs if s["amount"] > 0]
    if not live:
        return None, "板块接口全为 0（尚未开盘或接口异常）"
    # 成交额合计只用【行业】口径——概念板块之间互相重叠，相加会重复计算
    mkt_amt = sum(s["amount"] for s in live if s["kind"] == "行业")
    if mkt_amt < MIN_MKT_AMT:
        return None, (f"全市场板块成交额仅 {mkt_amt/1e8:.0f} 亿（门槛 {MIN_MKT_AMT/1e8:.0f} 亿），"
                      f"应为集合竞价或开盘极初期，排名不稳定，跳过本次")
    # 只在「有真实成交」的板块里排名——避免选出成交额接近 0 的迷你板块
    liquid = [s for s in live if s["amount"] >= mkt_amt * MIN_SHARE]
    if len(liquid) < TOP_N:
        liquid = sorted(live, key=lambda x: -x["amount"])[:max(TOP_N * 3, 15)]
    top = sorted(liquid, key=lambda x: -x["chg"])[:TOP_N]
    down = sorted(liquid, key=lambda x: x["chg"])[:3]

    lines = [f"*A股早盘板块播报  {now:%Y-%m-%d %H:%M} 北京*"]
    lines.append(f"今日全市场板块成交额合计 *{mkt_amt/1e8:.0f}亿*"
                 f"（截至 {now:%H:%M}，仅供判断放量节奏）")
    mv = market_volume()
    if mv:
        tag = "放量" if mv > 1.15 else ("缩量" if mv < 0.85 else "平量")
        lines.append(f"*上一交易日*量能：全天量 / 之前20日均量 = *{mv:.2f}*（{tag}）")
        if mv < 0.85:
            lines.append("  ⚠ 缩量状态。实测缩量上涨日后续5日 −0.12%，有量上涨日 +0.63%（基准 +0.13%）")
    up_amt = sum(s["amount"] for s in live if s["chg"] > 0 and s["kind"] == "行业")
    dn_amt = sum(s["amount"] for s in live if s["chg"] < 0 and s["kind"] == "行业")
    if mkt_amt < THIN_MKT:
        lines.append(f"⚠ *开盘初期，全市场成交额仅 {mkt_amt/1e8:.0f}亿，板块排名不稳定，"
                     f"后面两档（9:45 / 10:00）会更可靠*")
    lines.append(f"入选排名：成交额 ≥全市场 {MIN_SHARE*100:.1f}%（≈{mkt_amt*MIN_SHARE/1e8:.0f}亿）"
                 f"的板块共 {len(liquid)} 个")
    lines.append(f"涨跌家数 {sum(1 for s in live if s['chg'] > 0)}/{sum(1 for s in live if s['chg'] < 0)}"
                 f"，上涨板块成交额 {up_amt/1e8:.0f}亿 vs 下跌 {dn_amt/1e8:.0f}亿"
                 f"（比值 {up_amt/dn_amt:.2f}）" if dn_amt > 0 else "")
    themes = fetch_etf_themes()
    if themes:
        lines.append("")
        lines.append("*主题 ETF 看板*（行业/概念两套分类都没有这些，用 ETF 补）")
        ranked = sorted(themes, key=lambda x: -x[1])
        lines.append("   " + " · ".join(f"{n} {v:+.2f}%" for n, v in ranked))
        # 涨幅前二的主题，补上它的前三大重仓股（季报持仓，非实时）
        code_of = {n: c for c, n in ETF_THEMES}
        for name, chg in ranked[:2]:
            hold = fetch_etf_holdings(code_of[name])
            if hold:
                lines.append(f"   {name} 前三大重仓（季报持仓，非实时）："
                             + "、".join(f"{nm}({cd}) {w}" for cd, nm, w in hold))
    lines.append("")

    for rank, s in enumerate(top, 1):
        h = sector_history(s)
        seg = [f"*{rank}. {s['name']}  {s['chg']:+.2f}%*  成交额 {s['amount']/1e8:.0f}亿"
               f"  [{s['kind']}]"]
        if s.get("inflow") is not None:
            seg.append(f"   净流入 {s['inflow']/1e8:+.1f}亿 · "
                       f"涨跌家数 {s.get('n_up','?')}/{s.get('n_down','?')}")
        if h:
            bits = []
            if h["streak"] >= 1:
                bits.append(f"已连涨 *{h['streak']}* 天")
            if h["d3"] is not None:
                bits.append(f"近3日 {h['d3']:+.1f}%")
            if h["d5"] is not None:
                bits.append(f"近5日 {h['d5']:+.1f}%")
            if h["vr"]:
                bits.append(f"量比 {h['vr']:.2f}")
            seg.append("   " + " · ".join(bits))
            if h["d3"] is not None and h["d5"] is not None and h["d3"] > 0 > h["d5"]:
                seg.append("   ⚠ 近3日涨但近5日跌 = 超跌反弹，不是趋势反转")
            if h["streak"] >= 3:
                seg.append(f"   ⚠ 这是连涨第 {h['streak']} 天，不是第一天")
        for x in fetch_leaders(s):
            seg.append(f"   • {x['name']}({x['symbol']}) {x['chg']:+.2f}%  "
                       f"现价 {x['price']:.2f}")
        lines.append("\n".join(seg))
        lines.append("")

    lines.append("跌幅前三：" + " · ".join(f"{s['name']} {s['chg']:+.2f}%" for s in down))
    lines.append("")
    lines.append("_只报已发生的事实，不预测方向。板块动量/板块量能/个股量能/连涨+放量_")
    lines.append("_共 40+ 项检验经 BH 校正后无一通过，故不给「该买哪个」。龙头按成交额排。_")
    return "\n".join(x for x in lines if x is not None), None


def send_slack(text):
    cfg = json.load(open(f"{__import__('os').path.expanduser('~')}/.config/ycui/slack.json"))
    hook = cfg["slack_webhook_url"]
    body = json.dumps({"blocks": [{"type": "section",
                                   "text": {"type": "mrkdwn", "text": text[:2900]}}]}).encode()
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    dry = "--dry-run" in sys.argv
    now = datetime.datetime.now(BJT)
    if not dry and not is_trading_day(now):
        print(f"{now:%Y-%m-%d} 非交易日，跳过")
        return
    text, err = build_message(now)
    if err:
        print("跳过：" + err)
        return
    print(text)
    if dry:
        print("\n[--dry-run] 未发送")
        return
    print("\nSlack 状态:", send_slack(text))


if __name__ == "__main__":
    main()
