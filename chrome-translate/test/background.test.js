import { test, expect } from 'vitest';
import CT from '../lib.js';
const ok = arr => async () => ({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content: JSON.stringify({ t: arr }) } }] }) });
const cfg = { endpoint: 'http://x/v1', model: 'm', apiKey: 'k' };

test('translateBatch posts to chat/completions with bearer', async () => {
  let seen; const f = async (url, init) => { seen = { url, init }; return ok(['甲'])(); };
  const r = await CT.translateBatch(cfg, ['a b'], new AbortController().signal, f);
  expect(r).toEqual(['甲']);
  expect(seen.url).toBe('http://x/v1/chat/completions');
  expect(seen.init.method).toBe('POST');
  expect(seen.init.headers.Authorization).toBe('Bearer k');
  expect(JSON.parse(seen.init.body).model).toBe('m');
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

test('runTranslation stops issuing batches after abort', async () => {
  const ac = new AbortController(); let calls = 0;
  const f = async () => { calls++; ac.abort(); return ok(['甲'])(); };
  await CT.runTranslation(['a b', 'c d', 'e f'], cfg, { onBatch() {}, onProgress() {}, signal: ac.signal, fetchImpl: f, concurrency: 1, batchOpts: { maxChars: 3 } });
  expect(calls).toBe(1);
});

test('translateBatch reports non-JSON body as format error', async () => {
  const f = async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token <'); } });
  await expect(CT.translateBatch(cfg, ['a b'], new AbortController().signal, f)).rejects.toThrow('返回格式错误');
});

test('runTranslation reports a timed-out batch as 超时', async () => {
  const f = (_u, init) => new Promise((_res, rej) => init.signal.addEventListener('abort', () => rej(init.signal.reason)));
  const errors = [];
  const r = await CT.runTranslation(['a b'], cfg, { onBatch: (_s, _t, res) => errors.push(res.message), onProgress() {},
    signal: new AbortController().signal, fetchImpl: f, timeoutMs: 20 });
  expect(errors).toEqual(['超时']);
  expect(r.failed).toBe(true);
});
