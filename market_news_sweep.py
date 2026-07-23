#!/usr/bin/env python3
"""全球市场快讯扫描 — /close-advisor 的事件输入源。

用法：
    python3 tools/market_news_sweep.py            # 默认最近12小时
    python3 tools/market_news_sweep.py 24         # 最近24小时

抓取（直连，不依赖搜索引擎索引）：
  中文：新浪财经7×24快讯、华尔街见闻全球快讯
  国外：CNBC Top News、MarketWatch Top、WSJ Markets、Yahoo Finance 各RSS头条

输出按时间倒序，命中关键词的条目加 ★ 标记。本工具只做采集，
筛选与解读由调用方（Claude/人）完成。
"""
import subprocess, json, sys, re, datetime, html
import requests
from zoneinfo import ZoneInfo

BJT = ZoneInfo("Asia/Shanghai")
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 12
CUTOFF = datetime.datetime.now(BJT) - datetime.timedelta(hours=HOURS)

KEYWORDS = ["美联储", "Fed", "FOMC", "CPI", "关税", "tariff", "半导体", "芯片", "chip",
            "英伟达", "Nvidia", "NVDA", "光模块", "存储", "DRAM", "HBM", "美光", "Micron",
            "台积电", "TSMC", "海力士", "Hynix", "三星", "Samsung", "capex", "资本开支",
            "长鑫", "中际旭创", "寒武纪", "澜起", "出口管制", "export control", "议息",
            "earnings", "财报", "AI", "霍尔木兹", "原油", "oil", "地缘", "IPO"]

def mark(text):
    return "★ " if any(k.lower() in text.lower() for k in KEYWORDS) else "  "

def curl_noproxy(url):
    r = subprocess.run(["/usr/bin/curl", "-sL", "--noproxy", "*", "-m", "15",
                        "-H", "User-Agent: Mozilla/5.0", url],
                       capture_output=True, timeout=20)
    return r.stdout.decode("utf-8", errors="replace")

items = []  # (datetime_bjt, source, text)

# ---- 新浪财经 7×24 ----
try:
    d = json.loads(curl_noproxy(
        "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=60&zhibo_id=152"))
    for it in d["result"]["data"]["feed"]["list"]:
        t = datetime.datetime.strptime(it["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJT)
        txt = re.sub(r"\s+", " ", it["rich_text"]).strip()
        items.append((t, "新浪7×24", txt))
except Exception as e:
    print(f"[新浪7×24 抓取失败: {e}]", file=sys.stderr)

# ---- 华尔街见闻 全球快讯 ----
try:
    d = json.loads(curl_noproxy(
        "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=60"))
    for it in d["data"]["items"]:
        t = datetime.datetime.fromtimestamp(it["display_time"], tz=BJT)
        txt = re.sub(r"<[^>]+>|\s+", " ", it.get("content_text") or it.get("title") or "").strip()
        if txt:
            items.append((t, "华尔街见闻", txt))
except Exception as e:
    print(f"[华尔街见闻 抓取失败: {e}]", file=sys.stderr)

# ---- 国外 RSS ----
RSS = [("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
       ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
       ("WSJ-Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
       ("YahooFin", "https://finance.yahoo.com/news/rssindex")]

def parse_rss(name, xml):
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        blk = m.group(1)
        tt = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
        dt = re.search(r"<pubDate>(.*?)</pubDate>", blk)
        if not tt: continue
        title = html.unescape(tt.group(1).strip())
        when = None
        if dt:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
                try:
                    when = datetime.datetime.strptime(dt.group(1).strip(), fmt).astimezone(BJT)
                    break
                except ValueError:
                    pass
        if when is None:
            when = datetime.datetime.now(BJT)  # 无时间戳的按当前时间保留，宁多勿漏
        yield when, name, title

for name, url in RSS:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        got = list(parse_rss(name, r.text))
        # 无pubDate的源（如Yahoo）全部条目被标为当前时间，限流防刷屏，且★条目优先保留
        now = datetime.datetime.now(BJT)
        undated = [g for g in got if (now - g[0]).total_seconds() < 60]
        if len(undated) > 15:
            starred = [g for g in undated if mark(g[2]) == "★ "]
            got = [g for g in got if g not in undated] + (starred + undated)[:15]
        items.extend(got)
    except Exception as e:
        print(f"[{name} 抓取失败: {e}]", file=sys.stderr)

# ---- 输出 ----
recent = sorted((x for x in items if x[0] >= CUTOFF), reverse=True)
print(f"===== 全球市场快讯（最近{HOURS:.0f}小时，共{len(recent)}条，★=命中关注关键词）=====")
for t, src, txt in recent:
    print(f"{mark(txt)}{t:%m-%d %H:%M} [{src}] {txt[:150]}")
if not recent:
    print("（无数据——检查网络或各源可用性）")
