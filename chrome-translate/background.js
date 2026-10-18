// 后台 service worker：持有每个标签页的翻译状态，向 LLM Router 发请求，更新角标。
importScripts('lib.js');

const DEFAULTS = { endpoint: 'http://172.24.201.32:8080/v1', model: 'cuiyang/gpt-6-luna', apiKey: '' };
const runs = new Map(); // tabId -> { controller }

function setBadge(tabId, text, color) {
  chrome.action.setBadgeText({ tabId, text }).catch(() => {});
  if (color) chrome.action.setBadgeBackgroundColor({ tabId, color }).catch(() => {});
}
function flashBadge(tabId, text) {
  setBadge(tabId, text, '#888');
  setTimeout(() => setBadge(tabId, ''), 3000);
}

async function getConfig() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  const cfg = { ...DEFAULTS };
  for (const k of Object.keys(DEFAULTS)) if (stored[k]) cfg[k] = stored[k];
  return cfg;
}

chrome.action.onClicked.addListener(async tab => {
  const tabId = tab.id;
  const cfg = await getConfig();
  if (!cfg.apiKey) {
    await chrome.storage.session.set({ needKey: true });
    chrome.runtime.openOptionsPage();
    return;
  }
  setBadge(tabId, '');
  if (runs.has(tabId)) {
    if (runs.get(tabId).starting) return; // 准备阶段（注入、找段落）内的再次点击忽略
    runs.get(tabId).controller.abort();
    runs.delete(tabId);
    return;
  }
  // 先占位，防止注入和 toggle 期间的第二次点击再开一份翻译。
  const controller = new AbortController();
  const run = { controller, starting: true };
  runs.set(tabId, run);
  let resp;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['readability.js', 'lib.js', 'content.js'] });
    await chrome.scripting.insertCSS({ target: { tabId }, files: ['content.css'] });
    resp = await chrome.tabs.sendMessage(tabId, { type: 'toggle' });
  } catch (e) {
    runs.delete(tabId);
    flashBadge(tabId, 'N/A');
    return;
  }
  if (controller.signal.aborted) return; // 占位期间被取消
  if (!resp || resp.removed) { runs.delete(tabId); return; }
  const paragraphs = resp.paragraphs || [];
  if (paragraphs.length === 0) { runs.delete(tabId); flashBadge(tabId, '0'); return; }

  run.starting = false;
  setBadge(tabId, '0%', '#3367d6');
  // 非流式请求超过 30 秒没返回，Chrome 可能回收 service worker；每 20 秒调一次扩展 API 重置空闲计时。
  const keepalive = setInterval(() => chrome.runtime.getPlatformInfo().catch(() => {}), 20000);
  const { failed } = await CT.runTranslation(paragraphs, cfg, {
    signal: controller.signal,
    onProgress: (done, total) => setBadge(tabId, Math.round(done / total * 100) + '%'),
    onBatch: (start, texts, result) => {
      const items = texts.map((_, i) => result instanceof Error
        ? { index: start + i, text: '翻译失败：' + result.message, error: true }
        : { index: start + i, text: result[i] });
      chrome.tabs.sendMessage(tabId, { type: 'insert', items }).catch(() => {});
    },
  });
  clearInterval(keepalive);
  if (runs.get(tabId)?.controller !== controller) return; // 已被取消或标签页已关
  runs.delete(tabId);
  if (failed) setBadge(tabId, 'ERR', '#b00020'); else setBadge(tabId, '');
});

chrome.tabs.onRemoved.addListener(tabId => {
  const run = runs.get(tabId);
  if (run) { run.controller.abort(); runs.delete(tabId); }
});
