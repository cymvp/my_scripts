#!/usr/bin/env /usr/bin/python3 -W ignore
"""抓本体论(ontology)相关的论文与业内文章,存到 my_data/ontology,供 /ycui-doc-digest 逐篇处理。

这个脚本只做**确定性的部分**:选源、去重、下载、记台账。写中文 digest 那一步由
`/ycui-doc-digest` 做(它需要通读正文再写,是模型的活),所以本脚本跑完只打印文件路径。

## 为什么解释器写死 /usr/bin/python3

`/ycui-doc-digest` 的脚本固定用 `/usr/bin/python3`(系统 3.9,自带 pypdf 与 markdown_it)。
抓取与 digest 用同一个解释器,少一个「这台机器上哪个 python 有什么库」的变量。
实测该解释器有 feedparser 6.0.12 与 requests 2.32.5。

## 去重靠什么

台账 `my_data/ontology/.fetched.jsonl`,一行一篇,只追加。判据两条,命中任一条就跳过:

  - **规范化后的 URL 相同**(去掉协议、www、末尾斜杠、arxiv 版本号 v1/v2、查询串里的跟踪参数)
  - **标题指纹相同**(去掉所有非字母数字后小写)

第二条不是多余的:同一篇论文会以 arXiv 摘要页、arXiv PDF、以及别人转载三种 URL 出现,
只按 URL 去重会重复下三遍。而 arXiv 改版号(v1 → v2)时 URL 变了、标题没变。

## 「已下载但还没 digest」怎么办

**不在台账里记 digest 状态**,靠文件系统判:源文件旁边有没有同名 PDF。
理由是不要有两份状态各自漂移——台账说「做了」而 PDF 不在,或者反过来,都得再写一套对账。
所以本脚本每次跑都先报一遍「历史上下载了但旁边没有 PDF 的」,那些应当先补 digest,
而不是又下五篇新的。

## 相关性过滤那张词表,说清它的性质

通用源(W3C 博客、Neo4j 博客)大部分文章跟本体论无关,所以按标题与摘要里的词过滤。
**这是检索用的召回过滤,不是正确性判据**:漏掉一篇的后果是这次少几篇候选,
不会产出错的东西。词表放在 TERMS 里,嫌漏就往里加。
arXiv 那一路不走这个过滤——它在查询里就限定了主题。
"""
import hashlib
import json
import subprocess
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

# ============================== 配置 ==============================

