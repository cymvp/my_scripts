// 用 vm 加载 background.js，chrome API 用最小 mock。
import { test, expect, vi } from 'vitest';
import fs from 'node:fs';
import vm from 'node:vm';

function loadBackground({ fetchImpl, sendMessageImpl, execFail } = {}) {
  const listeners = {};
  const badge = [];
  const ctx = {
    console, setTimeout, clearTimeout, setInterval, clearInterval, AbortController, AbortSignal, Promise, Error, Map, Array, Object, JSON, Math, String, Set,
    fetch: fetchImpl,
    importScripts(f) { vm.runInContext(fs.readFileSync(f, 'utf8'), ctx); },
    chrome: {
      action: {
        onClicked: { addListener: fn => (listeners.click = fn) },
        setBadgeText: async ({ tabId, text }) => badge.push([tabId, text]),
        setBadgeBackgroundColor: async () => {},
      },
      storage: { local: { get: async () => ({ apiKey: 'k' }) }, session: { set: async () => {} } },
      runtime: { openOptionsPage() {}, getPlatformInfo: vi.fn(async () => ({})) },
      scripting: {
        executeScript: async () => { if (execFail) throw new Error('no'); },
        insertCSS: vi.fn(async () => {}),
      },
      tabs: {
        onRemoved: { addListener: fn => (listeners.removed = fn) },
        onUpdated: { addListener: fn => (listeners.updated = fn) },
        sendMessage: sendMessageImpl || (async (_t, msg) => (msg.type === 'toggle' ? { paragraphs: ['a b', 'c d'] } : { ok: true })),
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('background.js', 'utf8'), ctx);
  return { listeners, badge, ctx };
}
const slowOk = () => new Promise(res => setTimeout(() => res({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content: '{"t":["甲","乙"]}' } }] }) }), 50));

test('closing the tab aborts the run and removes it from the table', async () => {
  const { listeners, badge } = loadBackground({ fetchImpl: slowOk });
  const p = listeners.click({ id: 7 });
  await new Promise(r => setTimeout(r, 10));
  listeners.removed(7);
  await p;
  expect(badge.at(-1)).not.toEqual([7, '']); // 取消后不再写角标（关标签页路径不碰角标）
  expect(badge.filter(b => b[1] === '100%').length).toBe(0);
});

test('double click within the setup window starts only one run', async () => {
  let fetches = 0;
  const { listeners } = loadBackground({ fetchImpl: async (...a) => { fetches++; return slowOk(); } });
  const p1 = listeners.click({ id: 1 });
  const p2 = listeners.click({ id: 1 });
  await Promise.all([p1, p2]);
  expect(fetches).toBe(1);
});

test('removing translations clears the badge', async () => {
  const { listeners, badge } = loadBackground({ sendMessageImpl: async () => ({ removed: true }) });
  await listeners.click({ id: 2 });
  expect(badge).toContainEqual([2, '']);
});

test('sendMessage failure shows N/A instead of throwing', async () => {
  const { listeners, badge } = loadBackground({ sendMessageImpl: async () => { throw new Error('Receiving end does not exist'); } });
  await listeners.click({ id: 3 });
  expect(badge).toContainEqual([3, 'N/A']);
});

test('keepalive pings the runtime while a run is in flight', async () => {
  vi.useFakeTimers();
  const { listeners, ctx } = loadBackground({ fetchImpl: () => new Promise(res => setTimeout(() => res({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content: '{"t":["甲","乙"]}' } }] }) }), 45000)) });
  const p = listeners.click({ id: 4 });
  await vi.advanceTimersByTimeAsync(46000);
  await p;
  expect(ctx.chrome.runtime.getPlatformInfo).toHaveBeenCalled();
  vi.useRealTimers();
});

test('CSS is not injected when the click only removes translations', async () => {
  const { listeners, ctx } = loadBackground({ sendMessageImpl: async () => ({ removed: true }) });
  await listeners.click({ id: 5 });
  expect(ctx.chrome.scripting.insertCSS).not.toHaveBeenCalled();
});

test('navigating away during a run aborts it', async () => {
  let fetches = 0;
  const { listeners, badge } = loadBackground({ fetchImpl: async () => { fetches++; return slowOk(); } });
  const p = listeners.click({ id: 6 });
  await new Promise(r => setTimeout(r, 10));
  listeners.updated(6, { status: 'loading' }, {});
  await p;
  expect(badge.filter(b => b[1] === '100%').length).toBe(0);
  expect(badge.at(-1)).toEqual([6, '']);
});
