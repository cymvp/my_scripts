# chrome-translate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个本机安装的 Chrome 插件，点图标后把当前文章的每个正文段落下方插入中文译文，再点一次移除。

**Architecture:** Manifest V3。后台 service worker 持有每个标签页的翻译状态并向 LLM Router 发请求；内容脚本是纯渲染层，用 Readability 的副本定位正文、按文本匹配回真实页面；纯逻辑集中在 `lib.js`，插件和 Node 单元测试共用。

**Tech Stack:** 原生 JavaScript（无打包）、`@mozilla/readability` 0.6.0 单文件复制进仓库、`vitest` 5 + `jsdom` 30 做单元测试。

**Spec:** `chrome-translate/docs/spec/2026-10-18-chrome-translate-spec.md`

## Global Constraints

- 仓库根目录 `~/projects/my_scripts/chrome-translate/`，所有路径相对它。
- 不引入打包工具。`lib.js` 文件末尾：`if (typeof module !== 'undefined') module.exports = CT; else globalThis.CT = CT;`
- 内容脚本、后台脚本不能用 `import`。后台用 `importScripts('lib.js')`。
- 默认 endpoint `http://172.24.201.32:8080/v1`，默认模型 `cuiyang/gpt-6-luna`，存储 key 为 `endpoint` / `model` / `apiKey`。
- API key 不出现在仓库任何文件里。
- 分批：每批 ≤ 3000 字符且 ≤ 20 段；并发 3；每批超时 60 秒。
- 译文块 class `ct-translation`，属性 `data-ct-index`。
- 角标：进度 `NN%`；失败后结束停 红色 `ERR`；不可注入 `N/A` 3 秒；无正文 `0` 3 秒。
- 所有文件 UTF-8，提交信息格式 `<type>(<scope>): <description>`。不要自动 push。

## Review Focus

1. 段落文本含双引号、反斜杠、换行时，请求 JSON 仍合法，译文仍按序对上。测试放 Task 1（`parseTranslation` 与 `buildRequestBody`）。
2. 模型返回的 `content` 外面裹了 ```` ```json ```` 围栏。按格式错误处理，不尝试剥离。测试放 Task 1。
3. 单个段落超过 3000 字符时，单独成一批而不是被丢弃。测试放 Task 1。
4. 页面 `li` 里既有直接文本又有 `p` 时只取 `p`；`blockquote` 里有 `p` 时只取 `p`。测试放 Task 2。
5. 翻译过程中用户关掉标签页，后台的状态表要删掉对应条目，不留悬挂的 controller。测试放 Task 4。

---

### Task 1: 仓库骨架与 lib.js 的纯文本逻辑

**Files:**
- Create: `package.json`、`.gitignore`、`lib.js`、`readability.js`（复制自 npm 包）、`test/lib.text.test.js`

**Interfaces:**
- Produces:
  - `CT.normalizeText(s: string): string` 合并连续空白为一个空格并去首尾。
  - `CT.splitBatches(texts: string[], opts?: {maxChars?: number, maxCount?: number}): Array<{start: number, texts: string[]}>`，默认 3000 / 20，`start` 是该批第一段在原数组的下标。
  - `CT.buildRequestBody(model: string, texts: string[]): object` 返回 `{model, messages:[{role:'system',content:SYSTEM_PROMPT},{role:'user',content:JSON.stringify(texts)}], response_format:{type:'json_object'}}`，SYSTEM_PROMPT 逐字取 spec 6.3。
  - `CT.parseTranslation(content: string, expected: number): string[]`，失败抛 `Error('返回格式错误')`。

- [ ] **Step 1: 建仓库骨架**

`package.json`：`"type": "commonjs"`，devDependencies `vitest@5.0.5`、`jsdom@30.1.3`，scripts `"test": "vitest run"`。`.gitignore` 写 `node_modules/`。
运行 `npm install`，然后 `cp node_modules/@mozilla/readability/Readability.js readability.js`，并在文件第一行前加注释 `// @mozilla/readability 0.6.0, Apache-2.0, copied unmodified`。

- [ ] **Step 2: 写失败测试 `test/lib.text.test.js`**

