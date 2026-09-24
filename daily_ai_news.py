import os, sys, time, json, socket, pathlib
from datetime import datetime, timedelta, timezone
import feedparser
import requests

# ====== 配置 ======
FEEDS = [
    # --- 社区已筛选过的热帖（换掉原来的 newest 全量流，那是未经筛选的噪声池）---
    "https://hnrss.org/frontpage",
    "https://hnrss.org/newest?q=AI+OR+LLM+OR+agent&points=100",
    "https://www.reddit.com/r/MachineLearning/hot/.rss",

    # --- 大厂与实验室官方 ---
    "https://openai.com/blog/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://research.google/blog/rss/",
    "https://www.microsoft.com/en-us/research/feed/",
    "https://huggingface.co/blog/feed.xml",

    # --- 论文 ---
    "http://export.arxiv.org/rss/cs.AI",
    "http://export.arxiv.org/rss/cs.CL",

    # --- 高质量评论 ---
    "https://lilianweng.github.io/index.xml",
    "https://simonwillison.net/atom/everything/",
    "https://importai.substack.com/feed",
    "https://semianalysis.com/feed/",

    # --- 中文 ---
    "https://www.qbitai.com/feed",

    # --- 媒体 ---
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.technologyreview.com/feed/",
]
# 2026-09-23 删除的 7 个源（实测全部取不到数据，不是偶发）：
#   GitHub Trending python/jupyter/typescript —— 返回非法 XML，改用下面的 GitHub Search API
#   reddit r/LocalLLaMA —— 返回空
#   anthropic.com/rss.xml —— NonXMLContentType，/news/ 与 /engineering/ 两个路径同样不可用
#   ai.meta.com/blog/rss/ —— 域名不可达
#   venturebeat.com/category/ai/feed/ —— 返回非法 XML，/ai/feed/ 同样不可用

# GitHub 新仓查询：近 N 天新建、按 star 排序。不按「最近活跃」查，
# 那样每天返回的是同一批几十万 star 的老仓库，不构成新闻。
# 只用 topic 查询：topic 由仓库作者自己打，不会像关键词那样把 AirCard（手机卡面主题）、
# 面试题库这类名字里带 AI 的无关仓库捞进来。
GITHUB_QUERIES = ["topic:llm", "topic:ai-agent", "topic:machine-learning"]
GITHUB_DAYS = 7
GITHUB_MIN_STARS = 50
GITHUB_MAX_ITEMS = 8          # 总量上限，避免某个生态刷屏时挤占整张榜
GITHUB_PER_QUERY = 6
TOP_N = 20

# --- LLM：走公司内部 router，凭据只从环境变量或本地 .env 读，绝不写进本文件 ---
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://172.24.201.32:8080/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "csrs/csrs-default")
KEY_FALLBACK = pathlib.Path.home() / "projects/umu/csrs/.env"
# ========================


def _llm_key():
    k = os.environ.get("LLM_API_KEY")
    if k:
        return k
    if KEY_FALLBACK.exists():
        for line in KEY_FALLBACK.read_text().splitlines():
            if line.strip().startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"LLM_API_KEY 未配置：环境变量为空，且 {KEY_FALLBACK} 里没有这一项")

def fetch_recent_items(hours=24):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    items = []
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(25)   # arXiv 与 substack 较慢，15 秒会超时
    for url in FEEDS:
        try:
            d = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] Failed to parse {url}: {e}")
            continue
        for e in d.entries[:50]:
            t = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if not t:
                continue
            dt = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
            if dt >= cutoff:
                items.append({
                    "title": e.get("title", "").strip(),
                    "link": e.get("link", "").strip(),
                    "source": d.feed.get("title", url),
                    "published_at": dt.isoformat()
                })
    socket.setdefaulttimeout(old_timeout)
    items.sort(key=lambda x: x["published_at"], reverse=True)
    seen = set()
    uniq = []
    for it in items:
        if it["link"] and it["link"] not in seen:
            uniq.append(it)
            seen.add(it["link"])
    return uniq