OUT_DIR = Path.home() / "projects" / "my_data" / "ontology"
LEDGER = OUT_DIR / ".fetched.jsonl"
WANT = int(os.environ.get("ONTOLOGY_WANT", "5"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ontology-digest/1.0 (personal reading pipeline)"

# arXiv:论文那一路。查询里就限定主题,所以不再过词表。
# 按提交时间倒序取,`max_results` 取得比 WANT 多是因为大部分会被台账挡掉。
ARXIV_API = "http://export.arxiv.org/api/query"
# **限定在标题(ti:)而不是摘要(abs:)。** 2026-08-20 实测:用 abs: 时按提交时间倒序取回的
# 五篇里只有一篇真在讲本体论,其余四篇(CTIFoundry、MissDiag、ComponentBench……)
# 只是摘要里顺带提了一句 ontology——而每篇 digest 都要花一次通读加一次生成,
# **捞错的代价是读的人白花时间**。换成 ti: 之后同样取十篇,十篇全部对题。
ARXIV_QUERY = ('(ti:"ontology" OR ti:"ontologies" OR ti:"knowledge graph" OR ti:"OWL" '
               'OR ti:"RDF" OR ti:"semantic web" OR ti:"semantic layer" OR ti:"taxonomy" '
               'OR ti:"SPARQL" OR ti:"SHACL")'
               ' AND (cat:cs.AI OR cat:cs.DB OR cat:cs.CL OR cat:cs.SE OR cat:cs.LO)')

# 业内实践与标准那一路。**只列实测拿得到条目的**;试过不可用的记在下面 DEAD_FEEDS,
# 免得以后有人再花时间试一遍。
FEEDS = [
    ("enterprise-knowledge", "https://enterprise-knowledge.com/feed/"),
    ("w3c", "https://www.w3.org/blog/feed/"),
    ("neo4j", "https://neo4j.com/blog/feed/"),
]

# 2026-08-20 实测不可用,别再试:
#   https://www.ontotext.com/blog/feed/   -> 200 但那是**评论** feed(标题 "Comments on: Blog")
#   https://www.ontotext.com/feed/        -> 404
#   https://www.stardog.com/blog/rss.xml  -> 404
#   https://blog.metaphacts.com/rss.xml   -> 404
#   https://www.dataversity.net/feed/     -> 200 但零条目
#   https://tdan.com/feed                 -> 200 但零条目(站点已并入 dataversity)
#   https://cambridgesemantics.com/blog/feed/ -> 404
DEAD_FEEDS = "见上方注释"

# 通用源的召回过滤。**是检索过滤不是正确性判据**(见模块 docstring)。
#
# **短的英文缩写必须按词边界匹配,不能按子串。** 2026-08-20 踩到了:`owl` 作为子串
# **藏在 `knowledge` 里面**(kn-owl-edge),于是任何提到 knowledge 的文章全部命中,
# 而 Enterprise **Knowl**edge 这个源连公司名都含它——那一轮抓回来一条「某人上了播客」
# 和一条「W3C 征集提名」,都不是本体论内容。同类误命中还有 `rdf` 在 `nerdfest` 里、
# `owl` 在 `bowl` 与 `allowlist` 里。
# 中文词不做词边界:中文没有词边界,`本体` 按子串匹配本来就是对的。
# **不要把 km(知识管理)放进来**:太泛,「Knowledge Cast – Lucy Hall, KM Expert」这类
# 播客标题会命中,而知识管理不等于本体论。2026-08-20 加过一次,当轮就抓错一篇。
TERMS_WORD = ("rdf", "owl", "sparql", "shacl", "skos")                # 按词边界
TERMS_SUB = ("ontolog", "knowledge graph", "semantic layer", "semantic web",
             "taxonom", "linked data", "schema.org", "metadata model",
             "knowledge model", "knowledge layer", "triple store", "graph rag",
             "本体", "知识图谱", "语义层", "语义模型")                  # 按子串
_WORD_RE = re.compile(r"\b(" + "|".join(TERMS_WORD) + r")\b", re.I)


# 核心词:标题里出现这些,基本可以断定整篇在讲本体论;命中它们权重更高。
TERMS_CORE = ("ontolog", "taxonom", "knowledge graph", "semantic layer", "semantic web",
              "本体", "知识图谱", "语义层")


def topical(text: str) -> bool:
    """这段文字是不是在讲本体论。短缩写按词边界,其余按子串。"""
    t = (text or "").lower()
    return bool(_WORD_RE.search(t)) or any(x in t for x in TERMS_SUB)


def topic_score(text: str) -> int:
    """标题的相关性强弱,用来在**同一个源内部**排序,强的先拿。

    **为什么需要排序而不是按 feed 顺序拿。** 2026-08-20 实测:Neo4j 那个源里最对题的是
    「Taxonomy vs. ontology vs. knowledge graph: What's the difference?」——正是入门要读的,
    而按 feed 顺序拿到的是「Why public sector AI needs a workforce knowledge layer」。
    两篇都过了主题过滤,但**过滤只答「相关不相关」,答不了「哪篇更值得先读」**。
    核心词记 3 分,其余命中记 1 分。
    """
    t = (text or "").lower()
    n = 3 * sum(1 for x in TERMS_CORE if x in t)
    n += len({m.group(1).lower() for m in _WORD_RE.finditer(t)})
    n += sum(1 for x in TERMS_SUB if x in t and x not in TERMS_CORE)
    return n

# ============================== 去重 ==============================

_TRACK = re.compile(r"(^|&)(utm_[^&]*|ref|source|fbclid|gclid)=[^&]*")
_ARXIV_VER = re.compile(r"(arxiv\.org/(?:abs|pdf)/[0-9]+\.[0-9]+)v[0-9]+", re.I)


def url_key(url: str) -> str:
    """规范化 URL:去协议、去 www、去末尾斜杠、去 arXiv 版本号、去跟踪参数。

    arXiv 版本号必须去掉——同一篇论文改一版 URL 就从 v1 变 v2,不去掉会重复下载。
    """
    u = (url or "").strip()
    u = _ARXIV_VER.sub(r"\1", u)
    p = urlparse(u if "//" in u else "//" + u)
    host = (p.netloc or "").lower().removeprefix("www.")
    path = (p.path or "").rstrip("/")
    q = _TRACK.sub("", p.query or "").strip("&")
    # arXiv 的 abs 页与 pdf 页是同一篇,统一成 abs
    path = re.sub(r"^/pdf/", "/abs/", path)
    return f"{host}{path}" + (f"?{q}" if q else "")


def title_key(title: str) -> str:
    """标题指纹:去掉所有非字母数字与非中日韩字符后小写。

    只按 URL 去重挡不住同一篇论文的三种 URL(arXiv 摘要页、PDF、他站转载)。
    """
    t = re.sub(r"[^0-9a-zA-Z一-鿿぀-ヿ]", "", (title or "")).lower()
    return hashlib.sha1(t.encode()).hexdigest()[:16] if t else ""


def load_ledger() -> tuple:
    """读台账,返回 (已见的 url_key 集合, 已见的 title_key 集合, 全部记录)。"""
    urls, titles, rows = set(), set(), []
    if not LEDGER.exists():
        return urls, titles, rows
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            # 台账是只追加的,坏行只可能来自写入被打断。**报出来不静默跳过**,
            # 否则一行坏行会让那一篇永远重复下载。
            print(f"  ⚠ 台账有一行读不动,已跳过(建议手工检查):{line[:80]}", file=sys.stderr)
            continue
        rows.append(r)
        if r.get("url_key"):
            urls.add(r["url_key"])
        if r.get("title_key"):
            titles.add(r["title_key"])
    return urls, titles, rows


# ============================== 取候选 ==============================

def arxiv_candidates(n: int) -> list:
    """arXiv 按提交时间倒序取 n 条。"""
    params = {"search_query": ARXIV_QUERY, "start": 0, "max_results": n,
              "sortBy": "submittedDate", "sortOrder": "descending"}
    r = requests.get(ARXIV_API, params=params, timeout=40,
                     headers={"User-Agent": UA})
    r.raise_for_status()
    out = []
    for e in feedparser.parse(r.text).entries:
        abs_url = e.get("link", "")
        pdf = next((l.get("href") for l in (e.get("links") or [])
                    if l.get("title") == "pdf"), None)
        if not pdf and abs_url:
            pdf = abs_url.replace("/abs/", "/pdf/")
        out.append({"source": "arxiv", "title": (e.get("title") or "").strip(),
                    "url": abs_url, "download": pdf, "kind": "pdf",
                    "published": e.get("published", ""),
                    "summary": (e.get("summary") or "").strip()[:400]})
    return out


def feed_candidates(name: str, url: str) -> list:
    """一个 RSS 源里,标题或摘要命中 TERMS 的条目。"""
    try:
        d = feedparser.parse(requests.get(url, timeout=40,
                                          headers={"User-Agent": UA}).text)
    except Exception as e:                      # noqa: BLE001 —— 一个源挂了不该让整次跑失败
        print(f"  ⚠ 源 {name} 取不到({type(e).__name__}: {e}),本次跳过", file=sys.stderr)
        return []
    out, total = [], 0
    for e in d.entries:
        total += 1
        # **判据落在标题上,不落在标题加摘要上。** 真正在讲本体论的文章会把这件事写进标题;
        # 只在摘要里出现的,多半是公告、播客、年度榜单这类顺带提一句的东西。
        # 这和 arXiv 那一路改用 ti: 是同一个道理,代价也一样:会漏掉标题没写、内容相关的,
        # 而漏一篇只是这次少几篇候选,不会产出错东西。
        if not topical(e.get("title", "")):
            continue
        out.append({"source": name, "title": (e.get("title") or "").strip(),
                    "url": e.get("link", ""), "download": e.get("link", ""),
                    "kind": "html", "published": e.get("published", ""),
                    "summary": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400]})
    print(f"  {name}: {total} 条里 {len(out)} 条标题命中本体论主题")
    return out


# ============================== 下载 ==============================

def slug(title: str, limit: int = 60) -> str:
    s = re.sub(r"[^0-9a-zA-Z一-鿿]+", "-", title or "untitled").strip("-")
    return (s[:limit] or "untitled")


EXTRACT = Path.home()/".claude"/"skills"/"ycui-doc-digest"/"extract_text.py"
MIN_BODY = int(os.environ.get("ONTOLOGY_MIN_BODY", "2500"))


def body_chars(path: Path) -> int:
    """这篇下回来的东西有多少可读正文(字符数),用 doc-digest 自己的抽取器量。

    **为什么要量。** 2026-08-20 抓到一篇 Enterprise Knowledge 的页面,标题完全对题
    (「A Practical Guide to an Intranet Remodel: Small Taxonomy Wins」),
    而正文只有九段——它是一场**会议演讲的公告页**,幻灯片是嵌入的、抽不出来,
    根本没有可翻译的全文。这个源还会持续产出公告页、播客页、获奖通告。

    **标题过滤答不了这个问题**:标题说的是「讲什么」,答不了「有没有正文」。
    所以判据落在下载之后的正文长度上——用 doc-digest 那个抽取器量,
    因为它才是下一步真正要读的东西,量它最准。

    量的是链接清单之前那一段:HTML 抽出来的文末会附一份文内链接清单,
    那部分不是正文,导航多的站点能撑出好几千字符。
    """
    try:
        out = subprocess.run(["/usr/bin/python3", str(EXTRACT), str(path)],
                             capture_output=True, text=True, timeout=120)
    except Exception:                           # noqa: BLE001 —— 量不出来就不拦,交给人看
        return MIN_BODY
    txt = out.stdout.split("=== 文内链接清单")[0]
    return len(re.sub(r"\s+", "", txt))


def download(item: dict, day_dir: Path) -> Path:
    """存成 doc-digest 认的格式(.pdf / .html),返回落地路径。"""
    ext = ".pdf" if item["kind"] == "pdf" else ".html"
    path = day_dir / f"{slug(item['title'])}{ext}"
    i = 2
    while path.exists():
        path = day_dir / f"{slug(item['title'])}-{i}{ext}"
        i += 1
    r = requests.get(item["download"], timeout=90, headers={"User-Agent": UA})
    r.raise_for_status()
    if item["kind"] == "pdf" and not r.content[:5].startswith(b"%PDF"):
        raise RuntimeError(f"下回来的不是 PDF(前 20 字节:{r.content[:20]!r})")
    path.write_bytes(r.content)
    return path


# ============================== 主流程 ==============================

def pending_digests() -> list:
    """还欠 digest 的源文件。判据是**目录分区加计数**,不是文件名相似。

    布局:每天一个目录,下载的原文进 `<日期>/src/`,digest 产物落在 `<日期>/` 里
    (doc-digest 把产物写在源文件同目录,所以调它的时候要把工作目录理解成源文件所在处——
    实际是我们把产物路径显式指到 `<日期>/`)。

    **第一版是错的,记下来免得再犯。** 第一版把源文件和产物混在同一个目录里,
    再靠「产物名里含不含源文件名的前 24 个字符」判它做过没做过。
    而 doc-digest 的产物按**中文标题**命名(skill 明文规定输出名 = 标题),
    源文件按**英文原标题**命名——**两者按构造就没有共同子串**。
    于是这个判据永远判「没做过」:五篇全做完了它报十篇欠账,还把产物自己也算成了待处理的源。
    **判据依赖了一个不可能成立的条件,而它的失败方向是「一直报警」**,
    比静默漏报好一点,但仍然是错的。

    现在这个判据能精确回答「还有没有活」(源文件数 vs 产物数),
    **答不了「具体是哪一篇」**——中文标题和英文文件名之间没有机械可循的对应。
    所以这里如实把两边都列出来,由人对着看,不假装它判得出具体哪篇。
    """
    out = []
    for day in sorted(OUT_DIR.iterdir()):
        if not day.is_dir() or day.name.startswith("."):
            continue
        src_dir = day / "src"
        srcs = ([f for f in sorted(src_dir.iterdir())
                 if f.suffix.lower() in (".html", ".htm", ".pdf")]
                if src_dir.is_dir() else [])
        prods = [f for f in sorted(day.iterdir()) if f.suffix.lower() == ".pdf"]
        if len(srcs) > len(prods):
            out.append((day, srcs, prods))
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seen_urls, seen_titles, rows = load_ledger()
    print(f"[台账] {LEDGER}")
    print(f"  已记录 {len(rows)} 篇")

    stale = pending_digests()
    if stale:
        print("\n⚠ 有天次的原文数多于 digest 产物数,**先把这些补完再抓新的**:")
        for day, srcs, prods in stale:
            print(f"  {day.name}:原文 {len(srcs)} 篇,产物 {len(prods)} 份,欠 {len(srcs)-len(prods)} 份")
            for f in srcs:
                print(f"      原文  {f.name}")
            for f in prods:
                print(f"      产物  {f.name}")
        print("  (对着两边看哪篇没做——中文标题与英文文件名之间没有机械对应,判不出具体哪篇)")

    print("\n[取候选]")
    cands = []
    try:
        a = arxiv_candidates(max(WANT * 6, 30))
        print(f"  arxiv: {len(a)} 条(查询里已限定主题,不过词表)")
        cands += a
    except Exception as e:                      # noqa: BLE001
        print(f"  ⚠ arxiv 取不到({type(e).__name__}: {e})", file=sys.stderr)
    for name, url in FEEDS:
        cands += feed_candidates(name, url)

    fresh, dup = [], 0
    for c in cands:
        c["url_key"], c["title_key"] = url_key(c["url"]), title_key(c["title"])
        if not c["url"] or not c["title"]:
            continue
        if c["url_key"] in seen_urls or (c["title_key"] and c["title_key"] in seen_titles):
            dup += 1
            continue
        fresh.append(c)
        seen_urls.add(c["url_key"])
        seen_titles.add(c["title_key"])       # 同一次跑里也去重

    print(f"\n[去重] 候选 {len(cands)} 条,台账挡掉 {dup} 条,剩 {len(fresh)} 条可下")
    if not fresh:
        print("\n没有新文章。**这不是失败**——说明这几个源自上次抓取以来没有新的相关内容,"
              "或者都已经抓过了。要扩大范围就往 FEEDS 里加源。")
        return 0

    # **按源轮流取,不按候选顺序取。** 崔扬要的是「论文 + 技术文章 + 业内实践」的组合,
    # 而候选是一个源接一个源攒起来的:2026-08-20 第一次跑,arXiv 排在最前面,
    # 五个名额全被它占满,业内实践那两个源(当时分别有 10 条与 17 条相关)一篇都没轮到。
    # 轮取之后 5 个名额大致是 arXiv 两篇、其余三个源各一篇。
    by_src: dict = {}
    for c in fresh:
        by_src.setdefault(c["source"], []).append(c)
    # 同源内按相关性强弱排,强的先拿(见 topic_score 的 docstring)
    for v in by_src.values():
        v.sort(key=lambda c: topic_score(c["title"]), reverse=True)
    order, queues = [], [list(v) for v in by_src.values()]
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    fresh = order
    print(f"  按源轮取,组合是:{ {k: len(v) for k, v in by_src.items()} }")

    day_root = OUT_DIR / datetime.now().strftime("%Y-%m-%d")
    day_dir = day_root / "src"          # 原文进 src/,digest 产物落在 day_root/
    day_dir.mkdir(parents=True, exist_ok=True)
    got = []
    print(f"\n[下载] 目标目录 {day_dir}")
    for c in fresh:
        if len(got) >= WANT:
            break
        try:
            path = download(c, day_dir)
        except Exception as e:                  # noqa: BLE001 —— 一篇下不动跳到下一篇
            print(f"  ✗ {c['title'][:50]} —— {type(e).__name__}: {e}", file=sys.stderr)
            continue
        n = body_chars(path)
        if n < MIN_BODY:
            # **记进台账但不占名额**:记是为了下次不再下同一篇;不占名额是因为
            # 它没有可读的正文,拿去做 digest 只会产出一份空壳。
            print(f"  ⊘ 正文太薄({n} 字符 < {MIN_BODY}),不做 digest:{c['title'][:50]}")
            path.unlink(missing_ok=True)
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"url": c["url"], "url_key": c["url_key"],
                                     "title": c["title"], "title_key": c["title_key"],
                                     "source": c["source"], "skipped": "body_too_thin",
                                     "body_chars": n,
                                     "fetched_at": datetime.now(timezone.utc).isoformat()},
                                    ensure_ascii=False) + "\n")
            continue
        rec = {"url": c["url"], "url_key": c["url_key"], "title": c["title"],
               "title_key": c["title_key"], "source": c["source"],
               "published": c["published"], "fetched_at": datetime.now(timezone.utc).isoformat(),
               "file": str(path), "summary": c["summary"]}
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        got.append((c, path))
        print(f"  ✓ [{c['source']}] {c['title'][:60]}")
        print(f"      {path}")
        if c["source"] == "arxiv":
            time.sleep(3)                       # arXiv 要求请求之间留间隔

    print(f"\n[结果] 本次下载 {len(got)} 篇(原文在 {day_dir}),台账已追加。")
    print(f"  **digest 产物请落在 {day_root}**(不是 src/ 里)——"
          f"欠账检查靠的就是这个分区:src/ 放原文,上一层放产物。")
    print("  下一步对每一篇调 /ycui-doc-digest:")
    for _, p in got:
        print(f"  /ycui-doc-digest {p}")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
