// @vitest-environment jsdom
import { test, expect } from 'vitest';
import CT from '../lib.js';
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

test('insertTranslation after p, inside li, same tag for headings; removeTranslations clears', () => {
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

test('matchTitle finds the live h1 whose text equals the Readability title', () => {
  const live = html(`<h1 id="t">How  the team does review</h1><p>Body text here</p>`);
  expect(CT.matchTitle('How the team does review', live).id).toBe('t');
  expect(CT.matchTitle('Something else entirely', live)).toBe(null);
  expect(CT.matchTitle('', live)).toBe(null);
});

test('nested paragraph elements are taken only at the innermost level', () => {
  const r = html(`<ul><li>Outer item text<ul><li>Nested item text</li></ul></li></ul>
    <table><tr><td><p>Cell para text</p></td></tr></table>`);
  expect(CT.collectParagraphs(r).map(x => x.text)).toEqual(['Nested item text', 'Cell para text']);
});

test('mixed li with lead text and p takes only the p', () => {
  const r = html(`<ul><li>Lead words here<p>Inner para here</p></li></ul>`);
  expect(CT.collectParagraphs(r).map(x => x.text)).toEqual(['Inner para here']);
});

test('td/th translations go inside the cell', () => {
  const r = html(`<table><tr><td id="c">Cell text</td></tr></table>`);
  CT.insertTranslation(r.querySelector('#c'), 0, '单元格');
  expect(r.querySelector('#c').lastElementChild.className).toBe('ct-translation');
  expect(r.querySelectorAll('tr > *').length).toBe(1);
});