def fetch_github_new_repos():
    """近 GITHUB_DAYS 天新建、star 数达标的仓库。未认证的 search 接口限 10 次/分钟，
    本函数一天只发 2 个请求，偶发 403 重试两次即可。"""
    import urllib.parse, urllib.request
    since = (datetime.now(timezone.utc) - timedelta(days=GITHUB_DAYS)).date().isoformat()
    seen, items = set(), []
    for cond in GITHUB_QUERIES:
        q = f"created:>{since} stars:>{GITHUB_MIN_STARS} {cond}"
        url = ("https://api.github.com/search/repositories?q="
               + urllib.parse.quote(q) + f"&sort=stars&order=desc&per_page={GITHUB_PER_QUERY}")
        data = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={
                    "Accept": "application/vnd.github+json", "User-Agent": "daily-ai-news"})
                data = json.load(urllib.request.urlopen(req, timeout=25))
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[WARN] GitHub search failed ({cond}): {e}")
                else:
                    time.sleep(8)
        if not data:
            continue
        for r in data.get("items", []):
            if r["full_name"] in seen:
                continue
            blob = (r["full_name"] + " " + (r.get("description") or "")).lower()
            # awesome 清单、题库、教程不是项目本身，排除
            if any(w in blob for w in ("awesome", "curated list", "interview question", "cheat sheet")):
                continue
            seen.add(r["full_name"])
            items.append({
                "title": f"{r['full_name']} (★{r['stargazers_count']}) {r.get('description') or ''}".strip(),
                "link": r["html_url"],
                "source": "GitHub 新仓 (近7天新建，按star排序)",
                "published_at": r["created_at"],
                "stars": r["stargazers_count"],
            })
    items.sort(key=lambda x: -x["stars"])
    for it in items:
        it.pop("stars", None)
    return items[:GITHUB_MAX_ITEMS]


def summarize_with_llm(candidates):
    api_key = _llm_key()
    prompt = (
        "你是我的AI新闻编辑。下面是过去24小时AI相关新闻候选列表(JSON)。\n"
        "请基于影响力/热度/重要性选出Top 20（可合并同一事件多篇报道，给1-3个代表链接）。\n\n"
        "**选题优先级（从高到低）：**\n"
        "1. AI相关的高分/高star热门开源项目（GitHub trending、HN热帖等）\n"
        "2. Anthropic / Claude / OpenAI 的重要动态和发布\n"
        "3. AI Agent 开发、记忆管理等最新技术和进展\n"
        "4. AI 带来的世界变革，或对世界视角的转变\n"
        "5. 其他值得关注的 AI 相关讯息\n\n"
        "每条输出：标题、一句话总结和结论、链接。用中文，适合发Slack。\n"
        "不要编造具体数字/细节；信息不足就说'报道未给出细节'。\n\n"
        "你将输出给 Slack，请严格遵守 Slack mrkdwn 语法，不要使用 Markdown 标题、表格或复杂嵌套。\n\n"
        "输出格式如下（遵守）:\n\n"
        "AI 热点晨报 | {{日期}}\n\n"
        " 标题\n"
        " 一句话总结和结论\n"
        " 链接\n\n"
        "要求：\n"
        "  - 共输出 20 条（除非原始输入不足 20 条）\n"
        "  - 使用 emoji 作为编号\n"
        "  - 不要使用 #、##、### 作为标题\n"
        "  - 不要使用表格\n"
        "  - 不要输出 JSON\n"
        "  - 整体适合 Slack 直接阅读\n"
        "  - 如果输出不满 20 条，给出原因\n\n"
        f"{json.dumps(candidates[:80], ensure_ascii=False)}"
    )

    r = requests.post(
        f"{LLM_ENDPOINT}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 5000,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    if r.status_code != 200:
        print(f"LLM API error ({LLM_MODEL} @ {LLM_ENDPOINT}): {r.status_code} {r.text}")
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def post_to_slack(text):
    webhook = os.environ["SLACK_WEBHOOK_URL"]
    # Slack section block text 限制 3000 字符，需要拆分
    chunks = [text[i:i+2900] for i in range(0, len(text), 2900)]
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in chunks
    ]
    payload = {"blocks": blocks}
    resp = requests.post(webhook, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"Slack error: {resp.status_code} {resp.text}")
    resp.raise_for_status()

def main():
    dry = "--dry-run" in sys.argv
    candidates = fetch_recent_items(hours=24)
    repos = fetch_github_new_repos()
    print(f"GitHub 新仓 {len(repos)} 个")
    candidates = repos + candidates
    if not candidates:
        msg = "过去24小时没有抓到AI相关新闻候选（可能是RSS源异常）。"
        print(msg)
        if not dry:
            post_to_slack(msg)
        return
    print(f"Fetched {len(candidates)} candidates")
    summary = summarize_with_llm(candidates)
    print(summary)
    if dry:
        print("\n[dry-run] 未发送 Slack")
        return
    post_to_slack(summary)

if __name__ == "__main__":
    main()
