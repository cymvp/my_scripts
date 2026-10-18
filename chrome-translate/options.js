const DEFAULTS = { endpoint: 'http://172.24.201.32:8080/v1', model: 'cuiyang/gpt-6-luna', apiKey: '' };
const BUILTIN_HOST = '172.24.201.32';
const $ = id => document.getElementById(id);

function notice(text, ok) {
  $('notice').textContent = text;
  $('notice').className = ok ? 'ok' : '';
}

async function load() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  for (const k of Object.keys(DEFAULTS)) $(k).value = stored[k] || DEFAULTS[k];
  const { needKey } = await chrome.storage.session.get('needKey');
  if (needKey) {
    notice('请先填写 API key');
    chrome.storage.session.remove('needKey');
  }
}

async function save() {
  const cfg = { endpoint: $('endpoint').value.trim(), model: $('model').value.trim(), apiKey: $('apiKey').value.trim() };
  let url;
  try { url = new URL(cfg.endpoint); } catch (e) { notice('Endpoint 不是合法地址'); return; }
  if (url.hostname !== BUILTIN_HOST) {
    const granted = await chrome.permissions.request({ origins: [url.protocol + '//' + url.hostname + '/*'] });
    if (!granted) { notice('未获得该地址的访问权限'); return; }
  }
  await chrome.storage.local.set(cfg);
  notice('已保存', true);
}

$('save').addEventListener('click', save);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.needKey && changes.needKey.newValue) {
    notice('请先填写 API key');
    chrome.storage.session.remove('needKey');
  }
});
load();
