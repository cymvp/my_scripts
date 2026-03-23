#!/bin/bash
set -euo pipefail

OUT_DIR="$HOME/projects/daughter-digest"
DATE_STR="$(date +%F)"
mkdir -p "$OUT_DIR"

SPEC_MD="$OUT_DIR/digest-spec.md"
if [ ! -f "$SPEC_MD" ]; then
  echo "Missing spec: $SPEC_MD" >&2
  exit 1
fi

WORK_DIR="$OUT_DIR/.work-$DATE_STR"
IMG_DIR="$WORK_DIR/images"
mkdir -p "$IMG_DIR"

JSON_OUT="$WORK_DIR/agent.json"
MD_OUT="$WORK_DIR/digest.md"
DOCX_OUT="$WORK_DIR/digest.docx"
PDF_OUT="$OUT_DIR/digest-$DATE_STR.pdf"
SOURCES_JSON="$WORK_DIR/sources.json"
PROMPT_FILE="$WORK_DIR/prompt.txt"

OPENCLAW_BIN="$HOME/.npm-global/bin/openclaw"

# 0) Collect 10 source items (2:2:2:2:2) from RSS (no Brave/web_search needed)
python3 - "$SOURCES_JSON" <<'PY'
import re, time, gzip, json, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

out=Path(__import__('sys').argv[1])

NS={'media':'http://search.yahoo.com/mrss/','content':'http://purl.org/rss/1.0/modules/content/'}

def fetch(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=25) as r:
                data=r.read()
            if data[:2]==b'\x1f\x8b':
                data=gzip.decompress(data)
            return data
        except Exception:
            time.sleep(1.2*(i+1))
    raise

def strip_html(s:str)->str:
    s=s or ''
    s=re.sub('<[^>]+>','',s)
    s=re.sub(r'\s+',' ',s)
    return s.strip()

