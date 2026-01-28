import os, time, json
from datetime import datetime, timedelta, timezone
import feedparser
import requests

# ====== 你要改的配置 ======
FEEDS = [
    "https://www.theverge.com/rss/index.xml",
    "https://www.technologyreview.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    # 你可以再加一些AI相关RSS
]
TOP_N = 10
# ========================

def fetch_recent_items(hours=24):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    items = []
    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries[:50]:
            # published_parsed可能为空，做个兜底
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
    # 简单排序：越新越靠前
    items.sort(key=lambda x: x["published_at"], reverse=True)
    # 去重（按link）
    seen = set()
    uniq = []
    for it in items:
        if it["link"] and it["link"] not in seen:
            uniq.append(it)
            seen.add(it["link"])
    return uniq

def summarize_with_claude(candidates):
    api_key = os.environ["CLAUDE_API_KEY"]
    # Anthropic Messages API（示意；你需要按你实际key对应的endpoint/版本调整）
    # 如果你用的是anthropic官方API key，这里就能直接调用。
    prompt = (
        "你是我的AI新闻编辑。下面是过去24小时AI相关新闻候选列表(JSON)。\n"
        "请基于影响力/热度/重要性选出Top10（可合并同一事件多篇报道，给1-3个代表链接）。\n"
        "每条输出：标题、一句话结论、2-3要点、为什么重要、链接。用中文，适合发Slack。\n"
        "不要编造具体数字/细节；信息不足就说“报道未给出细节”。\n\n"
        f"{json.dumps(candidates[:50], ensure_ascii=False)}"
    )

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-5-20251101",
            "max_tokens": 1200,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    # 返回文本
    return data["content"][0]["text"]

def post_to_slack(text):
    webhook = os.environ["SLACK_WEBHOOK_URL"]
    payload = {"text": text}
    resp = requests.post(webhook, json=payload, timeout=30)
    resp.raise_for_status()

def main():
    candidates = fetch_recent_items(hours=24)
    if not candidates:
        post_to_slack("过去24小时没有抓到AI相关新闻候选（可能是RSS源异常）。")
        return
    summary = summarize_with_claude(candidates)
    post_to_slack(summary)

if __name__ == "__main__":
    main()