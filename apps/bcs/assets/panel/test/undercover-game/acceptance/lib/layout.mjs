import { evaluate, openPage, closePage, waitFor } from './cdp.mjs';
import { measure, layoutChecks, privacyScan } from './audit.mjs';
import { capturePng } from './screenshot.mjs';
import { pressKey, toggleState } from './keyboard.mjs';
import { NARROW_320, NARROW_360, WIDE, sleep } from './runtime.mjs';

/* ------------------------------------------------------- layout sweep ---- */

/**
 * Keyboard-driven expand/collapse probe of the new 查看流程 toggle. Focus is
 * moved with element.focus() (a DOM focus call, not an activation) and both
 * activations use real CDP key events on the focused button.
 */
async function probeToggle(page, viewport, mode) {
  const before = await toggleState(page);
  if (!before.togglePresent) return { present: false };
  await evaluate(page.cdp, `document.querySelector('[data-region="workflow-toggle"]').focus()`);
  const focused = await toggleState(page);
  await pressKey(page.cdp, 'Enter');
  await sleep(350);
  const expanded = await toggleState(page);
  await pressKey(page.cdp, 'Space');
  await sleep(350);
  const collapsed = await toggleState(page);
  const checks = {
    togglePresentAndVisible: before.toggleVisible === true && before.toggleRect.width > 0,
    toggleFocusable: focused.toggleFocused === true,
    ariaControlsTargetExists: before.ariaControlsTargetExists === true,
    startsCollapsed: before.collapsed === 'true' && before.expanded === 'false' && before.ariaExpanded === 'false' && before.nodes === 0,
    enterExpands: expanded.collapsed === 'false' && expanded.expanded === 'true' && expanded.ariaExpanded === 'true' && expanded.toggleLabel === '收起流程' && expanded.nodes > 0 && expanded.edges > 0,
    focusStaysOnToggleExpanded: expanded.toggleFocused === true,
    dockFooterVisibleExpanded: expanded.footerVisible === true && expanded.submitVisible === true,
    noOverflowExpanded: expanded.docScrollWidth <= expanded.innerWidth + 1,
    spaceCollapses: collapsed.collapsed === 'true' && collapsed.expanded === 'false' && collapsed.ariaExpanded === 'false' && collapsed.nodes === 0,
    focusStaysOnToggleCollapsed: collapsed.toggleFocused === true,
    dockFooterVisibleCollapsed: collapsed.footerVisible === true && collapsed.submitVisible === true,
  };
  return {
    present: true,
    viewport,
    mode,
    before,
    expanded,
    collapsed,
    checks,
    pass: Object.values(checks).every(Boolean),
  };
}

async function captureLayoutSweep(port, results) {
  const viewports = [NARROW_320, NARROW_360];
  const modes = ['speech', 'vote', 'pk-vote', 'finished'];
  const rows = [];
  for (const viewport of viewports) {
    for (const mode of modes) {
      const page = await openPage(port, { ...viewport, mode });
      try {
        if (mode === 'finished') await waitFor(page.cdp, `!!document.querySelector('[role="dialog"]')`, { label: 'finished dialog' });
        const measured = await measure(page);
        const check = layoutChecks(measured, viewport, mode);
        const toggle = await probeToggle(page, viewport, mode);
        rows.push({
          viewport: { width: viewport.width, height: viewport.height },
          mode,
          panelRect: measured.panel,
          boardCollapsed: measured.boardCollapsed,
          boardExpanded: measured.boardExpanded,
          renderedNodes: measured.boardNodeCount,
          toggle,
          checks: check.checks,
          pass: check.pass && (toggle.present ? toggle.pass : true),
          failedChecks: [
            ...check.failed,
            ...(toggle.present && !toggle.pass ? ['workflowToggleInteraction'] : []),
          ],
          consoleErrors: page.consoleEntries,
        });
      } finally {
        await closePage(port, page);
      }
    }
  }
  results.layoutSweep = rows;
  results.toggleEvidence = rows.filter((row) => row.toggle.present).map((row) => ({
    viewport: row.viewport,
    mode: row.mode,
    checks: row.toggle.checks,
    before: row.toggle.before,
    expanded: row.toggle.expanded,
    collapsed: row.toggle.collapsed,
    pass: row.toggle.pass,
  }));
}