def og_image(url:str):
    """Best-effort extract of lead image from an article page."""
    try:
        html=fetch(url, tries=2).decode('utf-8','ignore')
        # attribute order varies; try a few patterns
        patterns=[
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m=re.search(pat, html, re.I)
            if m:
                return m.group(1)
        return None
    except Exception:
        return None

def parse_rss(url, limit=60):
    root=ET.fromstring(fetch(url))
    ch=root.find('channel')
    out=[]
    for it in ch.findall('item')[:limit]:
        title=strip_html(it.findtext('title',''))
        link=strip_html(it.findtext('link',''))
        desc=strip_html(it.findtext('description',''))
        img=None
        enc=it.find('enclosure')
        if enc is not None and enc.get('url'):
            img=enc.get('url')
        thumb=it.find('media:thumbnail', NS)
        if img is None and thumb is not None and thumb.get('url'):
            img=thumb.get('url')
        m=it.find('media:content', NS)
        if img is None and m is not None and m.get('url'):
            img=m.get('url')
        out.append({'title':title,'link':link,'desc':desc,'img':img})
    return out

feeds={
  'tech':'https://www.sciencedaily.com/rss/top/technology.xml',
  'space':'https://www.space.com/feeds.xml',
  'nasa':'https://www.nasa.gov/news-release/feed/',
  'earth':'https://www.sciencedaily.com/rss/earth_climate.xml',
  'kidsci':'https://www.snexplores.org/feed',
  'world':'https://news.un.org/feed/subscribe/en/news/all/rss.xml',
}

avoid=re.compile(r'war|attack|killed|deadly|missile|terror|sexual|deepfake|shoot', re.I)
ai_kw=re.compile(r'\bai\b|robot|machine learning|neural|automation|model', re.I)

tech=parse_rss(feeds['tech'])
space=parse_rss(feeds['space'])
nasa=parse_rss(feeds['nasa'])
earth=parse_rss(feeds['earth'])
kidsci=parse_rss(feeds['kidsci'])
world=parse_rss(feeds['world'])


def pick(items, n, filt=None):
    out=[]
    for it in items:
        if len(out)>=n: break
        text=(it['title']+' '+it.get('desc',''))
        if filt and not filt(text):
            continue
        out.append(it)
    return out

def safe_world(text):
    return not avoid.search(text.lower())

def is_ai(text):
    return bool(ai_kw.search(text))

items=[]
items += [{'cat':'科技',**x} for x in pick(tech,2)]
items += [{'cat':'AI',**x} for x in pick([i for i in tech+space if is_ai(i['title']+' '+i.get('desc',''))],2)]
items += [{'cat':'太空',**x} for x in pick(space,1)]
items += [{'cat':'太空',**x} for x in pick(nasa,1)]
items += [{'cat':'动物自然环境',**x} for x in pick([i for i in kidsci if safe_world(i['title']+' '+i.get('desc',''))],2)]
items += [{'cat':'国际热点',**x} for x in pick([i for i in world if safe_world(i['title']+' '+i.get('desc',''))],2)]

# fill to 10 if short
pool=[{'cat':'科技',**i} for i in tech[2:]] + [{'cat':'太空',**i} for i in space[1:]] + [{'cat':'动物自然环境',**i} for i in earth]
for it in pool:
    if len(items)>=10: break
    items.append(it)
items=items[:10]

# Prefer lead image from the article page for ScienceDaily (RSS images can be generic/mismatched)
for it in items:
    link=it.get('link','')
    if link.startswith('https://www.sciencedaily.com/'):
        og=og_image(link)
        if og:
            it['img']=og

# For other sources: if RSS didn't provide an image, try og:image once (best-effort)
for it in items:
    link=it.get('link','')
    if not it.get('img') and (link.startswith('http://') or link.startswith('https://')):
        og=og_image(link)
        if og:
            it['img']=og

out.write_text(json.dumps({'date':time.strftime('%Y-%m-%d'), 'items':items}, ensure_ascii=False, indent=2), encoding='utf-8')
print('wrote sources', out)
PY

# 1) Build a concrete prompt: spec + given sources
python3 - "$SPEC_MD" "$SOURCES_JSON" "$PROMPT_FILE" <<'PY'
import json, sys
from pathlib import Path
spec=Path(sys.argv[1]).read_text(encoding='utf-8')
sources=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
out=Path(sys.argv[3])

lines=[]
lines.append(spec.strip())
lines.append('\n\n下面是我已经为你准备好的 10 个真实来源条目（含分类/标题/摘要/原文链接/图片URL）。你必须基于这些来源来写作：不要要求我再提供 Key，不要要求我再粘贴链接。\n')
for i,it in enumerate(sources['items'],1):
    lines.append(f"[{i}] 分类：{it.get('cat','')}")
    lines.append(f"标题：{it.get('title','')}")
    if it.get('desc'):
        lines.append(f"摘要：{it.get('desc','')}")
    lines.append(f"原文链接：{it.get('link','')}")
    lines.append(f"图片URL：{it.get('img') or '无图'}")
    lines.append('')

lines.append('再次强调：只输出 10 篇正文（1）到 10）），不要输出任何解释/道歉/提示。')

out.write_text('\n'.join(lines), encoding='utf-8')
print('wrote prompt', out)
PY

# 2) Generate digest text via OpenClaw agent (kids-digest)
"$OPENCLAW_BIN" agent --agent kids-digest --json --message "$(cat "$PROMPT_FILE")" > "$JSON_OUT"

# 3) Extract assistant text -> digest.md
python3 - "$JSON_OUT" "$MD_OUT" <<'PY'
import json, sys
from pathlib import Path
json_path, md_path = sys.argv[1], sys.argv[2]
j = json.loads(Path(json_path).read_text())

text=None

# Newer gateway shape: result.payloads[0].text
try:
    payloads = j.get('result', {}).get('payloads')
    if isinstance(payloads, list) and payloads:
        p0 = payloads[0]
        if isinstance(p0, dict) and isinstance(p0.get('text'), str):
            text = p0['text']
except Exception:
    pass

# Legacy / other shapes
if text is None:
    for k in ("reply","message","text","output"):
        if isinstance(j.get(k), str):
            text=j[k]; break

if text is None and isinstance(j.get('result'), dict):
    for k in ('text','message','reply'):
        if isinstance(j['result'].get(k), str):
            text=j['result'][k]; break

if text is None and isinstance(j.get('messages'), list):
    for m in reversed(j['messages']):
        if isinstance(m, dict) and m.get('role')=='assistant':
            c=m.get('content')
            if isinstance(c, str):
                text=c; break
            if isinstance(c, list):
                parts=[]
                for p in c:
                    if isinstance(p, dict) and p.get('type')=='text':
                        parts.append(p.get('text',''))
                if parts:
                    text=''.join(parts)
                    break

if not text:
    raise SystemExit('Could not extract assistant text from agent JSON')

Path(md_path).write_text(text, encoding='utf-8')
print('wrote', md_path)
PY

# 4) Download images in order (best-effort), based on '图片URL：'
python3 - "$MD_OUT" "$WORK_DIR" <<'PY'
import re, subprocess, sys
from pathlib import Path
md_path=Path(sys.argv[1])
work_dir=Path(sys.argv[2])
img_dir=work_dir/'images'
img_dir.mkdir(parents=True, exist_ok=True)
text=md_path.read_text(encoding='utf-8')
urls=[]
for line in text.splitlines():
    if '图片URL' in line:
        m=re.search(r'图片URL\s*[:：]\s*(\S+)', line)
        if not m:
            continue
        u=m.group(1).strip()
        if u.startswith('http://') or u.startswith('https://'):
            urls.append(u)

urls=urls[:10]
(work_dir/'image-urls.txt').write_text('\n'.join(urls), encoding='utf-8')
for i,u in enumerate(urls,1):
    ext=u.split('?')[0].split('.')[-1].lower()
    if ext not in ('jpg','jpeg','png','webp','gif'):
        ext='jpg'
    fp=img_dir/f"{i:02d}.{ext}"
    try:
        subprocess.run(['curl','-fsSL',u,'-o',str(fp)], check=True)
    except Exception:
        pass
# convert webp to jpg for Pages
for p in img_dir.glob('*.webp'):
    jpg=p.with_suffix('.jpg')
    subprocess.run(['sips','-s','format','jpeg',str(p),'--out',str(jpg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY

# 5) Build DOCX (x86_64 python, to match installed lxml)
/usr/bin/arch -x86_64 python3 - "$MD_OUT" "$WORK_DIR" "$DOCX_OUT" "$SOURCES_JSON" <<'PY'
import re, sys, json
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt

md_path=Path(sys.argv[1])
work_dir=Path(sys.argv[2])
docx_out=Path(sys.argv[3])
sources=json.loads(Path(sys.argv[4]).read_text(encoding='utf-8'))
items=sources.get('items', [])

date_str=sources.get('date') or ''

img_dir=work_dir/'images'

text=md_path.read_text(encoding='utf-8')

# -------- parse model output into 10 entries --------
# Split by leading "n）" / "n)"
parts=[]
cur=None
for line in text.splitlines():
    m=re.match(r'^\s*(\d{1,2})\s*[\)）]\s*$', line.strip())
    if m:
        if cur:
            parts.append(cur)
        cur={'idx':int(m.group(1)), 'lines':[]}
        continue
    if cur is None:
        continue
    cur['lines'].append(line.rstrip())
if cur:
    parts.append(cur)
parts=sorted(parts, key=lambda x:x['idx'])

entries=[]
for p in parts[:10]:
    block='\n'.join(p['lines']).strip()
    def grab(label):
        m=re.search(rf'{label}\s*[:：]\s*(.+)', block)
        return m.group(1).strip() if m else ''
    zh=grab('中文标题')
    en=grab('English Title')
    lead=grab('导语')
    kw=grab('关键词')
    src=grab('原文链接')
    #正文：从“正文：”开始到“关键词：”之前（尽量）
    body=''
    m=re.search(r'正文\s*[:：]\s*(.*)', block, re.S)
    if m:
        body=m.group(1)
        body=re.split(r'\n\s*关键词\s*[:：]', body)[0].strip()
        body=re.split(r'\n\s*图片URL\s*[:：]', body)[0].strip()
        body=re.split(r'\n\s*原文链接\s*[:：]', body)[0].strip()
    entries.append({'idx':p['idx'], 'zh':zh, 'en':en, 'lead':lead, 'body':body, 'kw':kw, 'src':src})

# fallback: if parsing failed, keep raw text
if len(entries) < 8:
    entries=[]

# -------- build DOCX with fixed layout --------
Doc=Document()

# base font
style=Doc.styles['Normal']
style.font.name='PingFang SC'
style.font.size=Pt(11)

# Title
Doc.add_heading(f"每日见闻（{date_str}）", level=1)

# Group indices by category from sources.json
cat_order=['科技','AI','太空','动物自然环境','国际热点']
cat_map={c:[] for c in cat_order}
for i,it in enumerate(items,1):
    c=it.get('cat') or ''
    if c not in cat_map:
        # bucket unknowns
        cat_map.setdefault(c, [])
    cat_map[c].append(i)

# helper to get image path by overall index
img_paths=[]
for i in range(1, 11):
    candidates=list(img_dir.glob(f"{i:02d}.*"))
    cand_sorted=sorted(candidates, key=lambda p: (p.suffix.lower() not in ('.jpg','.jpeg','.png'), str(p)))
    img_paths.append(cand_sorted[0] if cand_sorted else None)

def add_item(i:int):
    # find entry text
    e=None
    if entries:
        for x in entries:
            if x['idx']==i:
                e=x
                break
    # title
    if e and e.get('zh'):
        Doc.add_heading(f"{i}. {e['zh']}", level=3)
    else:
        # fallback to source title
        t=(items[i-1].get('title') if i-1 < len(items) else '')
        Doc.add_heading(f"{i}. {t}", level=3)

    # image (from original source only)
    p=img_paths[i-1] if i-1 < len(img_paths) else None
    if p and p.exists():
        if p.suffix.lower()=='.webp':
            alt=p.with_suffix('.jpg')
            if alt.exists():
                p=alt
        try:
            Doc.add_picture(str(p), width=Inches(6.0))
        except Exception:
            pass

    # English title
    if e and e.get('en'):
        para=Doc.add_paragraph(f"English Title: {e['en']}")
        para.runs[0].italic=True if para.runs else None

    # Lead
    if e and e.get('lead'):
        para=Doc.add_paragraph(f"导语：{e['lead']}")
        if para.runs:
            para.runs[0].bold=True

    # Body
    if e and e.get('body'):
        for blk in re.split(r'\n\s*\n', e['body']):
            blk=blk.strip()
            if not blk:
                continue
            Doc.add_paragraph(blk)

    # Keywords + Source
    if e and e.get('kw'):
        Doc.add_paragraph(f"关键词：{e['kw']}")
    # Always use original link from sources.json when available
    src=(items[i-1].get('link') if i-1 < len(items) else '')
    if not src and e:
        src=e.get('src')
    if src:
        Doc.add_paragraph(f"原文链接：{src}")

# Sections
for cat in cat_order:
    idxs=cat_map.get(cat, [])
    if not idxs:
        continue
    Doc.add_heading(f"{cat}（{len(idxs)}）", level=2)
    for i in idxs:
        add_item(i)

# any remaining categories
for cat, idxs in cat_map.items():
    if cat in cat_order or not idxs:
        continue
    Doc.add_heading(f"{cat}（{len(idxs)}）", level=2)
    for i in idxs:
        add_item(i)

Doc.save(docx_out)
print('wrote', docx_out)
PY

# 6) Export PDF via Pages (LaunchAgent-safe)
open -a "Pages" "$DOCX_OUT" || true
sleep 3

osascript <<OSA
set inFile to POSIX file "$DOCX_OUT"
set outFile to POSIX file "$PDF_OUT"

tell application "Pages"
  activate
  delay 2
  set theDoc to open inFile
  delay 2
  export theDoc to outFile as PDF
  close theDoc saving no
end tell
OSA

echo "OK: $PDF_OUT"
