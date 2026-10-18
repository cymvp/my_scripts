// 内容脚本：纯渲染层。收 toggle / insert 两种消息，不保存翻译状态。
if (!window.__ctLoaded) {
  window.__ctLoaded = true;
  window.__ctParagraphs = [];

  function selectParagraphs() {
    const clone = document.cloneNode(true);
    const article = new Readability(clone).parse();
    if (!article || !article.content) return [];
    const tmp = document.implementation.createHTMLDocument('').body;
    tmp.innerHTML = article.content;
    const paragraphs = CT.matchParagraphs(tmp, document);
    const h1 = CT.matchTitle(article.title, document);
    if (h1 && !paragraphs.some(p => p.el === h1)) {
      paragraphs.unshift({ el: h1, text: CT.normalizeText(h1.textContent) });
    }
    return paragraphs;
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === 'toggle') {
      if (CT.removeTranslations(document) > 0) {
        window.__ctParagraphs = [];
        reply({ removed: true });
      } else {
        try { window.__ctParagraphs = selectParagraphs(); } catch (e) { console.warn('[ct]', e); window.__ctParagraphs = []; }
        reply({ paragraphs: window.__ctParagraphs.map(p => p.text) });
      }
      return true;
    }
    if (msg.type === 'insert') {
      msg.items.forEach(({ index, text, error }) => {
        const p = window.__ctParagraphs[index];
        if (p) CT.insertTranslation(p.el, index, text, !!error);
      });
      reply({ ok: true });
      return true;
    }
  });
}
