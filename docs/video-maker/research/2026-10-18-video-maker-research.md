# 讲解类短视频（小红书 / 视频号）的制作技术路线调研

日期：2026-10-18。调研目标：做「AI 热点新闻」和「本体模型知识点」两类带语音讲解的短视频，画面随讲解内容逐页切换，素材可由 AI 生成。本文给出技术选型，不做设计。

目录

- [1. 结论](#1-结论)
- [2. 要做的东西](#2-要做的东西)
- [3. 声画同步的机制](#3-声画同步的机制)
- [4. 四条技术路线](#4-四条技术路线)
- [5. 各环节的组件选型](#5-各环节的组件选型)
- [6. 推荐方案](#6-推荐方案)
- [7. 未核实事项与风险](#7-未核实事项与风险)
- [8. 待拍板项](#8-待拍板项)
- [9. 来源](#9-来源)

## 1. 结论

推荐用「脚本 JSON 驱动」的代码生成路线：LLM 把选题写成分页脚本，TTS 把每页旁白合成语音并返回时间戳，Remotion 按时间戳把页面、字幕、图片渲染成竖屏 mp4。整条链路在本机跑，一期不用数字人。

这个结论有三个依据。第一，声画同步的本质是「以语音为时间轴」，TTS 返回的时间戳天然给出每页、每句、每个词的起止时间，拿它驱动画面切换就是精确同步，不用人工对轴。第二，本仓库已有两个内容源脚本：`daily_ai_news.py` 抓 AI 新闻并用 LLM 摘要，`ontology_digest_fetch.py` 抓本体论论文供 digest。视频脚本直接接在它们后面，选题环节不用重做。第三，免费的 edge-tts 实测对中文返回分词级时间戳（见 3.3），零成本就能做逐词高亮字幕。

成品工具（剪映文本成片、NotebookLM）能更快出片，代价是画面内容、切页时机和字幕样式都不受控，且不能接进现有脚本。本文把它们列为对照方案，见第 4 节。

## 2. 要做的东西

一条成片由五样东西合成：文稿、页面、语音、字幕、素材。

| 组成 | 含义 | 来源 |
|---|---|---|
| 文稿 | 分页的讲稿，每页一个标题、几条要点、一段旁白 | LLM 从新闻摘要或论文 digest 生成 |
| 页面 | 每页讲稿对应的一张画面 | 模板渲染（HTML/CSS 或 React 组件） |
| 语音 | 旁白的音频 | TTS |
| 字幕 | 跟语音逐句或逐词出现的文字 | TTS 返回的时间戳 |
| 素材 | 配图、图表、短视频片段 | AI 文生图、Mermaid 图、现成截图 |

两类内容对页面的要求不同。AI 新闻一条一页，页面是标题加要点加一张配图。本体模型知识点需要画概念关系图（类、属性、实例之间的边），页面里要能放 SVG 或 Mermaid 图。两类内容共用同一套合成链路，只是页面模板不同。

## 3. 声画同步的机制

### 3.1 以语音为时间轴

先合成语音，再根据语音的时长和时间戳决定画面何时切换。反过来做（先定画面时长再让语音去凑）会让旁白被截断或留白。

举个例子。一页讲稿的旁白合成后是 7.5 秒，这页画面就显示 7.5 秒，下一页从第 7.5 秒开始。十页加起来，视频总长就是十段语音的总长，一秒都不会错位。

### 3.2 三种对齐粒度

| 粒度 | 做法 | 效果 | 对 TTS 的要求 |
|---|---|---|---|
| 页级 | 每页一段音频，页长等于音频长 | 讲到哪页切哪页 | 只要音频时长 |
| 句级 | 一页内按句切换要点 | 讲到第二条要点时第二条亮起 | 句级时间戳 |
| 词级 | 字幕逐词弹出或高亮 | 小红书常见的「蹦字」字幕 | 词级时间戳 |

页级最简单，用 ffprobe 读音频时长就够，任何 TTS 都能做。词级需要 TTS 本身返回时间戳，否则要再用 WhisperX 这类对齐工具去听音频反推，多一道环节且中文精度没有公开数据。

### 3.3 edge-tts 的中文时间戳实测

edge-tts 是调用微软 Edge 浏览器朗读服务的开源库，免费，不需要 API key。2026-10-18 在本机用 `zh-CN-YunxiNeural` 音色合成「本体模型是一种用来描述领域概念和概念之间关系的结构。」，Python API 设 `boundary="WordBoundary"` 后返回 14 条事件，前几条如下。

| 文本 | 开始（秒） | 时长（秒） |
|---|---|---|
| 本体 | 0.100 | 0.338 |
| 模型 | 0.450 | 0.563 |
| 是 | 1.113 | 0.188 |
| 一种 | 1.313 | 0.275 |
| 用来 | 1.613 | 0.225 |

读法：「模型」这个词从第 0.450 秒开始发音，持续 0.563 秒。服务端已经做了中文分词，返回的是词，字幕可以按词高亮。命令行默认只输出句级字幕（`--write-subtitles` 产出 SRT，一句一条），词级要走 Python API。

## 4. 四条技术路线

### 4.1 路线甲：代码生成，Remotion 渲染

Remotion 是用 React 写视频的框架，每一帧由组件根据当前帧号渲染，再用无头浏览器逐帧截图并编码成 mp4。页面切换、字幕高亮、入场动画都是代码里按时间戳写的条件判断。`@remotion/captions` 包提供把时间戳切成字幕页的工具。许可：个人和 3 人以下公司免费，4 人以上公司要买 Company License。本机已有 Node 22。

### 4.2 路线乙：代码生成，ffmpeg 拼接

每页用 HTML 模板渲染成一张 PNG（Playwright 截图），每页一段音频，用 ffmpeg 把「图片 + 音频」逐页拼接并烧录 SRT 字幕。开源项目 AutoVideo、marp2video 就是这个做法。本机已有 ffmpeg 8.1.2。

### 4.3 路线丙：成品工具

剪映的「文本成片」输入文案后自动配素材、配音、配乐；剪映和腾讯智影都提供数字人口播。NotebookLM 的 Video Overviews 把上传的资料做成带 AI 主持人讲解的幻灯片视频，支持简体中文输出，2026 年 6 月起有 60 秒竖版的 Short Video Overviews（面向付费订阅）。

### 4.4 路线丁：Manim

Manim 是数学动画库，配套的 manim-voiceover 插件能在旁白的指定词触发动画，词级定位靠 Whisper。适合公式推导和图形变换，不适合新闻类内容。

### 4.5 对比

下表按「声画精确同步」「接现有脚本」「素材可控」三个硬需求比较四条路线。√ 表示该项占优。

| 维度 | 甲 Remotion | 乙 ffmpeg 拼接 | 丙 成品工具 | 丁 Manim |
|---|---|---|---|---|
| 切页时机精确可控 | √ | √ | × | √ |
| 逐词字幕与页内动画 | √ | × | 部分（剪映有） | √ |
| 能接 daily_ai_news.py 等现有脚本 | √ | √ | × | √ |
| 本体关系图这类自定义画面 | √（SVG/Mermaid 直接放） | √（截图） | × | 部分 |
| 上手成本 | 中（React 项目） | 低（Python 脚本） | 最低 | 高 |
| 出片速度（一条 1 分钟视频） | 渲染约 1 到 3 分钟 | 秒级 | 分钟级 | 渲染慢 |
| 数字人 | 需另接服务 | 需另接服务 | √ 内置 | × |
| 费用 | 个人免费 | 免费 | 剪映有免费额度，NotebookLM 竖版需付费 | 免费 |
| 合计 | 7 | 5 | 2 | 4 |

怎么读这张表：甲和乙占优的是「可控」和「可编程」，丙占优的是「不用写代码」和「有数字人」。合计数不能直接当结论，丙输掉的几项恰是本次的硬需求（切页时机、接现有脚本、自定义画面），所以丙只能做对照，不能做主线。甲和乙的差别在逐词字幕与页内动画，这两项决定成片像不像平台上常见的讲解视频。

## 5. 各环节的组件选型

### 5.1 文稿与分页

用 LLM 把输入材料写成固定结构的脚本 JSON：每页含 `title`、`bullets`、`narration`、`image_prompt`。新闻类输入是 `daily_ai_news.py` 已经产出的摘要；本体类输入是 `/ycui-doc-digest` 产出的中文 digest。开发用 LLM 按全局规则走 `~/.config/llm-router/.env`。

### 5.2 页面渲染

路线甲里页面是 React 组件；路线乙里页面是 HTML 模板。两种都用浏览器排版，中文字体、SVG、Mermaid 图都能放。竖屏按 1080 × 1920 做。

### 5.3 TTS

下表比较各 TTS 的中文时间戳支持与费用。

| 候选 | 费用 | 中文时间戳 | 音色质量 | 核实状态 |
|---|---|---|---|---|
| edge-tts | 免费 | 词级，本机实测 | 可用，偏播音腔 | 已实测 |
| MiniMax speech-2.8 | 阿里云百炼页标价 hd 3.5 元 / 万字符、turbo 2 元 / 万字符 | `subtitle_enable` + `subtitle_type=word`，官方文档确认 | 高，支持克隆 | 文档已核实，未实测 |
| 火山引擎豆包 TTS 2.0 | 未查到官方价 | 第三方封装称异步接口返回 `sentences[].words[]` | 高 | 参数名未在官方文档核实 |
| 本地 CosyVoice 3 / IndexTTS 2 | 免费 | 不返回，需再跑对齐 | 高，支持克隆 | Mac 无 NVIDIA 卡，可行性未核实 |

读法：一条 1 分钟视频旁白约 250 到 300 字，用 MiniMax hd 约 0.1 元，费用都可忽略，差别在音色和稳定性。edge-tts 用的是微软未公开接口，有被限流或改接口的风险，适合一期起步，正式发布前可换 MiniMax。

### 5.4 字幕

字幕直接用 TTS 返回的时间戳，不需要再跑 Whisper 识别。注意两点：阿里云文档明确提示 TTS 返回的文本是按读音转写的（「12」会变成「十二」），上屏要用原文，只取时间；edge-tts 实测返回的是原文分词，没有这个问题。

### 5.5 AI 素材

| 用途 | 候选 | 费用 | 核实状态 |
|---|---|---|---|
| 配图 | Seedream 5.0 Pro（火山方舟，接口 `POST /v1/images/generations`） | 第三方转述 1K 单张 0.3 元 | 价格未在官方页核实 |
| 配图 | 即梦、可灵网页版 | 有每日免费额度 | 第三方测评 |
| 短视频片段 | 可灵 3.0、Veo 3.1、Seedance 2.0 | 按秒计费，各家不同 | 未核实 |
| 概念关系图 | Mermaid / 自写 SVG | 免费 | 常规工具 |
| 数字人口播 | HeyGen、剪映数字人、腾讯智影 | HeyGen 2026 年起 API 纯按量付费；国内两家价格来源互相矛盾 | 均未核实 |

读法：一期用「文生图 + Mermaid 图」就够覆盖两类内容。数字人要另外接服务，口型同步质量和价格都要实测，建议放到二期。

### 5.6 合成

路线甲用 `npx remotion render` 出 mp4。路线乙用 ffmpeg 的 concat 拼接图片与音频，再用 subtitles 滤镜烧字幕。两者都输出 H.264 mp4，两个平台都能直接上传。

## 6. 推荐方案

推荐路线甲（Remotion），理由是正确性：字幕高亮、要点逐条出现、图片入场，这些效果都是「在某一帧做某件事」，Remotion 的帧驱动模型就是为这个设计的。路线乙要做同样效果得把每页拆成几十张图再拼，等于自己造一个弱化的渲染器。

路线乙是便宜的那个。如果只要「讲到哪页切哪页」而不要页内动画和逐词字幕，路线乙一天能跑通。要是选它，换的是把后续的字幕和动画需求堵死，到时要重做。

两条路线共用的部分是 Python 侧：脚本 JSON 生成、TTS 调用、时间戳整理。先做这部分，渲染层后换也不浪费。

一期范围建议：

1. 脚本生成：从一条 AI 新闻摘要生成 5 到 8 页脚本 JSON。
2. 语音与时间戳：edge-tts 合成每页旁白，输出音频文件和词级时间戳 JSON。
3. 渲染：Remotion 竖屏模板一套（标题页、要点页、图片页），字幕逐词高亮。
4. 素材：每页一张 Seedream 文生图，或 Mermaid 关系图。
5. 产出：1080 × 1920 的 mp4，手动上传两个平台。

## 7. 未核实事项与风险

| 事项 | 现状 | 影响 | 下一步 |
|---|---|---|---|
| 小红书、视频号的上传规格 | 只找到第三方资料且互相矛盾，官方页未抓到 | 影响分辨率和时长上限的取值 | 打开两个平台的发布页看提示，半小时内可定 |
| AI 生成内容的标识义务 | 2025 年 9 月起国家规定 AI 生成合成内容要显式标识，两个平台发布页的勾选要求未核实 | 发布时可能要打标 | 发第一条时在发布页核对 |
| edge-tts 接口稳定性 | 非官方接口 | 可能突然不可用 | TTS 调用写成可替换接口，备选 MiniMax |
| 火山引擎 TTS 时间戳参数 | 只有第三方封装的说法 | 决定它进不进备选 | 需要时读官方 V3 文档 |
| Seedream 价格 | 第三方转述 | 影响单条视频成本估算 | 需要时查火山方舟价目页 |
| AI 图片的版权与平台审核 | 未查 | 影响能否商用 | 二期前查各平台条款 |

## 8. 待拍板项

1. 路线选甲（Remotion）还是乙（ffmpeg）。本文推荐甲。
2. 一期内容类型：AI 新闻（输入现成）或本体知识点（需要关系图模板）。
3. 竖屏 9:16 一种尺寸，还是同时出横屏。
4. 一期 TTS 用 edge-tts 起步，正式发布前换 MiniMax。
5. 数字人放到二期。

以上确认后进入设计阶段，写 spec。

## 9. 来源

- Remotion 许可与定价：https://www.remotion.dev/docs/license/pricing
- Remotion 字幕包：https://www.remotion.dev/docs/captions/api
- Remotion 字幕显示：https://www.remotion.dev/docs/captions/displaying
- edge-tts 仓库：https://github.com/rany2/edge-tts
- edge-tts SubMaker 源码：https://raw.githubusercontent.com/rany2/edge-tts/master/src/edge_tts/submaker.py
- MiniMax T2A HTTP 接口（subtitle_enable / subtitle_type）：https://platform.minimax.io/docs/api-reference/speech-t2a-http
- MiniMax speech-2.8 在阿里云百炼的价格：https://help.aliyun.com/zh/model-studio/speech-2-8-hd
- 阿里云 TTS 时间戳说明（发音转写提示）：https://help.aliyun.com/zh/isi/developer-reference/timestamp-feature
- 豆包 TTS 第三方封装（enable_timestamp 说法）：https://tessl.io/registry/skills/github/calesthio/OpenMontage/doubao-tts
- NotebookLM Video Overviews 语言支持：https://support.google.com/notebooklm/answer/16454555
- NotebookLM Short Video Overviews：https://pasqualepillitteri.it/en/news/6883/notebooklm-short-video-overviews-en
- manim-voiceover：https://pypi.org/project/manim-voiceover/
- marp2video：https://pkg.go.dev/github.com/grokify/marp2video
- AutoVideo：https://github.com/jaimeberdejo/AutoVideo
- Seedream 5.0 Pro 价格转述：https://www.heyuan110.com/zh/posts/ai/2026-07-09-seedream-5-pro/
- 剪映文本成片体验：https://www.ifanr.com/1639648
- HeyGen 定价（第三方）：https://anam.ai/blog/heygen-pricing
- WhisperX 论文：https://arxiv.org/html/2303.00747v2
