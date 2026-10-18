// 纯逻辑，不依赖 Chrome API。后台用 importScripts 加载，内容脚本按注入顺序加载，测试用 require。
// 用 var 并复用已有对象：内容脚本每次点击都会重新注入，顶层 const 会报重复声明。
var CT = (typeof globalThis !== 'undefined' && globalThis.CT) || {};

CT.SYSTEM_PROMPT = `You translate English web article paragraphs into Simplified Chinese.
Input is a JSON array of strings. Output a JSON object {"t": [...]} with the
translations in the same order and the same length.
Keep inline code, URLs, product names, and people's names unchanged.
Translate naturally; do not add notes or explanations.`;

CT.normalizeText = function (s) {
  return String(s).replace(/\s+/g, ' ').trim();
};

// 按累计字符数和段数分批。单段超过 maxChars 时自成一批。
CT.splitBatches = function (texts, opts) {
  const maxChars = (opts && opts.maxChars) || 3000;
  const maxCount = (opts && opts.maxCount) || 20;
  const batches = [];
  let cur = null;
  let curChars = 0;
  texts.forEach((t, i) => {
    const over = cur && (curChars + t.length > maxChars || cur.texts.length >= maxCount);
    if (!cur || over) {
      cur = { start: i, texts: [] };
      curChars = 0;
      batches.push(cur);
    }
    cur.texts.push(t);
    curChars += t.length;
  });
  return batches;
};

CT.buildRequestBody = function (model, texts) {
  return {
    model,
    messages: [
      { role: 'system', content: CT.SYSTEM_PROMPT },
      { role: 'user', content: JSON.stringify(texts) },
    ],
    response_format: { type: 'json_object' },
  };
};

CT.parseTranslation = function (content, expected) {
  let obj;
  try {
    obj = JSON.parse(content);
  } catch (e) {
    throw new Error('返回格式错误');
  }
  const t = obj && obj.t;
  if (!Array.isArray(t) || t.length !== expected || !t.every(x => typeof x === 'string')) {
    throw new Error('返回格式错误');
  }
  return t;
};


// ---------- DOM 部分（spec 5.1 / 5.2 / 5.3） ----------
CT.PARAGRAPH_SELECTOR = 'p,h1,h2,h3,h4,h5,h6,li,blockquote,td,th,figcaption';

// 按 spec 5.2 收集段落：跳过 pre/code 内、li/blockquote 内含 p 时跳过自身、不足 2 个词跳过。
CT.collectParagraphs = function (root) {
  const out = [];
  root.querySelectorAll(CT.PARAGRAPH_SELECTOR).forEach(el => {
    if (el.closest('pre,code')) return;
    if (el.classList && el.classList.contains('ct-translation')) return;
    // 内部还套着别的段落元素时跳过自身，只取最内层，避免同一段文字翻两次。
    if (el.querySelector(CT.PARAGRAPH_SELECTOR)) return;
    const text = CT.normalizeText(el.textContent);
    if (text.split(' ').length < 2) return;
    out.push({ el, text });
  });
  return out;
};

// 用正文副本里的段落文本，在真实页面里找对应元素。文本相同的多个元素只取第一个。
CT.matchParagraphs = function (articleRoot, liveRoot) {
  const map = new Map();
  CT.collectParagraphs(liveRoot).forEach(({ el, text }) => {
    if (!map.has(text)) map.set(text, el);
  });
  const out = [];
  const used = new Set();
  CT.collectParagraphs(articleRoot).forEach(({ text }) => {
    const el = map.get(text);
    if (el && !used.has(el)) { used.add(el); out.push({ el, text }); }
  });
  return out;
};

// Readability 把文章标题单独放在 article.title 里，不在正文副本里。在真实页面找文本相同的 h1。
CT.matchTitle = function (title, liveRoot) {
  const want = CT.normalizeText(title || '');
  if (!want) return null;
  for (const h of liveRoot.querySelectorAll('h1')) {
    if (CT.normalizeText(h.textContent) === want) return h;
  }
  return null;
};

CT.insertTranslation = function (el, index, text, isError) {
  const tag = el.tagName.toLowerCase();
  const node = el.ownerDocument.createElement(/^h[1-6]$/.test(tag) ? tag : 'div');
  node.className = 'ct-translation' + (isError ? ' ct-error' : '');
  node.setAttribute('data-ct-index', String(index));
  node.textContent = text;
  // li 和表格单元格的译文放在元素内部，放外面会破坏列表或多出一列。
  if (tag === 'li' || tag === 'td' || tag === 'th') el.append(node); else el.after(node);
};

CT.removeTranslations = function (root) {
  const nodes = root.querySelectorAll('.ct-translation');
  nodes.forEach(n => n.remove());
  return nodes.length;
};


// ---------- 请求部分（spec 6） ----------
CT.translateBatch = async function (cfg, texts, signal, fetchImpl) {
  const f = fetchImpl || fetch;
  const url = cfg.endpoint.replace(/\/+$/, '') + '/chat/completions';
  const res = await f(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + cfg.apiKey },
    body: JSON.stringify(CT.buildRequestBody(cfg.model, texts)),
    signal,
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  const content = data && data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content;
  return CT.parseTranslation(content, texts.length);
};

// 分批、并发、每批独立超时。onBatch(start, texts, string[] | Error)；onProgress(done, total)。
CT.runTranslation = async function (paragraphs, cfg, hooks) {
  const concurrency = hooks.concurrency || 3;
  const timeoutMs = hooks.timeoutMs || 60000;
  const queue = CT.splitBatches(paragraphs, hooks.batchOpts);
  const total = paragraphs.length;
  let done = 0;
  let failed = false;

  async function worker() {
    while (queue.length && !hooks.signal.aborted) {
      const batch = queue.shift();
      const signal = AbortSignal.any([hooks.signal, AbortSignal.timeout(timeoutMs)]);
      let result;
      try {
        result = await CT.translateBatch(cfg, batch.texts, signal, hooks.fetchImpl);
      } catch (e) {
        if (hooks.signal.aborted) return;
        failed = true;
        result = e instanceof Error ? e : new Error(String(e));
      }
      if (hooks.signal.aborted) return;
      hooks.onBatch(batch.start, batch.texts, result);
      done += batch.texts.length;
      hooks.onProgress(done, total);
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker));
  return { failed };
};

if (typeof module !== 'undefined') module.exports = CT; else globalThis.CT = CT;
