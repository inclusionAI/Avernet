import { evaluate, openPage, closePage, waitFor } from './cdp.mjs';
import { SAFE_SPEECH_TEXT, sleep } from './runtime.mjs';

/* ------------------------------------------------- keyboard walkthrough -- */

const KEYS = {
  Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 },
  Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
  Space: { key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' ' },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38 },
  Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 },
};

async function pressKey(cdp, name, { shift = false } = {}) {
  const spec = KEYS[name];
  if (!spec) throw new Error(`unknown key ${name}`);
  const modifiers = shift ? 8 : 0;
  const base = { key: spec.key, code: spec.code, nativeVirtualKeyCode: spec.windowsVirtualKeyCode, modifiers };
  if (spec.text !== undefined && !shift) {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, windowsVirtualKeyCode: spec.windowsVirtualKeyCode, text: spec.text, unmodifiedText: spec.text });
  } else {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, windowsVirtualKeyCode: spec.windowsVirtualKeyCode });
  }
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base, windowsVirtualKeyCode: spec.windowsVirtualKeyCode });
  await sleep(120);
}

async function typeSafeSpeech(cdp) {
  await cdp.send('Input.insertText', { text: SAFE_SPEECH_TEXT });
  await sleep(150);
}

async function activeElement(page) {
  return evaluate(page.cdp, `(() => {
    const active = document.activeElement;
    if (!active) return null;
    return {
      tag: active.tagName,
      type: active.getAttribute('type'),
      ariaLabel: active.getAttribute('aria-label'),
      text: (active.textContent || '').trim().slice(0, 40),
      value: active.value ?? null,
      checked: typeof active.checked === 'boolean' ? active.checked : null,
      disabled: typeof active.disabled === 'boolean' ? active.disabled : null,
      inDialog: !!active.closest('[role="dialog"]'),
    };
  })()`);
}

async function dockSnapshot(page) {
  return evaluate(page.cdp, `(() => ({
    submittedNotice: document.body.innerText.includes('提交成功，等待其他玩家和主持人。'),
    submittedHeader: document.body.innerText.includes('✓ 已提交'),
    dockTitle: document.querySelector('[data-region="dock-header"]')?.innerText?.replace(/\\s+/g, ' ').trim() ?? null,
    checks: [...document.querySelectorAll('[role="alert"]')].map((node) => node.textContent.trim()),
    abstainInput: !!document.querySelector('input[value="__abstain__"]'),
    checkedVote: document.querySelector('input[name="vote-choice"]:checked')?.value ?? null,
    checkboxChecked: document.querySelector('[data-region="dock-body"] input[type="checkbox"]')?.checked ?? null,
    textareaValue: document.querySelector('textarea')?.value ?? null,
    dialogLabel: document.querySelector('[role="dialog"]')?.getAttribute('aria-label') ?? null,
  }))()`);
}

async function waitForSubmit(page, { timeout = 6_000 } = {}) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await dockSnapshot(page);
    if (last.submittedNotice && last.submittedHeader) return { pass: true, snapshot: last };
    await sleep(100);
  }
  return { pass: false, snapshot: last };
}

async function toggleState(page) {
  return evaluate(page.cdp, `(() => {
    const rect = (node) => {
      if (!node) return null;
      const value = node.getBoundingClientRect();
      return { x: value.x, y: value.y, width: value.width, height: value.height, bottom: value.bottom };
    };
    const visible = (node) => {
      if (!node) return false;
      const value = node.getBoundingClientRect();
      return value.width > 0 && value.height > 0 && value.top < innerHeight && value.bottom > 0 && value.left < innerWidth && value.right > 0;
    };
    const board = document.querySelector('[data-region="workflow-board"]');
    const toggle = document.querySelector('[data-region="workflow-toggle"]');
    const footer = document.querySelector('[data-region="dock-footer"]');
    const submit = document.querySelector('[data-region="dock-footer"] button[type="submit"]');
    const controls = toggle?.getAttribute('aria-controls');
    return {
      collapsed: board ? board.getAttribute('data-workflow-collapsed') : null,
      expanded: board ? board.getAttribute('data-workflow-expanded') : null,
      togglePresent: Boolean(toggle),
      toggleLabel: toggle?.textContent?.trim() ?? null,
      ariaExpanded: toggle?.getAttribute('aria-expanded') ?? null,
      ariaControls: controls ?? null,
      ariaControlsTargetExists: Boolean(controls && document.getElementById(controls)),
      toggleVisible: visible(toggle),
      toggleRect: rect(toggle),
      toggleFocused: document.activeElement === toggle,
      nodes: document.querySelectorAll('[data-workflow-node="true"]').length,
      edges: document.querySelectorAll('[data-workflow-edge="true"]').length,
      footerVisible: visible(footer),
      submitVisible: visible(submit),
      docScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
    };
  })()`);
}