/* -------------------------------------------------------- screenshots ---- */

async function captureWideScreenshots(port, results) {
  const captures = [];
  const errors = [];

  async function capture(mode, fileName, options = {}) {
    const page = await openPage(port, { ...WIDE, mode });
    try {
      if (options.finale) await waitFor(page.cdp, `!!document.querySelector('[role="dialog"][aria-label="游戏结束"]')`, { label: 'game-over dialog' });
      if (options.prepare) await options.prepare(page);
      const measured = await measure(page);
      const layout = layoutChecks(measured, WIDE, mode);
      const privacy = await privacyScan(page);
      const image = await capturePng(page, fileName);
      captures.push({
        file: fileName,
        mode,
        viewport: { width: WIDE.width, height: WIDE.height },
        panel: measured.panel,
        layout: measured.layout,
        actionActive: measured.actionActive,
        boardCollapsed: measured.boardCollapsed,
        board: measured.board,
        stage: measured.stage,
        dock: measured.dock,
        dockFooter: measured.dockFooter,
        submitButton: measured.submitButton,
        layoutChecks: layout.checks,
        layoutPass: layout.pass,
        ...image,
        synthetic: true,
        proves: options.proves,
        extra: options.extra ? await options.extra(page, measured) : undefined,
        consoleErrors: page.consoleEntries,
      });
      results.privacyScan.pages.push({ file: fileName, mode, ...privacy });
    } catch (error) {
      errors.push({ fileName, mode, error: error.message });
    } finally {
      await closePage(port, page);
    }
  }

  await capture('speech', '01-speech-synthetic.png', {
    proves: 'Human speech turn: masked word card (••••), editable speech textarea and submit footer are visible at 760x1000.',
  });
  await capture('vote', '02-vote-synthetic.png', {
    proves: 'Human vote turn: candidate radios, the 弃权 (abstain) card, the 确认 (confirmation) control and the 确认并投票 submit button are all visible.',
    prepare: async (page) => {
      const prepared = await evaluate(page.cdp, `(() => {
        const body = document.querySelector('[data-region="dock-body"]');
        const abstain = document.querySelector('input[value="__abstain__"]');
        const confirm = document.querySelector('[data-region="dock-body"] input[type="checkbox"]');
        const visible = (node) => {
          if (!node) return false;
          const rect = (node.closest('label') ?? node).getBoundingClientRect();
          const inner = body.getBoundingClientRect();
          return rect.top < inner.bottom && rect.bottom > inner.top && rect.height > 0;
        };
        const needsScroll = !visible(abstain) || !visible(confirm);
        if (needsScroll) body.scrollTop = body.scrollHeight;
        return { needsScroll, scrollTop: body.scrollTop, scrollHeight: body.scrollHeight, clientHeight: body.clientHeight, abstainVisible: visible(abstain), confirmationVisible: visible(confirm) };
      })()`);
      await sleep(250);
      return prepared;
    },
    extra: async (page) => {
      const check = await evaluate(page.cdp, `(() => {
        const body = document.querySelector('[data-region="dock-body"]');
        const within = (node) => {
          if (!node) return false;
          const rect = (node.closest('label') ?? node).getBoundingClientRect();
          const inner = body.getBoundingClientRect();
          const viewport = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
          return rect.top < Math.min(inner.bottom, viewport.bottom) && rect.bottom > Math.max(inner.top, viewport.top) && rect.left < viewport.right && rect.right > viewport.left;
        };
        return {
          abstainCardInView: within(document.querySelector('input[value="__abstain__"]')),
          confirmationControlInView: within(document.querySelector('[data-region="dock-body"] input[type="checkbox"]')),
        };
      })()`);
      return check;
    },
  });
  await capture('pk-vote', '03-pk-vote-synthetic.png', {
    proves: 'Human PK vote turn: PK header badge, single PK candidate, confirmation control and submit button; no abstain card is offered.',
    extra: async (page) => evaluate(page.cdp, `(() => ({
      abstainInputPresent: !!document.querySelector('input[value="__abstain__"]'),
      candidates: document.querySelectorAll('input[name="vote-choice"]').length,
      headerHasPk: /PK/.test(document.querySelector('[data-region="panel-header"]')?.innerText ?? ''),
    }))()`),
  });
  await capture('eliminated', '04-eliminated-synthetic.png', {
    proves: 'Eliminated seat state: the 6号 seat is rendered as eliminated (已淘汰) in the participant roster/scene while the rest of the room stays live.',
    extra: async (page) => evaluate(page.cdp, `(() => ({
      eliminatedVisible: document.body.innerText.includes('已淘汰'),
      eliminatedSeatText: document.querySelector('[data-region="participant-roster"]')?.innerText?.replace(/\\s+/g, ' ').trim() ?? null,
    }))()`),
  });
  await capture('finished', '05-finished-synthetic.png', {
    finale: true,
    proves: 'Finished state: the real GameOverDialog renders the synthetic public finale (winner + recap) with a 回到圆桌 button; no private word or raw ballot is shown.',
    extra: async (page) => evaluate(page.cdp, `(() => ({
      dialogLabel: document.querySelector('[role="dialog"]')?.getAttribute('aria-label') ?? null,
      dialogText: document.querySelector('[role="dialog"]')?.innerText?.replace(/\\s+/g, ' ').trim().slice(0, 240) ?? null,
      focusInsideDialog: !!document.activeElement?.closest('[role="dialog"]'),
      backgroundInert: document.querySelectorAll('[data-game-content][inert]').length,
    }))()`),
  });
  results.screenshots.push(...captures);
  results.wideLayoutChecks = captures.map((capture) => ({
    file: capture.file,
    mode: capture.mode,
    viewport: capture.viewport,
    panel: capture.panel,
    boardCollapsed: capture.boardCollapsed,
    checks: capture.layoutChecks,
    pass: capture.layoutPass,
    failedChecks: Object.entries(capture.layoutChecks).filter(([, check]) => !check.pass).map(([name]) => name),
    consoleErrors: capture.consoleErrors,
  }));
  results.errors.push(...errors);
}

