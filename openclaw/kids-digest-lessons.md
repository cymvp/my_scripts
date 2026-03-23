# Kids Digest 自动生成：教训与最佳实践（Mac / OpenClaw）

> 目的：把这次“昨天能跑、今天卡 session lock + Brave Key 误会 + Pages 导出失败”的经验固化下来，避免下次复发。

## TL;DR（结论）

1. **定时批处理任务不要复用 `agent:main:main`**（也不要用 `openclaw agent --agent main ...` 来跑大任务）。
2. 为批处理创建一个**独立 agent**（例如 `kids-digest`），让它使用独立的 session 文件，避免与聊天/Control UI 抢锁。
3. **不依赖 Brave/web_search**也能满足“每篇有原文 URL”：用脚本从 RSS 先抽取 10 条真实来源链接，再让模型只负责扩写。
4. LaunchAgent 里用 AppleScript 控制 Pages 导出 PDF 容易遇到 `-600`：需要先用 `open -a Pages file.docx` 启动应用，再 AppleScript 导出。

---

## 事故复盘：为什么昨天能产出，今天却卡住？

### 昨天（顺利产出）的关键条件
- 内容生成与排版导出主要走 **RSS/本地内容 → DOCX → PDF** 的链路。
- **没有（或很少）依赖** OpenClaw 的 `openclaw agent --agent main` 在 `agent:main:main` 上写 session history。
- 因此不会频繁触发：
  - `~/.openclaw/agents/main/sessions/<session>.jsonl.lock` 抢锁

### 今天（卡住）的根因
- 把“写 10×1000 字长文”的生成步骤放进 OpenClaw agent，并且使用了 `--agent main`（共享会话）。
- 同时存在多个消费者：
  - OpenClaw gateway
  - Control UI / 聊天会话
  - 手动多次触发脚本/验证命令
- 导致同一个 session 文件出现锁冲突：
  - 报错典型为：`Error: session file locked (timeout 10000ms)`
  - 锁文件典型为：`.../934afa62-...jsonl.lock`
  - 持锁 pid 常见为：openclaw-gateway

**结论**：不是“今天更难”，而是“今天把批处理跑进了共享会话 + 并发触发”，锁冲突概率大幅上升。

---

## Brave Key 的误会：为什么会出现“缺 BRAVE_API_KEY 就拒绝生成”？

- 用户需求只是“每篇必须有原文 URL（家长核对）”。
- 但模型在某些提示下会把“必须有原文链接”误读为“必须 web_search 实时核对”，从而在缺少 Brave Key 时拒绝输出，避免编造链接。

**正确做法**：不让模型去“找链接”。脚本先从 RSS 提供真实链接，模型只负责扩写和结构化输出。

---

## 推荐架构（稳定、可迁移）

### 1) 独立 agent：kids-digest
在 `~/.openclaw/openclaw.json` 中增加（注意是 `agents.list`，不是 `agents.entries`）：

```json
{
  "agents": {
    "defaults": { "model": { "primary": "openai/gpt-5.2" } },
    "list": [
      { "id": "kids-digest" }
    ]
  }
}
```

### 2) 生成脚本
- 脚本位置：`~/projects/my_scripts/openclaw/generate-digest-pdf.sh`
- 规范文件：`~/projects/daughter-digest/digest-spec.md`
- 输出：`~/projects/daughter-digest/digest-YYYY-MM-DD.pdf`
- 中间产物：`~/projects/daughter-digest/.work-YYYY-MM-DD/`

脚本策略：
1. RSS 抽取 10 条来源（2:2:2:2:2）→ 写入 `sources.json`
2. 拼接 `digest-spec.md` + 来源条目 → `prompt.txt`
3. `openclaw agent --agent kids-digest --json --message "$(cat prompt.txt)"`
4. 解析输出到 `digest.md`，下载图片（如果有）
5. 生成 DOCX
6. `open -a Pages digest.docx` + AppleScript 导出 PDF（避免 -600）

---

## 排障清单（以后再出问题按这个查）

1. **PDF 里只有“缺 Key”那段话**：说明模型拒绝输出，检查 prompt 是否包含“已给定 10 条来源链接”并明确“不需要 web_search”。
2. **agent.json 变 0 字节**：通常是 agent 运行失败/超时；检查 gateway 日志与 session lock。
3. **出现 session file locked**：确认是否误用了 `--agent main`；以及是否同时有多个 openclaw 进程在跑。
4. **Pages 导出 -600**：脚本是否先 `open -a Pages file.docx`；LaunchAgent 是否在 GUI session 下运行。

