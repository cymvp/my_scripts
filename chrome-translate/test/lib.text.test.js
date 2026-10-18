import { test, expect } from 'vitest';
import CT from '../lib.js';

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
  expect(body.model).toBe('m');
  expect(body.messages[0].role).toBe('system');
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

test('lib.js can be evaluated twice in the same global scope (re-injection)', async () => {
  const vm = await import('node:vm');
  const fs = await import('node:fs');
  const src = fs.readFileSync('lib.js', 'utf8');
  const ctx = vm.createContext({});
  vm.runInContext(src, ctx);
  expect(() => vm.runInContext(src, ctx)).not.toThrow();
  expect(typeof ctx.CT.splitBatches).toBe('function');
});
