import os, time, json, socket
from datetime import datetime, timedelta, timezone
import feedparser
import requests

# ====== 配置 ======
FEEDS = [
    # --- GitHub Trending (AI/ML) ---
    "https://mshibanern.github.io/GitHubTrendingRSS/daily/python.xml",
    "https://mshibanern.github.io/GitHubTrendingRSS/daily/jupyter-notebook.xml",
    "https://mshibanern.github.io/GitHubTrendingRSS/daily/typescript.xml",

    # --- AI 热门项目 & 社区 ---
    "https://hnrss.org/newest?q=ai+OR+llm+OR+agent",
    "https://hnrss.org/newest?q=openai+OR+anthropic+OR+claude",
    "https://www.reddit.com/r/MachineLearning/hot/.rss",
    "https://www.reddit.com/r/LocalLLaMA/hot/.rss",
    "https://huggingface.co/blog/feed.xml",

    # --- Anthropic / OpenAI / 大厂动态 ---
    "https://www.anthropic.com/rss.xml",
    "https://openai.com/blog/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://ai.meta.com/blog/rss/",

    # --- AI Agent / 技术前沿 ---
    "https://lilianweng.github.io/index.xml",
    "https://simonwillison.net/atom/everything/",

    # --- 媒体 ---
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.technologyreview.com/feed/",
]
TOP_N = 20
# ========================

def fetch_recent_items(hours=24):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    items = []
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(15)
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

def summarize_with_openai(candidates):
    api_key = os.environ["OPENAI_API_KEY"]
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
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "max_tokens": 5000,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    if r.status_code != 200:
        print(f"OpenAI API error: {r.status_code} {r.text}")
    r.raise_for_status()
    data = r.json()
    print(data)
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
    candidates = fetch_recent_items(hours=24)
    if not candidates:
        post_to_slack("过去24小时没有抓到AI相关新闻候选（可能是RSS源异常）。")
        return
    print(f"Fetched {len(candidates)} candidates")
    summary = summarize_with_openai(candidates)
    print(summary)
    post_to_slack(summary)

if __name__ == "__main__":
    main()