async function captureNarrowScreenshots(port, results) {
  const captures = [];
  const errors = [];

  async function captureNarrow(viewport, mode, fileName, options = {}) {
    const page = await openPage(port, { ...viewport, mode });
    try {
      const prepared = options.prepare ? await options.prepare(page) : undefined;
      const measured = await measure(page);
      const privacy = await privacyScan(page);
      const image = await capturePng(page, fileName);
      captures.push({
        file: fileName,
        mode,
        variant: options.variant ?? 'default',
        viewport: { width: viewport.width, height: viewport.height },
        panel: measured.panel,
        panelSurfaceNote: 'The preview harness adds 12px padding around the panel, so the browser viewport is exactly the target size while the panel surface is 24px smaller.',
        layout: measured.layout,
        actionActive: measured.actionActive,
        boardCollapsed: measured.boardCollapsed,
        boardExpanded: measured.boardExpanded,
        renderedNodes: measured.boardNodeCount,
        workflowToggle: measured.workflowToggle,
        dock: measured.dock,
        dockBody: measured.dockBody,
        dockFooter: measured.dockFooter,
        stage: measured.stage,
        ...image,
        synthetic: true,
        proves: options.proves,
        prepared,
        extra: options.extra ? await options.extra(page, measured) : undefined,
        consoleErrors: page.consoleEntries,
      });
      results.privacyScan.pages.push({ file: fileName, mode, ...privacy });
    } catch (error) {
      errors.push({ fileName, mode, error: error.message });
    } finally {
      await closePage(port, page);
    }
  }

  await captureNarrow(NARROW_320, 'speech', 'narrow-320-speech-synthetic.png', {
    proves: 'Exact 320x600 viewport, speech turn: the workflow board is collapsed, the night-hall stage stays visible and the focused textarea + dock footer stay inside the visible action dock.',
    extra: async (page) => {
      const geometry = await evaluate(page.cdp, `(() => {
        const rect = (node) => { if (!node) return null; const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom }; };
        const body = document.querySelector('[data-region="dock-body"]');
        return { activeTag: document.activeElement?.tagName, textarea: rect(document.querySelector('textarea')), dockBody: rect(body), dockBodyScrollTop: body.scrollTop, dockFooter: rect(document.querySelector('[data-region="dock-footer"]')) };
      })()`);
      return geometry;
    },
  });
  await captureNarrow(NARROW_320, 'speech', 'narrow-320-speech-expanded-synthetic.png', {
    variant: 'expanded',
    proves: 'Exact 320x600 viewport with the workflow board expanded via the 查看流程 toggle: the real graph node cards/edges are rendered (operation still pending) and the action dock with submit footer remains reachable below the scrollable Scene.',
    prepare: async (page) => {
      const point = await evaluate(page.cdp, `(() => {
        const toggle = document.querySelector('[data-region="workflow-toggle"]');
        if (!toggle) return null;
        toggle.focus();
        const rect = toggle.getBoundingClientRect();
        return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, label: toggle.textContent.trim(), ariaExpanded: toggle.getAttribute('aria-expanded') };
      })()`);
      if (!point) throw new Error('workflow toggle not found on 320x600 speech page');
      await page.cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 });
      await page.cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 });
      await sleep(400);
      return { activation: 'CDP Input.dispatchMouseEvent click at toggle center', clickPoint: { x: point.x, y: point.y }, labelBefore: point.label, ariaExpandedBefore: point.ariaExpanded };
    },
    extra: async (page) => {
      const state = await toggleState(page);
      return {
        ...state,
        activation: 'CDP Input.dispatchMouseEvent click at toggle center',
        realGraph: { nodes: state.nodes, edges: state.edges },
        actionDockStillReachable: state.footerVisible === true && state.submitVisible === true,
      };
    },
  });
  await captureNarrow(NARROW_320, 'vote', 'narrow-320-vote-synthetic.png', {
    proves: 'Exact 320x600 viewport, vote turn: dock body is scrolled to expose the 弃权 (abstain) card and the 确认 (confirmation) control while the submit footer remains visible.',
    prepare: async (page) => {
      await evaluate(page.cdp, `(() => { const body = document.querySelector('[data-region="dock-body"]'); body.scrollTop = body.scrollHeight; })()`);
      await sleep(250);
    },
    extra: async (page) => evaluate(page.cdp, `(() => {
      const body = document.querySelector('[data-region="dock-body"]');
      const within = (node) => {
        if (!node) return false;
        const rect = (node.closest('label') ?? node).getBoundingClientRect();
        const inner = body.getBoundingClientRect();
        return rect.top < Math.min(inner.bottom, innerHeight) && rect.bottom > Math.max(inner.top, 0);
      };
      return {
        scrollTop: body.scrollTop, scrollHeight: body.scrollHeight, clientHeight: body.clientHeight,
        abstainCardInView: within(document.querySelector('input[value="__abstain__"]')),
        confirmationControlInView: within(document.querySelector('[data-region="dock-body"] input[type="checkbox"]')),
      };
    })()`),
  });
  await captureNarrow(NARROW_360, 'speech', 'narrow-360-speech-synthetic.png', {
    proves: 'Exact 360x640 viewport, speech turn: collapsed workflow board, visible night-hall stage, focused textarea and visible dock footer.',
    extra: async (page) => {
      const geometry = await evaluate(page.cdp, `(() => {
        const rect = (node) => { if (!node) return null; const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom }; };
        const body = document.querySelector('[data-region="dock-body"]');
        return { activeTag: document.activeElement?.tagName, textarea: rect(document.querySelector('textarea')), dockBody: rect(body), dockBodyScrollTop: body.scrollTop, dockFooter: rect(document.querySelector('[data-region="dock-footer"]')) };
      })()`);
      return geometry;
    },
  });
  results.screenshots.push(...captures);
  results.errors.push(...errors);
}


export { probeToggle, captureLayoutSweep, captureWideScreenshots, captureNarrowScreenshots };
