(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const isPresenter = new URLSearchParams(location.search).get('presenter') === '1';
  const starts = slides.map((_, index) => slides.slice(0, index).reduce((total, slide) => total + slide.duration, 0));
  const totalDuration = slides.reduce((total, slide) => total + slide.duration, 0);
  const mainSlides = slides.filter((slide) => slide.duration > 0).length;
  const appendixSlides = slides.filter((slide) => slide.section === '附录').length;
  const qaSlides = slides.length - mainSlides - appendixSlides;
  const extraLabel = (slide) => slide.section === '附录' ? '附录 / 按需展开' : '问答 / 主讲之外';
  $('overview-summary').textContent = `${mainSlides} 页主讲 / ${totalDuration / 60} 分钟　另有 ${qaSlides} 页问答、${appendixSlides} 页附录`;
  const pad = (number) => String(number).padStart(2, '0');
  const clock = (seconds) => `${pad(Math.floor(seconds / 60))}:${pad(Math.floor(seconds % 60))}`;
  const parseHash = () => {
    const match = /^#slide-(\d+)$/.exec(location.hash);
    return match ? Math.min(slides.length - 1, Math.max(0, Number(match[1]) - 1)) : 0;
  };
  let current = parseHash();
  let presenterWindow = null;
  let accumulated = 0;
  let timerStarted = null;
  let lastElapsed = 0;
  let idleTimeout;
  let toastTimeout;
  let connected = false;
  let touchStart = null;
  const elements = slides.map((slide, index) => {
    const section = document.createElement('section');
    section.className = `slide ${slide.theme} ${slide.layout}`;
    section.setAttribute('role', 'group');
    section.setAttribute('aria-roledescription', '幻灯片');
    section.setAttribute('aria-label', `${index + 1} / ${slides.length}：${slide.title}`);
    section.id = `slide-${index + 1}`;
    section.dataset.duration = slide.duration;
    section.innerHTML = slide.html;
    const footer = document.createElement('div');
    footer.className = 'slide-footer';
    footer.innerHTML = `<span>${slide.section}</span><span class="folio">${pad(index + 1)} / ${pad(slides.length)}</span>`;
    section.appendChild(footer);
    $('deck').appendChild(section);
    return section;
  });
  const focusables = elements.map((element) => Array.from(element.querySelectorAll('a,button,input,select,textarea,[tabindex]')).map((node) => ({node, tabIndex:node.getAttribute('tabindex')})));
  function fit() {
    const scale = Math.min(window.innerWidth / 1600, window.innerHeight / 900);
    document.documentElement.style.setProperty('--scale', String(scale));
    if (isPresenter) $('presenter-preview').style.setProperty('--preview-scale', String($('presenter-preview').clientWidth / 1600));
  }
  function elapsedSeconds() { return Math.floor((accumulated + (timerStarted === null ? 0 : performance.now() - timerStarted)) / 1000); }
  function sendState() {
    if (presenterWindow && !presenterWindow.closed) presenterWindow.postMessage({type:'avernet:state', index:current, elapsed:elapsedSeconds(), running:timerStarted !== null}, '*');
  }
  function toast(message) {
    clearTimeout(toastTimeout);
    $('toast').textContent = message;
    $('toast').hidden = false;
    toastTimeout = setTimeout(() => { $('toast').hidden = true; }, 4000);
  }
  function renderPresenter(elapsed = lastElapsed, running = false) {
    lastElapsed = elapsed;
    const slide = slides[current];
    const preview = elements[current].cloneNode(true);
    preview.removeAttribute('id');
    preview.removeAttribute('inert');
    preview.setAttribute('aria-hidden', 'true');
    preview.classList.add('active');
    $('presenter-preview').replaceChildren(preview);
    $('speaker-copy').replaceChildren();
    const heading = document.createElement('h2');
    heading.textContent = slide.title;
    $('speaker-copy').appendChild(heading);
    for (const note of slide.notes) {
      const paragraph = document.createElement('p');
      paragraph.textContent = note;
      $('speaker-copy').appendChild(paragraph);
    }
    if (slide.sources.length) {
      const sources = document.createElement('div');
      sources.className = 'speaker-source';
      sources.textContent = '内容来源';
      for (const key of slide.sources) {
        const path = SOURCES[key];
        const link = document.createElement('a');
        link.href = `${REPO_URL}/blob/dev/${path}`;
        link.target = '_blank'; link.rel = 'noopener'; link.textContent = path;
        sources.appendChild(link);
      }
      $('speaker-copy').appendChild(sources);
    }
    const schedule = slide.duration
      ? `本页建议 ${Math.floor(slide.duration / 60)} 分 ${slide.duration % 60} 秒<br>计划时段 ${clock(starts[current])} — ${clock(starts[current] + slide.duration)}`
      : `${extraLabel(slide)}，不计入主讲`;
    $('presenter-schedule').innerHTML = `<b>${slide.section}</b><br>${schedule}<br>主讲总时长 ${clock(totalDuration)}`;
    $('presenter-next').textContent = current + 1 < slides.length ? `下一页：${slides[current + 1].title}` : '已到最后一页';
    $('presenter-count').textContent = `${pad(current + 1)} / ${slides.length}`;
    $('presenter-prev').disabled = current === 0;
    $('presenter-next-button').disabled = current === slides.length - 1;
    updateTimer(elapsed, running);
    fit();
  }
  function updateTimer(elapsed, running) {
    lastElapsed = elapsed;
    $('elapsed').textContent = clock(elapsed);
    $('elapsed').style.color = elapsed > totalDuration ? '#ffbd87' : '#9beac5';
    $('timer-toggle').textContent = running ? '暂停计时' : elapsed ? '继续计时' : '开始计时';
  }
  function setSlide(index, {replaceHash = true, silent = false} = {}) {
    if (!Number.isInteger(index)) return;
    current = Math.max(0, Math.min(slides.length - 1, index));
    elements.forEach((element, i) => {
      const active = i === current;
      element.classList.toggle('active', active);
      element.setAttribute('aria-hidden', String(!active));
      element.inert = !active;
      for (const {node, tabIndex} of focusables[i]) {
        if (!active) node.setAttribute('tabindex', '-1');
        else if (tabIndex === null) node.removeAttribute('tabindex');
        else node.setAttribute('tabindex', tabIndex);
      }
    });
    if (replaceHash) {
      try { history.replaceState(null, '', `#slide-${current + 1}`); }
      catch { if (location.hash !== `#slide-${current + 1}`) location.hash = `slide-${current + 1}`; }
    }
    $('slide-count').textContent = `${pad(current + 1)} / ${pad(slides.length)}`;
    $('progress-fill').style.width = `${(current + 1) / slides.length * 100}%`;
    $('prev').disabled = current === 0;
    $('next').disabled = current === slides.length - 1;
    $('live-status').textContent = `${current + 1} / ${slides.length}，${slides[current].title}`;
    document.title = `${slides[current].title} · Avernet Keynote`;
    document.querySelectorAll('.overview-slide').forEach((button, i) => button.setAttribute('aria-current', String(i === current)));
    if (isPresenter) renderPresenter();
    else if (!silent) sendState();
  }
  function navigate(index) {
    if (isPresenter && window.opener && !window.opener.closed) window.opener.postMessage({type:'avernet:navigate',index}, '*');
    else setSlide(index);
  }
  function openOverview() {
    if (isPresenter) return;
    $('overview').showModal();
    $('overview-grid').children[current]?.focus();
  }
  function openPresenter() {
    if (isPresenter) return;
    if (presenterWindow && !presenterWindow.closed) { presenterWindow.focus(); sendState(); return; }
    const url = new URL(location.href);
    url.searchParams.set('presenter', '1');
    presenterWindow = window.open(url.href, 'avernet-keynote-presenter', 'popup,width=1280,height=820');
    if (!presenterWindow) toast('浏览器阻止了讲者窗口，请允许本页面打开弹窗后重试。');
  }
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
      else toast('当前浏览器不支持页面全屏，请使用浏览器的全屏功能。');
    } catch { toast('无法进入全屏，请使用浏览器的全屏功能。'); }
  }
  function showControls() {
    document.body.classList.remove('idle');
    clearTimeout(idleTimeout);
    idleTimeout = setTimeout(() => {
      if (!$('controls').contains(document.activeElement) && !document.querySelector('dialog[open]')) document.body.classList.add('idle');
    }, 2700);
  }
  function toggleTimer() {
    if (timerStarted !== null) { accumulated += performance.now() - timerStarted; timerStarted = null; }
    else timerStarted = performance.now();
    sendState();
  }
  for (const [index, slide] of slides.entries()) {
    const button = document.createElement('button');
    button.className = 'overview-slide'; button.type = 'button';
    button.innerHTML = `<span class="overview-top"><span>${pad(index + 1)}</span><span>${slide.section}</span></span><strong>${slide.title}</strong><small>${slide.duration ? `${clock(starts[index])} — ${clock(starts[index] + slide.duration)}` : extraLabel(slide)}</small>`;
    button.addEventListener('click', () => { setSlide(index); $('overview').close(); });
    $('overview-grid').appendChild(button);
  }
  $('prev').addEventListener('click', () => navigate(current - 1));
  $('next').addEventListener('click', () => navigate(current + 1));
  $('overview-button').addEventListener('click', openOverview);
  $('notes-button').addEventListener('click', openPresenter);
  $('fullscreen-button').addEventListener('click', toggleFullscreen);
  $('help-button').addEventListener('click', () => $('help').showModal());
  $('blackout').addEventListener('click', () => { $('blackout').hidden = true; showControls(); });
  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(button.dataset.close).close()));
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.addEventListener('click', (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
    dialog.addEventListener('close', showControls);
  }
  document.addEventListener('keydown', (event) => {
    const tag = event.target.tagName;
    if (event.altKey || event.ctrlKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(tag) || event.target.isContentEditable) return;
    if (document.querySelector('dialog[open]')) return;
    if (!isPresenter && !$('blackout').hidden) { $('blackout').hidden = true; event.preventDefault(); return; }
    if ((event.key === ' ' || event.key === 'Enter') && event.target.closest('button,a')) return;
    const key = event.key.toLowerCase();
    if (['arrowright','arrowdown','pagedown',' '].includes(key)) { event.preventDefault(); navigate(current + 1); }
    else if (['arrowleft','arrowup','pageup'].includes(key)) { event.preventDefault(); navigate(current - 1); }
    else if (key === 'home') { event.preventDefault(); navigate(0); }
    else if (key === 'end') { event.preventDefault(); navigate(slides.length - 1); }
    else if (key === 'o') openOverview();
    else if (key === 'n') openPresenter();
    else if (key === 'f') toggleFullscreen();
    else if (key === 'b' && !isPresenter) $('blackout').hidden = false;
    else if (key === 'p' && !isPresenter) { event.preventDefault(); window.print(); }
    else if (key === '?' && !isPresenter) $('help').showModal();
    showControls();
  });
  $('viewport').addEventListener('touchstart', (event) => {
    if (event.touches.length === 1 && !event.target.closest('a,button')) touchStart = {x:event.touches[0].clientX, y:event.touches[0].clientY};
  }, {passive:true});
  $('viewport').addEventListener('touchend', (event) => {
    if (!touchStart || event.changedTouches.length !== 1) return;
    const dx = event.changedTouches[0].clientX - touchStart.x;
    const dy = event.changedTouches[0].clientY - touchStart.y;
    if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.4) navigate(current + (dx < 0 ? 1 : -1));
    touchStart = null; showControls();
  }, {passive:true});
  $('viewport').addEventListener('touchcancel', () => { touchStart = null; }, {passive:true});
  window.addEventListener('message', (event) => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (isPresenter) {
      if (event.source !== window.opener || data.type !== 'avernet:state') return;
      connected = true; $('presenter-warning').hidden = true;
      if (Number.isInteger(data.index) && data.index >= 0 && data.index < slides.length) {
        if (current !== data.index) { setSlide(data.index, {silent:true}); $('speaker-copy').scrollTop = 0; }
        updateTimer(data.elapsed, data.running);
      }
    } else {
      if (!presenterWindow || event.source !== presenterWindow) return;
      if (data.type === 'avernet:ready') sendState();
      else if (data.type === 'avernet:navigate' && Number.isInteger(data.index)) setSlide(data.index);
      else if (data.type === 'avernet:timer-toggle') toggleTimer();
      else if (data.type === 'avernet:timer-reset') { accumulated = 0; timerStarted = null; sendState(); }
    }
  });
  $('presenter-prev').addEventListener('click', () => navigate(current - 1));
  $('presenter-next-button').addEventListener('click', () => navigate(current + 1));
  $('timer-toggle').addEventListener('click', () => {
    if (window.opener && !window.opener.closed) window.opener.postMessage({type:'avernet:timer-toggle'}, '*');
    else toast('请从主窗口重新打开讲者视图，以使用计时。');
  });
  $('timer-reset').addEventListener('click', () => {
    if (window.opener && !window.opener.closed) window.opener.postMessage({type:'avernet:timer-reset'}, '*');
  });
  window.addEventListener('resize', fit);
  window.addEventListener('hashchange', () => setSlide(parseHash(), {replaceHash:false}));
  document.addEventListener('pointermove', showControls, {passive:true});
  document.addEventListener('focusin', showControls);
  document.addEventListener('fullscreenchange', () => { $('fullscreen-button').textContent = document.fullscreenElement ? '退出全屏' : '全屏'; fit(); });
  if (isPresenter) {
    document.body.classList.add('presenter-mode');
    if (window.opener && !window.opener.closed) window.opener.postMessage({type:'avernet:ready'}, '*');
    setTimeout(() => { if (!connected) $('presenter-warning').hidden = false; }, 1600);
    new ResizeObserver(fit).observe($('presenter-preview'));
  } else {
    setInterval(sendState, 1000);
  }
  setSlide(current); fit(); showControls();
})();
