#!/bin/bash
set -euo pipefail

TARGET_WEIXIN_ID="o9cq80wvkp5fs2qrfn_y0i97rqaa@im.wechat"
WEIXIN_ACCOUNT_ID="a5b6e134e986-im-bot"

OUT_ROOT="$HOME/projects/daughter-digest"
DATE_STR="$(date +%F)"
OUT_DIR="$OUT_ROOT/$DATE_STR"
mkdir -p "$OUT_DIR/images"

PROMPT_FILE="$OUT_DIR/prompt.txt"
cat > "$PROMPT_FILE" <<'PROMPT'
Generate today's digest for a 10-year-old (Grade 4) in Chinese, with a light, engaging tone and strict safety filtering.

Goal: 10 items total with this ratio:
- Technology (life-related practical tech/tools): 2
- AI/Robotics: 2
- Space/Astronomy: 2
- Animals/Nature/Earth environment: 2
- International hotspots/current events: 2 (REAL-WORLD major events explained in kid-safe terms)

For EACH item, write a kid-friendly mini-article of ~900-1100 Chinese characters (rough target). It should be readable as a standalone article.

Structure for each item:
- 标题（≤18字）
- 导语（1-2句）
- 正文（分3-6段）
- 关键词（中英各3个）
- English (1 simple sentence or the original headline)
- 图片URL（必须是 http(s) 绝对地址；如果没有就写“无图”）
- 原文链接

Do NOT include a 亲子提问 section.
Avoid: discounts/deals, developer conferences, privacy policy debates, violence/graphic war content, gore, fear-inducing disaster details.

Sources (use web_fetch on RSS):
- ScienceDaily Technology https://www.sciencedaily.com/rss/top/technology.xml
- Space.com https://www.space.com/feeds.xml
- NASA https://www.nasa.gov/news-release/feed/
- ScienceDaily Earth & Climate https://www.sciencedaily.com/rss/earth_climate.xml
- Science News Explores https://www.snexplores.org/feed
- International: NYT World https://rss.nytimes.com/services/xml/rss/nyt/World.xml + UN News https://news.un.org/feed/subscribe/en/news/all/rss.xml
PROMPT

JSON_OUT="$OUT_DIR/agent.json"
MD_OUT="$OUT_DIR/digest.md"
DOCX_OUT="$OUT_DIR/digest.docx"
PDF_OUT="$OUT_DIR/digest.pdf"

OPENCLAW_BIN="$HOME/.npm-global/bin/openclaw"
"$OPENCLAW_BIN" agent --agent main --json --message "$(cat "$PROMPT_FILE")" > "$JSON_OUT"

# Step 1: extract assistant text
python3 - "$JSON_OUT" "$MD_OUT" <<'PY'
import json, sys
from pathlib import Path
json_path, md_path = sys.argv[1], sys.argv[2]
j = json.loads(Path(json_path).read_text())
text=None
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

# Step 2: download up to 10 images
python3 - "$MD_OUT" "$OUT_DIR" <<'PY'
import re, subprocess, sys
from pathlib import Path
md_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
text=md_path.read_text(encoding='utf-8')
urls=[]
for line in text.splitlines():
    if '图片URL' in line:
        m=re.search(r'图片URL\s*[:：]\s*(\S+)', line)
        if m:
            u=m.group(1).strip()
            if u.startswith('http://') or u.startswith('https://'):
                urls.append(u)
urls=urls[:10]
(out_dir/'image-urls.txt').write_text('\n'.join(urls), encoding='utf-8')
(out_dir/'images').mkdir(parents=True, exist_ok=True)
for i,u in enumerate(urls,1):
    ext=u.split('?')[0].split('.')[-1].lower()
    if ext not in ('jpg','jpeg','png','webp','gif'):
        ext='jpg'
    fp=out_dir/'images'/f"{i:02d}.{ext}"
    try:
        subprocess.run(['curl','-fsSL',u,'-o',str(fp)], check=True)
    except Exception:
        pass
for p in (out_dir/'images').glob('*.webp'):
    jpg=p.with_suffix('.jpg')
    subprocess.run(['sips','-s','format','jpeg',str(p),'--out',str(jpg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PY

# Step 3: build DOCX
python3 - "$MD_OUT" "$OUT_DIR" "$DOCX_OUT" <<'PY'
import re, sys
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt

md_path, out_dir, docx_out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
lines=md_path.read_text(encoding='utf-8').splitlines()

img_paths=[]
for i in range(1, 11):
    candidates=list((out_dir/'images').glob(f"{i:02d}.*"))
    cand_sorted=sorted(candidates, key=lambda p: (p.suffix.lower() not in ('.jpg','.jpeg','.png'), str(p)))
    img_paths.append(cand_sorted[0] if cand_sorted else None)
img_iter=iter(img_paths)

item_re=re.compile(r'^\s*\d+\s*[\)）]')

doc=Document()
style=doc.styles['Normal']
style.font.name='PingFang SC'
style.font.size=Pt(11)

for ln in lines:
    ln=ln.rstrip()
    if not ln:
        continue
    if ln.startswith('# '):
        doc.add_heading(ln[2:].strip(), level=1); continue
    if ln.startswith('## '):
        doc.add_heading(ln[3:].strip(), level=2); continue
    if item_re.match(ln):
        doc.add_heading(ln.strip(), level=3)
        p=next(img_iter, None)
        if p and p.exists():
            if p.suffix.lower()=='.webp':
                alt=p.with_suffix('.jpg')
                if alt.exists():
                    p=alt
            try:
                doc.add_picture(str(p), width=Inches(6.0))
            except Exception:
                pass
        continue
    doc.add_paragraph(ln)

doc.save(docx_out)
print('wrote', docx_out)
PY

# Step 4: export PDF via Pages
osascript <<OSA
set inFile to POSIX file "$DOCX_OUT"
set outFile to POSIX file "$PDF_OUT"

tell application "Pages"
  activate
  set theDoc to open inFile
  delay 1
  export theDoc to outFile as PDF
  close theDoc saving no
end tell
OSA

# Step 5: send to Weixin
"$OPENCLAW_BIN" message send \
  --channel openclaw-weixin \
  --target "$TARGET_WEIXIN_ID" \
  --message "每日见闻（$DATE_STR）PDF" \
  --media "$PDF_OUT" \
  --reply-account "$WEIXIN_ACCOUNT_ID" || true

echo "OK: $PDF_OUT"
