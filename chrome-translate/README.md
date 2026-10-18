# chrome-translate

点一下工具栏图标，把当前英文文章变成段落级中英对照：每个正文段落下方插入中文译文，原文保留。再点一次移除。翻译由个人 LLM Router（OpenAI 兼容接口）完成。

## 安装

1. 打开 `chrome://extensions`，右上角打开「开发者模式」。
2. 点「加载已解压的扩展程序」，选择本目录 `chrome-translate/`。
3. 点插件图标旁的「详情 → 扩展程序选项」（或第一次点图标会自动打开设置页），粘贴 API key，保存。

设置页三项：endpoint（默认 `http://172.24.201.32:8080/v1`）、模型（默认 `cuiyang/gpt-6-luna`）、API key。key 只存在本机 Chrome 的扩展存储里，不会进入本仓库。endpoint 改成别的主机时 Chrome 会弹一次权限申请。

## 使用

- 点图标：翻译。角标显示进度百分比，译文按批出现。
- 翻译中再点：取消，已出现的译文保留。
- 翻译完再点：移除全部译文。
- 角标 `ERR`：有批次失败，对应段落下有红字说明。再点两次即重来。
- 角标 `N/A`：当前页面不允许注入脚本（如 `chrome://` 页面）。角标 `0`：没找到正文。

## 测试

```bash
npm install
npm test
```

## 文件职责

| 文件 | 职责 |
|---|---|
| `manifest.json` | 权限与入口声明（Manifest V3） |
| `background.js` | 响应图标点击；持有每个标签页的翻译状态；向 LLM Router 发请求；更新角标 |
| `lib.js` | 纯逻辑：分批、组请求、解析返回、段落选取、文本匹配、译文插入。插件与测试共用 |
| `content.js` + `content.css` | 页面内渲染层：用 Readability 定位正文，插入和移除译文 |
| `readability.js` | Mozilla Readability 0.6.0 原样复制 |
| `options.html` + `options.js` | 设置页 |
| `docs/spec/` | 设计文档 |
| `docs/tasks/` | 实施计划与验收记录 |