/**
 * Runs one keyboard scenario and records explicit per-step pass/fail plus the
 * exact CDP key sequence. The steps use Input.dispatchKeyEvent only: no
 * synthetic DOM events and no jsdom.
 */
async function runKeyboardScenario(port, spec) {
  const page = await openPage(port, spec);
  const steps = [];
  const step = (action, expected, actual, extra = {}) => {
    const pass = typeof expected === 'function' ? Boolean(expected(actual)) : actual === expected;
    steps.push({ action, expected: extra.expectedLabel ?? String(expected), actual, pass, ...extra.detail });
    return pass;
  };
  const keySequence = [];
  const key = async (name, options) => { keySequence.push(options?.shift ? `Shift+${name}` : name); await pressKey(page.cdp, name, options); };

  try {
    switch (spec.scenario) {
      case 'speech': {
        const initial = await activeElement(page);
        step('page load: action textarea receives focus automatically', (a) => a?.tag === 'TEXTAREA', initial);
        await typeSafeSpeech(page.cdp);
        const typed = await dockSnapshot(page);
        step(`type safe text via Input.insertText (${SAFE_SPEECH_TEXT})`, (s) => s.textareaValue === SAFE_SPEECH_TEXT, typed, { expectedLabel: `textarea value === "${SAFE_SPEECH_TEXT}"` });
        await key('Tab');
        const focused = await activeElement(page);
        step('Tab: focus moves from textarea to submit button', (a) => a?.tag === 'BUTTON' && a?.type === 'submit' && /提交发言/.test(a?.text ?? ''), focused);
        await key('Enter');
        const submitted = await waitForSubmit(page);
        step('Enter: speech submitted', (r) => r.pass, submitted, { expectedLabel: 'dock shows ✓ 已提交 / 提交成功，等待其他玩家和主持人。' });
        break;
      }
      case 'vote': {
        const initial = await activeElement(page);
        step('page load: first vote candidate radio receives focus', (a) => a?.tag === 'INPUT' && a?.type === 'radio', initial);
        await key('ArrowDown');
        const selected = await dockSnapshot(page);
        step('ArrowDown: next candidate selected', (s) => s.checkedVote === 'c', selected, { expectedLabel: 'checked radio value === "c"' });
        await key('Tab');
        const confirmFocus = await activeElement(page);
        step('Tab: focus moves from radio group to confirmation checkbox', (a) => a?.type === 'checkbox', confirmFocus);
        await key('Space');
        const confirmed = await dockSnapshot(page);
        step('Space: confirmation checkbox checked', (s) => s.checkboxChecked === true, confirmed);
        await key('Tab');
        const submitFocus = await activeElement(page);
        step('Tab: focus moves to submit button (确认并投票)', (a) => a?.tag === 'BUTTON' && a?.type === 'submit' && /确认并投票/.test(a?.text ?? ''), submitFocus);
        await key('Enter');
        const submitted = await waitForSubmit(page);
        step('Enter: vote submitted', (r) => r.pass, submitted);
        break;
      }
      case 'abstain': {
        const initial = await activeElement(page);
        step('page load: first vote candidate radio receives focus', (a) => a?.tag === 'INPUT' && a?.type === 'radio', initial);
        await key('ArrowUp');
        const abstain = await dockSnapshot(page);
        step('ArrowUp: wraps to the abstain radio (弃权)', (s) => s.checkedVote === '__abstain__', abstain, { expectedLabel: 'checked radio value === "__abstain__"' });
        await key('Tab');
        const confirmFocus = await activeElement(page);
        step('Tab: focus moves to confirmation checkbox', (a) => a?.type === 'checkbox', confirmFocus);
        await key('Space');
        const confirmed = await dockSnapshot(page);
        step('Space: abstain confirmation checked', (s) => s.checkboxChecked === true, confirmed);
        await key('Tab');
        const submitFocus = await activeElement(page);
        step('Tab: focus moves to submit button (确认并投票)', (a) => a?.tag === 'BUTTON' && a?.type === 'submit', submitFocus);
        await key('Enter');
        const submitted = await waitForSubmit(page);
        step('Enter: abstain ballot submitted', (r) => r.pass, submitted);
        break;
      }
      case 'pk-vote': {
        const initial = await activeElement(page);
        const initialDock = await dockSnapshot(page);
        step('page load: PK candidate radio receives focus', (a) => a?.tag === 'INPUT' && a?.type === 'radio', initial);
        step('No abstain input is offered in PK vote', (s) => s.abstainInput === false, initialDock, { expectedLabel: 'input[value="__abstain__"] absent' });
        await key('Space');
        const selected = await dockSnapshot(page);
        step('Space: PK candidate selected', (s) => s.checkedVote === 'c', selected, { expectedLabel: 'checked radio value === "c"' });
        await key('Tab');
        const confirmFocus = await activeElement(page);
        step('Tab: focus moves to confirmation checkbox', (a) => a?.type === 'checkbox', confirmFocus);
        await key('Space');
        const confirmed = await dockSnapshot(page);
        step('Space: PK confirmation checked', (s) => s.checkboxChecked === true, confirmed);
        await key('Tab');
        const submitFocus = await activeElement(page);
        step('Tab: focus moves to submit button (确认并投票)', (a) => a?.tag === 'BUTTON' && a?.type === 'submit', submitFocus);
        await key('Enter');
        const submitted = await waitForSubmit(page);
        step('Enter: PK vote submitted', (r) => r.pass, submitted);
        break;
      }
      case 'finished': {
        await waitFor(page.cdp, `!!document.querySelector('[role="dialog"][aria-label="游戏结束"]')`, { label: 'game-over dialog' });
        const initial = await activeElement(page);
        step('result dialog auto-opens and traps focus on close button', (a) => a?.inDialog && /回到圆桌/.test(a?.text ?? ''), initial);
        const inert = await evaluate(page.cdp, `document.querySelectorAll('[data-game-content][inert]').length`);
        step('background game content is inert while dialog is open', (n) => n >= 3, inert);
        const cycle = [];
        for (let index = 0; index < 3; index += 1) {
          await key('Tab');
          cycle.push(await activeElement(page));
        }
        step('Tab cycles inside the dialog (focus never escapes)', (elements) => elements.every((a) => a?.inDialog) && new Set(elements.map((a) => a?.text)).size >= 2, cycle);
        await key('Escape');
        await sleep(400);
        const closed = await dockSnapshot(page);
        const closedFocus = await activeElement(page);
        step('Escape closes the dialog and restores focus to 查看终局', (a) => a?.tag === 'BUTTON' && /查看终局/.test(a?.text ?? '') && a?.inDialog === false, closedFocus);
        step('dock shows the result state with 查看终局 trigger', (s) => s.dialogLabel === null, closed);
        await key('Enter');
        await sleep(400);
        const reopened = await activeElement(page);
        step('Enter on 查看终局 reopens the result dialog', (a) => a?.inDialog && /回到圆桌/.test(a?.text ?? ''), reopened);
        break;
      }
      case 'narrow-speech': {
        const initial = await activeElement(page);
        step('page load: speech textarea receives focus at 320x600', (a) => a?.tag === 'TEXTAREA', initial);
        const geometry = await evaluate(page.cdp, `(() => {
          const area = (rect) => {
            if (!rect) return 0;
            const left = Math.max(0, rect.left), top = Math.max(0, rect.top);
            const right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
            return Math.max(0, right - left) * Math.max(0, bottom - top);
          };
          const textarea = document.querySelector('textarea');
          const dockBody = document.querySelector('[data-region="dock-body"]');
          const dockFooter = document.querySelector('[data-region="dock-footer"]');
          const submit = document.querySelector('[data-region="dock-footer"] button[type="submit"]');
          const rect = (node) => node ? { x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y, width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height, bottom: node.getBoundingClientRect().bottom } : null;
          return {
            textarea: rect(textarea), dockBody: rect(dockBody), dockFooter: rect(dockFooter), submit: rect(submit),
            textareaVisibleInDockBody: area(textarea?.getBoundingClientRect()) > 0 && !!dockBody && area(dockBody.getBoundingClientRect()) > 0,
            dockBodyScrollTop: dockBody?.scrollTop ?? null,
            dockFooterVisible: area(dockFooter?.getBoundingClientRect()) > 0,
            submitVisible: area(submit?.getBoundingClientRect()) > 0,
          };
        })()`);
        step('focused textarea intersects the visible dock viewport', (g) => g.textareaVisibleInDockBody === true, geometry);
        step('dock footer and submit button remain visible at 320x600', (g) => g.dockFooterVisible && g.submitVisible, geometry);

        // Tab forward from the auto-focused textarea: submit -> secondary ->
        // header refresh -> workflow toggle (the toggle is the Scene's first
        // focusable control, so it is reachable without entering the roster).
        await key('Tab');
        const tab1 = await activeElement(page);
        step('Tab: focus moves to the submit button (提交发言)', (a) => a?.tag === 'BUTTON' && a?.type === 'submit', tab1);
        await key('Tab');
        const tab2 = await activeElement(page);
        step('Tab: focus moves to 告诉主持人卡住了', (a) => a?.tag === 'BUTTON' && /告诉主持人卡住了/.test(a?.text ?? ''), tab2);
        await key('Tab');
        const tab3 = await activeElement(page);
        step('Tab: focus moves to the header refresh button', (a) => a?.tag === 'BUTTON' && /刷新/.test(a?.text ?? ''), tab3);
        await key('Tab');
        const onToggle = await activeElement(page);
        step('Tab: focus reaches the 查看流程 workflow toggle', (a) => a?.tag === 'BUTTON' && /查看流程/.test(a?.text ?? ''), onToggle);
        const collapsedState = await toggleState(page);
        step('collapsed default: aria-expanded=false, aria-controls target exists, 0 rendered nodes and toggle visible', (s) => s.toggleVisible && s.ariaExpanded === 'false' && s.ariaControlsTargetExists && s.nodes === 0 && s.collapsed === 'true' && s.expanded === 'false', collapsedState);

        await key('Enter');
        const expandedState = await toggleState(page);
        step('Enter on toggle: real graph expands (aria-expanded=true, label 收起流程, node cards rendered)', (s) => s.collapsed === 'false' && s.expanded === 'true' && s.ariaExpanded === 'true' && s.toggleLabel === '收起流程' && s.nodes > 0 && s.edges > 0, expandedState);
        step('expanded state: focus stays on the toggle and dock footer stays visible', (s) => s.toggleFocused === true && s.footerVisible === true && s.submitVisible === true, expandedState);
        step('expanded state: no page horizontal overflow', (s) => s.docScrollWidth <= s.innerWidth + 1, expandedState);

        await key('Space');
        const recollapsedState = await toggleState(page);
        step('Space on toggle: board collapses again (aria-expanded=false, 0 nodes) and focus stays on the toggle', (s) => s.collapsed === 'true' && s.expanded === 'false' && s.ariaExpanded === 'false' && s.nodes === 0 && s.toggleFocused === true, recollapsedState);

        // Back to the textarea with Shift+Tab: refresh -> document body ->
        // secondary -> submit -> textarea (Chrome wraps at the document edge).
        const returnPath = [];
        for (let index = 0; index < 5; index += 1) {
          await key('Tab', { shift: true });
          returnPath.push(await activeElement(page));
        }
        step('Shift+Tab x5: focus returns to the speech textarea', (elements) => elements[elements.length - 1]?.tag === 'TEXTAREA', returnPath, {
          expectedLabel: 'final active element is the speech textarea',
          detail: { observedPath: returnPath.map((a) => `${a?.tag}${a?.text ? `:${a.text.slice(0, 12)}` : ''}`) },
        });
        await typeSafeSpeech(page.cdp);
        const typed = await dockSnapshot(page);
        step(`type safe text via Input.insertText (${SAFE_SPEECH_TEXT})`, (s) => s.textareaValue === SAFE_SPEECH_TEXT, typed);
        await key('Tab');
        const submitFocus = await activeElement(page);
        step('Tab: focus moves to the submit button', (a) => a?.tag === 'BUTTON' && a?.type === 'submit', submitFocus);
        await key('Enter');
        const submitted = await waitForSubmit(page);
        step('Enter: speech submitted after expanding/collapsing the board', (r) => r.pass, submitted);
        break;
      }
      default:
        throw new Error(`unknown keyboard scenario ${spec.scenario}`);
    }
  } finally {
    await closePage(port, page);
  }
  return {
    scenario: spec.scenario,
    viewport: { width: spec.width, height: spec.height },
    mode: spec.mode,
    keySequence,
    steps,
    consoleErrors: page.consoleEntries,
    pass: steps.every((item) => item.pass),
  };
}


export { pressKey, typeSafeSpeech, activeElement, dockSnapshot, waitForSubmit, toggleState, runKeyboardScenario };
