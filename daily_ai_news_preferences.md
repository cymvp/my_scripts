# Daily AI News - 选题偏好

本文档描述 daily_ai_news.py 的选题优先级，供维护和调优 prompt 时参考。

## 选题优先级（从高到低）

1. **AI 热门开源项目** — GitHub trending、HN 高分帖中 AI 相关的高 star / 高分项目
2. **Anthropic / Claude / OpenAI 动态** — 模型发布、API 更新、重要公告等
3. **AI Agent 开发 & 记忆管理** — Agent 框架、工具链、记忆/检索技术的最新进展
4. **AI 对世界的变革与视角转变** — AI 在产业、社会、哲学层面带来的深层影响
5. **其他 AI 相关讯息** — 研究论文、融资、监管、行业趋势等

## 输出要求

- 每日推送 **20 条**
- 中文输出，Slack mrkdwn 格式
- emoji 编号，不使用 Markdown 标题/表格
- 同一事件合并报道，给 1-3 个代表链接
