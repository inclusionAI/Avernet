(() => {
  'use strict';
  const main = document.querySelector('main');
  const nav = document.querySelector('.topbar nav');
  if (!main || !nav) return;

  const storageKey = `bcn-document-comments:${location.pathname}`;
  const documentTitle = main.querySelector('h1').textContent;
  const launch = document.createElement('button');
  launch.type = 'button';
  launch.className = 'review-launch';
  launch.setAttribute('aria-controls', 'review-panel');
  launch.setAttribute('aria-expanded', 'false');
  nav.prepend(launch);

  const panel = document.createElement('section');
  panel.id = 'review-panel';
  panel.className = 'review-panel';
  panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-labelledby', 'review-title');
  panel.innerHTML = `
    <div class="review-heading"><h2 id="review-title">评审评论</h2><button type="button" id="review-close" aria-label="关闭评论">关闭</button></div>
    <p class="review-status" id="review-status" role="status" aria-live="polite"></p>
    <div class="review-actions">
      <button type="button" id="review-new">添加评论</button>
      <button type="button" id="review-export-md" title="导出全部评论，包含已归档">导出 Markdown</button>
      <button type="button" id="review-export-json" title="导出全部评论，包含已归档">导出 JSON</button>
      <label class="review-export-option"><input type="checkbox" id="review-export-archived" checked>导出时包含已归档评论</label>
    </div>
    <div class="review-filters" role="group" aria-label="评论筛选">
      <button type="button" id="review-active" aria-pressed="true">待处理 (0)</button>
      <button type="button" id="review-archived" aria-pressed="false">已归档 (0)</button>
    </div>
    <div class="review-scroll">
      <form class="review-form" id="review-form" hidden>
        <p class="review-context" id="review-context"></p>
        <blockquote class="review-quote" id="review-quote" hidden></blockquote>
        <label for="review-author">姓名（可选）</label>
        <input id="review-author" autocomplete="name" placeholder="你的名字">
        <label for="review-text">评论</label>
        <textarea id="review-text" required placeholder="写下问题或修改建议…"></textarea>
        <div class="review-form-actions"><button class="review-save" type="submit">保存评论</button><button type="button" id="review-discard">取消</button><span class="review-shortcut">⌘ / Ctrl + Enter 保存</span></div>
      </form>
      <div id="review-list"></div>
    </div>`;
  document.body.append(panel);
  const find = id => panel.querySelector(`#${id}`);
  const form = find('review-form');
  const textarea = find('review-text');
  const author = find('review-author');
  const list = find('review-list');
  const status = find('review-status');
  const selectionButton = document.createElement('button');
  selectionButton.type = 'button';
  selectionButton.className = 'review-selection';
  selectionButton.textContent = '评论选中文字';
  selectionButton.hidden = true;
  document.body.append(selectionButton);

  let comments = [];
  let commentView = 'active';
  let selectedAnchor = null;
  let draftAnchor = null;
  let editingId = null;
  let highlighted = null;
  let highlightTimer;
  let lastFocus;

  function announce(message, error = false) {
    status.textContent = message;
    status.dataset.error = String(error);
  }

  function readStore() {
    const value = localStorage.getItem(storageKey);
    if (!value) return [];
    const data = JSON.parse(value);
    if (data.version !== 1 || !Array.isArray(data.comments)) throw new Error('invalid store');
    for (const item of data.comments) {
      if (!item || typeof item.id !== 'string' || typeof item.text !== 'string' ||
          typeof item.author !== 'string' || typeof item.createdAt !== 'string' ||
          typeof item.updatedAt !== 'string' || !item.anchor ||
          (item.archived !== undefined && typeof item.archived !== 'boolean') ||
          !['quote', 'prefix', 'suffix', 'sectionId', 'sectionTitle'].every(key => typeof item.anchor[key] === 'string')) {
        throw new Error('invalid comment');
      }
    }
    return data.comments.map(item => ({...item, archived: item.archived ?? false}));
  }

  function load() {
    try {
      comments = readStore();
      announce('自动保存在此浏览器。选中文字可评论，也可添加整体意见。');
    } catch {
      announce('无法读取本地评论。请检查浏览器的本地存储设置。', true);
    }
    render();
  }

  function commit(change) {
    try {
      const latest = change(readStore());
      localStorage.setItem(storageKey, JSON.stringify({version: 1, comments: latest}));
      comments = latest;
      render();
      announce('已保存到此浏览器。');
      return true;
    } catch (error) {
      announce(error.message === 'missing comment'
        ? '这条评论已在另一个页面删除，请将草稿保存为新评论。'
        : '保存失败，当前输入已保留。请检查本地存储设置后重试。', true);
      return false;
    }
  }

  function setOpen(open) {
    if (open && panel.hidden) lastFocus = document.activeElement;
    panel.hidden = !open;
    document.body.classList.toggle('review-open', open);
    launch.setAttribute('aria-expanded', String(open));
    if (!open) lastFocus?.focus();
  }

  function sectionFor(node) {
    let result = {sectionId: '', sectionTitle: '整篇文档'};
    for (const heading of main.querySelectorAll('h2[id]')) {
      if (heading.contains(node) || (heading.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) {
        result = {sectionId: heading.id, sectionTitle: heading.textContent};
      }
    }
    return result;
  }

  function selectionAnchor() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!main.contains(range.startContainer) || !main.contains(range.endContainer)) return null;
    const quote = range.toString();
    if (!quote.trim()) return null;
    const before = document.createRange();
    before.selectNodeContents(main);
    before.setEnd(range.startContainer, range.startOffset);
    const start = before.toString().length;
    return {
      ...sectionFor(range.startContainer), quote, start,
      prefix: main.textContent.slice(Math.max(0, start - 40), start),
      suffix: main.textContent.slice(start + quote.length, start + quote.length + 40)
    };
  }

  function updateSelection() {
    selectedAnchor = selectionAnchor();
    selectionButton.hidden = !selectedAnchor;
    if (!selectedAnchor) return;
    const rect = window.getSelection().getRangeAt(0).getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > innerHeight) { selectionButton.hidden = true; return; }
    selectionButton.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 155))}px`;
    selectionButton.style.top = `${Math.max(8, Math.min(rect.bottom + 8, innerHeight - 48))}px`;
  }

  function begin(anchor, item = null) {
    setOpen(true);
    if (!form.hidden && textarea.value.trim() && editingId !== item?.id) {
      announce('请先保存或取消当前草稿，再添加另一条评论。', true);
      textarea.focus();
      return;
    }
    draftAnchor = anchor || {quote: '', prefix: '', suffix: '', sectionId: '', sectionTitle: '整篇文档'};
    if (!item && commentView !== 'active') { commentView = 'active'; render(); }
    editingId = item?.id || null;
    textarea.value = item?.text || '';
    if (item) author.value = item.author;
    find('review-context').textContent = `${editingId ? '编辑评论' : '新评论'} · ${draftAnchor.sectionTitle}`;
    find('review-quote').textContent = draftAnchor.quote;
    find('review-quote').hidden = !draftAnchor.quote;
    form.hidden = false;
    selectionButton.hidden = true;
    selectedAnchor = null;
    textarea.focus();
    form.scrollIntoView({block: 'nearest'});
  }

  function resetForm() {
    form.hidden = true;
    textarea.value = '';
    draftAnchor = null;
    editingId = null;
  }

  function offsetBefore(node) {
    const range = document.createRange();
    range.selectNodeContents(main);
    range.setEndBefore(node);
    return range.toString().length;
  }

  function locate(anchor) {
    const text = main.textContent;
    let lower = 0, upper = text.length;
    const headings = [...main.querySelectorAll('h2[id]')];
    const section = headings.findIndex(item => item.id === anchor.sectionId);
    if (section >= 0) {
      lower = offsetBefore(headings[section]);
      if (headings[section + 1]) upper = offsetBefore(headings[section + 1]);
    }
    const matches = [];
    let from = lower;
    while (from < upper) {
      const start = text.indexOf(anchor.quote, from);
      if (start < 0 || start >= upper) break;
      const end = start + anchor.quote.length;
      const score = Number(Boolean(anchor.prefix) && text.slice(Math.max(0, start - anchor.prefix.length), start) === anchor.prefix)
        + Number(Boolean(anchor.suffix) && text.slice(end, end + anchor.suffix.length) === anchor.suffix);
      matches.push({start, end, score});
      from = start + anchor.quote.length;
    }
    matches.sort((a, b) => b.score - a.score);
    if (!matches.length || (matches.length > 1 && matches[0].score === matches[1].score)) return null;
    const match = matches[0];
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    let offset = 0, started = false;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const next = offset + node.length;
      if (!started && match.start < next) {
        range.setStart(node, match.start - offset);
        started = true;
      }
      if (started && match.end <= next) {
        range.setEnd(node, match.end - offset);
        return range;
      }
      offset = next;
    }
    return null;
  }

  function jump(item, message) {
    const range = locate(item.anchor);
    if (!range) {
      message.textContent = '原文已更新或存在多处匹配，请对照保存的引用查看。';
      return;
    }
    for (const node of [range.startContainer, range.endContainer]) {
      let element = node.parentElement;
      while (element && element !== main) {
        if (element.tagName === 'DETAILS') element.open = true;
        element = element.parentElement;
      }
    }
    if (innerWidth < 1200) setOpen(false);
    highlighted?.classList.remove('review-target');
    highlighted = range.startContainer.parentElement.closest('p,td,th,pre,li,h2,h3,summary') || range.startContainer.parentElement;
    highlighted.classList.add('review-target');
    highlighted.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'smooth'});
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    message.textContent = '';
    clearTimeout(highlightTimer);
    highlightTimer = setTimeout(() => highlighted?.classList.remove('review-target'), 3000);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function action(parent, label, handler) {
    const button = element('button', '', label);
    button.type = 'button';
    button.addEventListener('click', handler);
    parent.append(button);
  }

  function syncExportControls() {
    const includeArchived = find('review-export-archived').checked;
    const hasComments = comments.some(item => includeArchived || !item.archived);
    for (const format of ['md', 'json']) {
      const button = find(`review-export-${format}`);
      button.disabled = !hasComments;
      button.title = includeArchived ? '导出全部评论，包含已归档' : '仅导出待处理评论，不含已归档';
    }
  }

  function render() {
    const archivedCount = comments.filter(item => item.archived).length;
    const activeCount = comments.length - archivedCount;
    launch.textContent = `评论 (${activeCount})`;
    find('review-active').textContent = `待处理 (${activeCount})`;
    find('review-archived').textContent = `已归档 (${archivedCount})`;
    find('review-active').setAttribute('aria-pressed', String(commentView === 'active'));
    find('review-archived').setAttribute('aria-pressed', String(commentView === 'archived'));
    syncExportControls();
    list.replaceChildren();
    const visible = comments.filter(item => item.archived === (commentView === 'archived'));
    if (!visible.length) {
      const empty = commentView === 'archived' ? '还没有已归档评论。'
        : comments.length ? '暂无待处理评论。可切换到“已归档”查看或恢复历史意见。'
        : '还没有评论。选中文档中的文字后点击“评论选中文字”，或添加整篇文档的意见。';
      list.append(element('p', 'review-empty', empty));
      return;
    }
    for (const item of visible.sort((a, b) => b.createdAt.localeCompare(a.createdAt))) {
      const article = element('article', 'review-comment');
      article.dataset.commentId = item.id;
      const header = element('div', 'review-comment-header');
      const time = element('time', '', new Date(item.updatedAt).toLocaleString('zh-CN', {hour12: false}));
      time.dateTime = item.updatedAt;
      header.append(element('strong', '', item.author || '未署名'), time);
      article.append(header, element('p', 'review-context', item.anchor.sectionTitle));
      if (item.anchor.quote) article.append(element('blockquote', 'review-quote', item.anchor.quote));
      article.append(element('p', 'review-text', item.text));
      const message = element('span', 'review-not-found');
      message.setAttribute('role', 'status');
      const actions = element('div', 'review-comment-actions');
      if (item.anchor.quote) action(actions, '定位原文', () => jump(item, message));
      action(actions, '编辑', () => begin(item.anchor, item));
      action(actions, item.archived ? '恢复' : '归档', () => {
        const archived = !item.archived;
        if (commit(items => {
          if (!items.some(value => value.id === item.id)) throw new Error('missing comment');
          return items.map(value => value.id === item.id
            ? {...value, archived, updatedAt: new Date().toISOString()} : value);
        })) {
          announce(archived ? '评论已归档，可在“已归档”中恢复。' : '评论已恢复到待处理。');
          find(commentView === 'active' ? 'review-active' : 'review-archived').focus();
        }
      });
      action(actions, '删除', () => {
        if (commit(items => items.filter(value => value.id !== item.id)) && editingId === item.id) resetForm();
      });
      article.append(actions, message);
      list.append(article);
    }
  }

  function exportComments(format) {
    try {
      const includeArchived = find('review-export-archived').checked;
      const saved = readStore().filter(item => includeArchived || !item.archived);
      if (!saved.length) {
        announce('当前导出范围内没有评论。');
        return;
      }
      const exportedAt = new Date().toISOString();
      const payload = {version: 1, document: {title: documentTitle, path: location.pathname}, exportedAt, comments: saved};
      const markdown = [`# ${documentTitle} · 评审评论`, '', `导出时间：${exportedAt}`, `文档：${location.pathname}`, '',
        ...saved.flatMap((item, index) => [
          `## ${index + 1}. ${item.anchor.sectionTitle}`, '',
          `评论人：${item.author || '未署名'}`, `状态：${item.archived ? '已归档' : '待处理'}`, `创建时间：${item.createdAt}`, `更新时间：${item.updatedAt}`, '',
          ...(item.anchor.quote ? ['原文：', '', ...item.anchor.quote.split('\n').map(line => `> ${line}`), ''] : []),
          '意见：', '', item.text, ''
        ])].join('\n');
      const blob = new Blob([format === 'json' ? JSON.stringify(payload, null, 2) : markdown], {
        type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `bcn-group-a2a-review-${exportedAt.slice(0, 10)}.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const scope = includeArchived ? `含 ${saved.filter(item => item.archived).length} 条已归档` : '不含已归档';
      announce(`已导出 ${saved.length} 条评论（${scope}）${textarea.value.trim() ? '；输入框中的草稿尚未保存' : ''}。`);
    } catch {
      announce('导出失败，请检查本地评论是否可以读取后重试。', true);
    }
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    const text = textarea.value.trim();
    if (!text) { textarea.focus(); return; }
    const now = new Date().toISOString();
    const id = editingId || crypto.randomUUID();
    if (commit(items => {
      if (editingId && !items.some(item => item.id === editingId)) throw new Error('missing comment');
      const existing = items.find(item => item.id === id);
      const updated = {id, text, author: author.value.trim(), anchor: draftAnchor, archived: existing?.archived ?? false, createdAt: existing?.createdAt || now, updatedAt: now};
      return existing ? items.map(item => item.id === id ? updated : item) : [...items, updated];
    })) resetForm();
  });
  textarea.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); form.requestSubmit(); }
  });
  launch.addEventListener('click', () => { setOpen(panel.hidden); if (!panel.hidden) find('review-new').focus(); });
  find('review-close').addEventListener('click', () => setOpen(false));
  find('review-new').addEventListener('click', () => begin(selectionAnchor()));
  find('review-discard').addEventListener('click', () => { resetForm(); find('review-new').focus(); });
  find('review-export-md').addEventListener('click', () => exportComments('md'));
  find('review-export-json').addEventListener('click', () => exportComments('json'));
  find('review-export-archived').addEventListener('change', syncExportControls);
  for (const view of ['active', 'archived']) {
    find(`review-${view}`).addEventListener('click', () => { commentView = view; render(); });
  }
  selectionButton.addEventListener('pointerdown', event => event.preventDefault());
  selectionButton.addEventListener('click', () => { if (selectedAnchor) begin(selectedAnchor); });
  document.addEventListener('selectionchange', updateSelection);
  document.addEventListener('pointerup', updateSelection);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) { setOpen(false); selectionButton.hidden = true; }
  });
  window.addEventListener('scroll', updateSelection, {passive: true});
  window.addEventListener('resize', updateSelection);
  window.addEventListener('storage', event => {
    if (event.key === storageKey || event.key === null) load();
  });
  load();
})();