```js
const CT = require('../lib.js');
test('normalizeText collapses whitespace', () =>
  expect(CT.normalizeText('  a \n\t b  ')).toBe('a b'));
test('splitBatches respects maxCount', () => {
  const b = CT.splitBatches(Array(50).fill('x'.repeat(100)));
  expect(b.map(x => x.texts.length)).toEqual([20, 20, 10]);
  expect(b.map(x => x.start)).toEqual([0, 20, 40]);
});
test('splitBatches respects maxChars', () => {
  const b = CT.splitBatches(Array(10).fill('x'.repeat(1000)));
  expect(b.every(x => x.texts.join('').length <= 3000)).toBe(true);
  expect(b.length).toBe(4);
});
test('splitBatches keeps an oversized paragraph as its own batch', () => {
  const b = CT.splitBatches(['a', 'x'.repeat(5000), 'b']);
  expect(b.map(x => x.texts.length)).toEqual([1, 1, 1]);
});
test('buildRequestBody escapes quotes and newlines', () => {
  const body = CT.buildRequestBody('m', ['say "hi"\nnow', 'back\\slash']);
  expect(JSON.parse(body.messages[1].content)).toEqual(['say "hi"\nnow', 'back\\slash']);
  expect(body.response_format).toEqual({ type: 'json_object' });
});
test('parseTranslation returns array in order', () =>
  expect(CT.parseTranslation('{"t":["甲","乙"]}', 2)).toEqual(['甲', '乙']));
test('parseTranslation rejects length mismatch', () =>
  expect(() => CT.parseTranslation('{"t":["甲"]}', 2)).toThrow('返回格式错误'));
test('parseTranslation rejects fenced json', () =>
  expect(() => CT.parseTranslation('```json\n{"t":["甲"]}\n```', 1)).toThrow('返回格式错误'));
test('parseTranslation rejects non-array', () =>
  expect(() => CT.parseTranslation('{"t":"甲"}', 1)).toThrow('返回格式错误'));
```

- [ ] **Step 3: 跑测试确认失败**

Run: `npx vitest run test/lib.text.test.js`
Expected: FAIL，`Cannot find module '../lib.js'`。

- [ ] **Step 4: 实现 `lib.js` 里上述四个函数**

`splitBatches` 顺序扫描，当前批加入下一段会超 maxChars 或 maxCount 就先封批；单段本身超 maxChars 时自成一批。

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run test/lib.text.test.js`
Expected: 9 passed。

- [ ] **Step 6: Commit**

```bash
git add chrome-translate/package.json chrome-translate/package-lock.json chrome-translate/.gitignore chrome-translate/lib.js chrome-translate/readability.js chrome-translate/test/lib.text.test.js
git commit -m "feat(chrome-translate): scaffold and text helpers in lib.js"
```

---

### Task 2: lib.js 的 DOM 逻辑：段落选取、文本匹配、译文插入与移除

**Files:**
- Modify: `lib.js`
- Create: `test/lib.dom.test.js`（文件头 `// @vitest-environment jsdom`）

**Interfaces:**
- Consumes: `CT.normalizeText`。
- Produces:
  - `CT.collectParagraphs(root: Element|Document): Array<{el: Element, text: string}>`，按 spec 5.2 规则，文档顺序。
  - `CT.matchParagraphs(articleRoot: Element, liveRoot: Element|Document): Array<{el: Element, text: string}>`，按 spec 5.1 第 2、3 步。
  - `CT.insertTranslation(el: Element, index: number, text: string, isError?: boolean): void`，按 spec 5.3；isError 时额外加 class `ct-error`。
  - `CT.removeTranslations(root: Element|Document): number` 返回移除个数。

- [ ] **Step 1: 写失败测试 `test/lib.dom.test.js`**

```js
// @vitest-environment jsdom
const CT = require('../lib.js');
const html = s => { document.body.innerHTML = s; return document.body; };

test('collectParagraphs takes p/h/li/blockquote/td/figcaption, skips pre/code and short', () => {
  const r = html(`<h2>Title here</h2><p>One two three.</p><pre><code>a b c d</code></pre>
    <ul><li>Just li text</li><li><p>Inner para text</p></li></ul>
    <blockquote><p>Quoted words here</p></blockquote><blockquote>Bare quote text</blockquote>
    <a>Share</a><p>Share</p><figcaption>A caption here</figcaption>`);
  expect(CT.collectParagraphs(r).map(x => x.text)).toEqual([
    'Title here', 'One two three.', 'Just li text', 'Inner para text',
    'Quoted words here', 'Bare quote text', 'A caption here']);
});
test('matchParagraphs maps article copy back to live elements, first wins on duplicates', () => {
  const live = html(`<p id="a">Same text here</p><p id="b">Same text here</p><p id="c">Other text here</p><nav><p>Nav text here</p></nav>`);
  const article = document.createElement('div');
  article.innerHTML = `<p>Same  text here</p><p>Other text here</p><p>Missing text here</p>`;
  expect(CT.matchParagraphs(article, live).map(x => x.el.id)).toEqual(['a', 'c']);
});
test('insertTranslation after p, inside li, same tag for headings', () => {
  const r = html(`<h2 id="h">T</h2><p id="p">P</p><ul><li id="l">L</li></ul>`);
  CT.insertTranslation(r.querySelector('#h'), 0, '标题');
  CT.insertTranslation(r.querySelector('#p'), 1, '段落');
  CT.insertTranslation(r.querySelector('#l'), 2, '列表', true);
  expect(r.querySelector('#h').nextElementSibling.tagName).toBe('H2');
  expect(r.querySelector('#p').nextElementSibling.className).toBe('ct-translation');
  expect(r.querySelector('#l').lastElementChild.classList.contains('ct-error')).toBe(true);
  expect(r.querySelector('[data-ct-index="1"]').textContent).toBe('段落');
  expect(CT.removeTranslations(r)).toBe(3);
  expect(r.querySelectorAll('.ct-translation').length).toBe(0);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run test/lib.dom.test.js`
Expected: FAIL，`CT.collectParagraphs is not a function`。

- [ ] **Step 3: 实现四个函数**

`collectParagraphs`：`querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,blockquote,td,th,figcaption')`；跳过 `el.closest('pre,code')`；`li` 或 `blockquote` 内含 `p` 时跳过自身；文本按 `normalizeText(el.textContent)`，按空格拆分不足 2 个词跳过。
`matchParagraphs`：对 liveRoot 的 `collectParagraphs` 建 `Map<text, el>`（已有的不覆盖），再按 articleRoot 的段落顺序查表。
`insertTranslation`：`li` 用 `append`，其他用 `after`；标题用同名标签，其余用 `div`。

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run`
Expected: 全部 passed。

- [ ] **Step 5: Commit**

```bash
git add chrome-translate/lib.js chrome-translate/test/lib.dom.test.js
git commit -m "feat(chrome-translate): paragraph selection, matching and insertion"
```

---

### Task 3: content.js 渲染层

**Files:**
- Create: `content.js`、`content.css`

**Interfaces:**
- Consumes: `globalThis.CT`、`globalThis.Readability`（readability.js 挂的）。
- Produces: 消息协议（后台 Task 4 依赖）：
  - 收 `{type:'toggle'}`：页面已有 `.ct-translation` 则移除并回 `{removed:true}`；否则回 `{paragraphs: string[]}`（无正文时为空数组）。
  - 收 `{type:'insert', items: Array<{index:number, text:string, error?:boolean}>}`：对每项调 `insertTranslation`，回 `{ok:true}`。
  - 全局 `window.__ctLoaded = true` 防重复注册；`window.__ctParagraphs: Element[]` 保存本次选取的元素，供 `insert` 按 index 定位。

- [ ] **Step 1: 写 `content.js`**

顶部 `if (!window.__ctLoaded) { window.__ctLoaded = true; ... }`。`toggle` 分支：`const clone = document.cloneNode(true); const article = new Readability(clone).parse();` 为 null 回空数组；否则 `const tmp = document.createElement('div'); tmp.innerHTML = article.content;` 再 `CT.matchParagraphs(tmp, document)`。监听用 `chrome.runtime.onMessage.addListener((msg, _s, reply) => {...; return true;})`。

- [ ] **Step 2: 写 `content.css`**

`.ct-translation { color: rgba(0,0,0,.72); margin: .25em 0 1em; font-size: inherit; line-height: inherit; }`、`.ct-error { color: #b00020; }`、暗色页面用 `@media (prefers-color-scheme: dark)` 把颜色改成 `rgba(255,255,255,.72)`。

- [ ] **Step 3: 手工验证**

此任务没有自动化测试（Readability 对真实页面的行为要在浏览器里看）。验收合并到 Task 6。本步只检查 `node --check content.js` 通过。

- [ ] **Step 4: Commit**

```bash
git add chrome-translate/content.js chrome-translate/content.css
git commit -m "feat(chrome-translate): content script render layer"
```

---

### Task 4: background.js 状态机与翻译请求

**Files:**
- Create: `background.js`、`manifest.json`、`test/background.test.js`
- Modify: `lib.js`（加 `CT.translateBatch`）

**Interfaces:**
- Consumes: Task 1 的 `splitBatches`、`buildRequestBody`、`parseTranslation`；Task 3 的消息协议。
- Produces:
  - `CT.translateBatch(cfg: {endpoint, model, apiKey}, texts: string[], signal: AbortSignal, fetchImpl?: typeof fetch): Promise<string[]>`。非 2xx 抛 `Error('HTTP ' + status)`；格式错误抛 `parseTranslation` 的错。
  - `CT.runTranslation(paragraphs: string[], cfg, hooks: {onBatch(start, texts, result: string[]|Error): void, onProgress(done, total): void, signal, concurrency?: 3, timeoutMs?: 60000, fetchImpl?}): Promise<{failed: boolean}>`。并发 3，每批独立超时。
  - `background.js` 内存 `runs: Map<tabId, {controller: AbortController}>`。

- [ ] **Step 1: 写失败测试 `test/background.test.js`**

```js
const CT = require('../lib.js');
const ok = arr => async () => ({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content: JSON.stringify({ t: arr }) } }] }) });
const cfg = { endpoint: 'http://x/v1', model: 'm', apiKey: 'k' };

test('translateBatch posts to chat/completions with bearer', async () => {
  let seen; const f = async (url, init) => { seen = { url, init }; return ok(['甲'])(); };
  await CT.translateBatch(cfg, ['a b'], new AbortController().signal, f);
  expect(seen.url).toBe('http://x/v1/chat/completions');
  expect(seen.init.headers.Authorization).toBe('Bearer k');
});
test('translateBatch throws on HTTP 500', async () => {
  const f = async () => ({ ok: false, status: 500 });
  await expect(CT.translateBatch(cfg, ['a b'], new AbortController().signal, f)).rejects.toThrow('HTTP 500');
});
test('runTranslation reports per-batch results and failed flag', async () => {
  let n = 0; const f = async () => (++n === 2 ? { ok: false, status: 500 } : ok(['甲'])());
  const batches = []; const progress = [];
  const r = await CT.runTranslation(['a b', 'c d', 'e f'], cfg,
    { onBatch: (s, t, res) => batches.push([s, res instanceof Error]), onProgress: (d, t) => progress.push(d / t),
      signal: new AbortController().signal, fetchImpl: f, concurrency: 1, batchOpts: { maxChars: 3 } });
  expect(batches).toEqual([[0, false], [1, true], [2, false]]);
  expect(progress.at(-1)).toBe(1);
  expect(r.failed).toBe(true);
});
```

`runTranslation` 接受 `batchOpts` 透传给 `splitBatches`，上面用 `maxChars: 3` 让三段各成一批。再加一个取消测试：

```js
test('runTranslation stops issuing batches after abort', async () => {
  const ac = new AbortController(); let calls = 0;
  const f = async () => { calls++; ac.abort(); return ok(['甲'])(); };
  await CT.runTranslation(['a b', 'c d', 'e f'], cfg, { onBatch() {}, onProgress() {}, signal: ac.signal, fetchImpl: f, concurrency: 1, batchOpts: { maxChars: 3 } });
  expect(calls).toBe(1);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run test/background.test.js`
Expected: FAIL，`translateBatch is not a function`。

- [ ] **Step 3: 在 `lib.js` 实现 `translateBatch` 与 `runTranslation`**

`runTranslation`：先 `splitBatches`，用一个简单的 worker 池（`concurrency` 个 async 循环从队列取批）；每批 `AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)])`；每批结束后 `done += texts.length` 并调 `onProgress`；队列取批前检查 `signal.aborted` 则退出。

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run`
Expected: 全部 passed。

- [ ] **Step 5: 写 `manifest.json` 和 `background.js`**

`manifest.json`：`manifest_version: 3`，`name: "Article Translator (EN→ZH)"`，`version: "0.1.0"`，`permissions: ["activeTab","scripting","storage"]`，`host_permissions: ["http://172.24.201.32/*"]`，`optional_host_permissions: ["<all_urls>"]`，`background: {service_worker: "background.js"}`，`action: {default_title: "翻译本页"}`，`options_page: "options.html"`。
`background.js`：`importScripts('lib.js')`。`chrome.action.onClicked` 处理顺序按 spec 第 4 节：
1. 读 storage；`apiKey` 空则 `chrome.runtime.openOptionsPage()` 并 `chrome.storage.session.set({needKey:true})`。
2. `runs.has(tab.id)` 则 `controller.abort()`、删条目、清角标。
3. `executeScript({files:['readability.js','lib.js','content.js']})` 和 `insertCSS({files:['content.css']})`，失败则角标 `N/A` 3 秒。
4. 发 `toggle`。`removed` 则结束；`paragraphs` 为空则角标 `0` 3 秒。
5. 建 controller 存入 `runs`，调 `runTranslation`，`onProgress` 设角标 `Math.round(done/total*100)+'%'`，`onBatch` 发 `insert`（Error 时每段 `text:'翻译失败：'+err.message, error:true`）。结束后删条目；`failed` 则角标红色 `ERR`，否则清空。
角标统一经 `setBadge(tabId, text, color?)`。`chrome.tabs.onRemoved` 时若 `runs` 有该 tab 则 abort 并删除。

- [ ] **Step 6: 语法检查**

Run: `node --check background.js && python3 -c "import json;json.load(open('manifest.json'))"`
Expected: 无输出。

- [ ] **Step 7: Commit**

```bash
git add chrome-translate/lib.js chrome-translate/background.js chrome-translate/manifest.json chrome-translate/test/background.test.js
git commit -m "feat(chrome-translate): background state machine and LLM requests"
```

---

### Task 5: 设置页

**Files:**
- Create: `options.html`、`options.js`

**Interfaces:**
- Consumes: storage key `endpoint` / `model` / `apiKey`；`chrome.storage.session.needKey`。

- [ ] **Step 1: 写 `options.html`**

三个输入框（endpoint、model、apiKey，apiKey 为 `type=password`）、一个保存按钮、顶部一个 `<p id="notice">`。中文文案。

- [ ] **Step 2: 写 `options.js`**

加载时从 `chrome.storage.local` 读三项，空则填默认值；读 `chrome.storage.session.needKey`，为真则 notice 显示「请先填写 API key」并清掉该标志。
保存时：解析 endpoint 的 origin，若不在 `http://172.24.201.32/*` 内，`chrome.permissions.request({origins:[origin+'/*']})`，拒绝则 notice 显示「未获得该地址的访问权限」且不保存。通过则写 storage，notice 显示「已保存」。

- [ ] **Step 3: 语法检查**

Run: `node --check options.js`

- [ ] **Step 4: Commit**

```bash
git add chrome-translate/options.html chrome-translate/options.js
git commit -m "feat(chrome-translate): options page with host permission request"
```

---

### Task 6: 手工验收与 README

**Files:**
- Create: `README.md`

- [ ] **Step 1: 在 Chrome 加载插件**

`chrome://extensions` 打开开发者模式，「加载已解压的扩展程序」选 `chrome-translate/`。打开设置页粘 key。

- [ ] **Step 2: 按 spec 第 10 节跑 T1、T2、T3、T4、T10**

逐条记录结果到本文件末尾的验收表（通过 / 不通过 + 现象）。T1 的样例页 `https://claude.dev/blog/solving-code-review-at-anthropic/`，额外检查：导航和页脚没有译文、代码块没有译文、标题译文样式与原标题一致。

- [ ] **Step 3: 写 `README.md`**

内容：用途一句话、安装三步、设置页说明、`npm test`、文件职责表（照 spec 第 4 节）。

- [ ] **Step 4: Commit**

```bash
git add chrome-translate/README.md chrome-translate/docs/tasks/2026-10-18-chrome-translate-plan.md
git commit -m "docs(chrome-translate): README and acceptance record"
```

## 验收记录

| 用例 | 结果 | 现象 |
|---|---|---|
| T1 | | |
| T2 | | |
| T3 | | |
| T4 | | |
| T10 | | |
