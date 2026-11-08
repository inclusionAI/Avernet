import { evaluate } from './cdp.mjs';
import { ACTION_MODES, PRIVACY_NEEDLES } from './runtime.mjs';

/* ------------------------------------------------------- measurements ---- */

const MEASURE_EXPRESSION = `(() => {
  const r = (element) => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      x: Math.round(rect.x * 10) / 10, y: Math.round(rect.y * 10) / 10,
      width: Math.round(rect.width * 10) / 10, height: Math.round(rect.height * 10) / 10,
      top: Math.round(rect.top * 10) / 10, bottom: Math.round(rect.bottom * 10) / 10,
      left: Math.round(rect.left * 10) / 10, right: Math.round(rect.right * 10) / 10,
    };
  };
  const q = (selector) => document.querySelector(selector);
  const panel = q('[data-panel-viewport]');
  const board = q('[data-region="workflow-board"]');
  const stage = q('[data-room-layer="night-hall"]');
  const dock = q('[data-region="action-dock"]');
  const dockBody = q('[data-region="dock-body"]');
  const dockFooter = q('[data-region="dock-footer"]');
  const submit = q('[data-region="dock-footer"] button[type="submit"]');
  const abstain = q('input[value="__abstain__"]');
  const confirm = q('[data-region="dock-body"] input[type="checkbox"]');
  const dialog = q('[role="dialog"]');
  const active = document.activeElement;
  const panelRect = r(panel);
  return {
    innerWidth, innerHeight,
    documentElementScrollWidth: document.documentElement.scrollWidth,
    documentElementScrollHeight: document.documentElement.scrollHeight,
    panel: panelRect,
    panelSurface: panelRect,
    layout: panel ? panel.getAttribute('data-layout') : null,
    actionActive: panel ? panel.getAttribute('data-action-active') : null,
    board: r(board),
    boardPresent: !!board,
    boardCollapsed: board ? board.getAttribute('data-workflow-collapsed') : null,
    boardExpanded: board ? board.getAttribute('data-workflow-expanded') : null,
    boardNodeCount: document.querySelectorAll('[data-workflow-node="true"]').length,
    boardEdgeCount: document.querySelectorAll('[data-workflow-edge="true"]').length,
    boardNodeIds: [...document.querySelectorAll('[data-workflow-node="true"]')].map((node) => node.getAttribute('data-node-id')),
    workflowToggle: (() => {
      const toggle = q('[data-region="workflow-toggle"]');
      if (!toggle) return { present: false };
      const insideViewport = (() => {
        const rect = toggle.getBoundingClientRect();
        return rect.top < innerHeight && rect.bottom > 0 && rect.left < innerWidth && rect.right > 0 && rect.width > 0 && rect.height > 0;
      })();
      const controls = toggle.getAttribute('aria-controls');
      return {
        present: true,
        text: (toggle.textContent || '').trim(),
        ariaExpanded: toggle.getAttribute('aria-expanded'),
        ariaControls: controls,
        ariaControlsTargetExists: Boolean(controls && document.getElementById(controls)),
        focusable: toggle.tabIndex >= 0 && !toggle.disabled && typeof toggle.focus === 'function',
        disabled: Boolean(toggle.disabled),
        visibleInViewport: insideViewport,
        focused: active === toggle,
        rect: r(toggle),
      };
    })(),
    sceneScroll: {
      scrollTop: q('[data-region="panel-scene"]')?.scrollTop ?? null,
      clientHeight: q('[data-region="panel-scene"]')?.clientHeight ?? null,
      scrollHeight: q('[data-region="panel-scene"]')?.scrollHeight ?? null,
    },
    stage: r(stage),
    stageSceneScroll: stage ? { height: r(stage).height, sceneScrollTop: q('[data-region="panel-scene"]')?.scrollTop ?? null, sceneClientHeight: q('[data-region="panel-scene"]')?.clientHeight ?? null, sceneScrollHeight: q('[data-region="panel-scene"]')?.scrollHeight ?? null } : null,
    dock: r(dock),
    dockBody: dockBody ? { ...r(dockBody), scrollTop: dockBody.scrollTop, scrollHeight: dockBody.scrollHeight, clientHeight: dockBody.clientHeight } : null,
    dockFooter: r(dockFooter),
    submitButton: submit ? { ...r(submit), label: submit.textContent.trim(), disabled: submit.disabled } : null,
    abstain: abstain ? { value: abstain.value, checked: abstain.checked, rect: r(abstain.closest('label') ?? abstain) } : null,
    confirm: confirm ? { checked: confirm.checked, disabled: confirm.disabled, rect: r(confirm.closest('label') ?? confirm), label: (confirm.closest('label')?.innerText ?? '').trim() } : null,
    candidates: document.querySelectorAll('input[name="vote-choice"]').length,
    wordCardText: q('[data-region="word-card"]')?.innerText?.replace(/\\s+/g, ' ').trim() ?? null,
    showWordPressed: q('[data-region="word-card"] button')?.getAttribute('aria-pressed') ?? null,
    dialog: dialog ? { label: dialog.getAttribute('aria-label'), rect: r(dialog) } : null,
    dialogFocusInside: dialog ? !!active?.closest('[role="dialog"]') : null,
    inertCount: document.querySelectorAll('[data-game-content][inert]').length,
    activeElement: active ? {
      tag: active.tagName,
      type: active.getAttribute('type'),
      ariaLabel: active.getAttribute('aria-label'),
      text: (active.textContent || '').trim().slice(0, 40),
      value: active.value ?? null,
      checked: typeof active.checked === 'boolean' ? active.checked : null,
      inDialog: !!active.closest('[role="dialog"]'),
    } : null,
    bodyText: document.body.innerText,
    resultTriggerVisible: !!q('[data-result-trigger]'),
    dockTitle: q('[data-region="dock-header"]')?.innerText?.replace(/\\s+/g, ' ').trim().slice(0, 160) ?? null,
  };
})()`;

async function measure(page) {
  return evaluate(page.cdp, MEASURE_EXPRESSION);
}

function intersectionArea(rect, width, height) {
  if (!rect) return 0;
  const left = Math.max(0, rect.left);
  const top = Math.max(0, rect.top);
  const right = Math.min(width, rect.right);
  const bottom = Math.min(height, rect.bottom);
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}

function intersectsViewport(rect, width, height) {
  return Boolean(rect) && intersectionArea(rect, width, height) > 0;
}

function layoutChecks(measured, viewport, mode) {
  const actionMode = ACTION_MODES.includes(mode);
  const narrow = measured.panel && measured.panel.width < 460;
  const checks = {
    noPageHorizontalOverflow: {
      pass: measured.documentElementScrollWidth <= measured.innerWidth + 1,
      detail: `${measured.documentElementScrollWidth} <= ${measured.innerWidth} + 1`,
    },
    stageVisibleInViewport: {
      pass: Boolean(measured.stage) && measured.stage.height > 0 && intersectsViewport(measured.stage, viewport.width, viewport.height),
      detail: { rect: measured.stage, intersectionArea: measured.stage ? Math.round(intersectionArea(measured.stage, viewport.width, viewport.height)) : 0 },
    },
    workflowBoardPresent: {
      pass: measured.boardPresent === true,
      detail: { present: measured.boardPresent, collapsed: measured.boardCollapsed },
    },
    workflowBoardCollapsed: {
      expected: Boolean(narrow && actionMode),
      pass: measured.boardCollapsed === String(Boolean(narrow && actionMode)),
      detail: { collapsed: measured.boardCollapsed, expanded: measured.boardExpanded, nodes: measured.boardNodeCount },
    },
    workflowToggleVisibleFocusable: {
      expected: Boolean(narrow && actionMode),
      pass: narrow && actionMode
        ? Boolean(measured.workflowToggle.present && measured.workflowToggle.visibleInViewport && measured.workflowToggle.focusable && measured.workflowToggle.ariaControlsTargetExists)
        : measured.workflowToggle.present === false,
      detail: measured.workflowToggle,
    },
    workflowToggleDefaultsCollapsed: {
      expected: Boolean(narrow && actionMode),
      pass: narrow && actionMode
        ? measured.workflowToggle.ariaExpanded === 'false' && measured.workflowToggle.text === '查看流程' && measured.boardNodeCount === 0
        : true,
      detail: {
        ariaExpanded: measured.workflowToggle.ariaExpanded ?? null,
        label: measured.workflowToggle.text ?? null,
        renderedNodes: measured.boardNodeCount,
      },
    },
    actionDockActive: {
      expected: actionMode,
      pass: measured.actionActive === String(actionMode),
      detail: { actionActive: measured.actionActive },
    },
    actionDockVisible: {
      expected: actionMode,
      pass: actionMode ? intersectsViewport(measured.dock, viewport.width, viewport.height) : true,
      detail: intersectsViewport(measured.dock, viewport.width, viewport.height),
    },
    dockFooterVisible: {
      pass: actionMode ? intersectsViewport(measured.dockFooter, viewport.width, viewport.height) : true,
      detail: measured.dockFooter,
    },
    submitButtonVisible: {
      expected: actionMode,
      pass: actionMode ? intersectsViewport(measured.submitButton, viewport.width, viewport.height) : true,
      detail: measured.submitButton,
    },
    abstainOffered: {
      expected: mode === 'vote',
      pass: mode === 'vote' ? Boolean(measured.abstain) : (mode === 'pk-vote' ? !measured.abstain : true),
      detail: measured.abstain ?? null,
    },
    confirmationControlPresent: {
      expected: mode === 'vote' || mode === 'pk-vote',
      pass: mode === 'vote' || mode === 'pk-vote' ? Boolean(measured.confirm) : true,
      detail: measured.confirm ?? null,
    },
    resultDialogPresent: {
      expected: mode === 'finished',
      pass: Boolean(measured.dialog) === (mode === 'finished'),
      detail: measured.dialog,
    },
  };
  const failed = Object.entries(checks).filter(([, check]) => !check.pass).map(([name]) => name);
  return { checks, pass: failed.length === 0, failed };
}

/* -------------------------------------------------------- privacy scan ---- */

async function privacyScan(page) {
  const result = await evaluate(page.cdp, `(() => {
    const visibleText = document.body.innerText;
    const domHtml = document.documentElement.outerHTML;
    const needles = ${JSON.stringify(PRIVACY_NEEDLES)};
    const hits = [];
    for (const needle of needles) {
      if (visibleText.includes(needle)) hits.push({ needle, location: 'visible-text' });
      if (domHtml.includes(needle)) hits.push({ needle, location: 'dom-html' });
    }
    const wordCard = document.querySelector('[data-region="word-card"]');
    const wordCardText = wordCard?.innerText?.replace(/\\s+/g, ' ').trim() ?? null;
    return { visibleTextLength: visibleText.length, domHtmlLength: domHtml.length, hits, wordCardText, wordCardHasMaskedWord: wordCard ? /••••/.test(wordCardText) : null };
  })()`);
  // Attribute-level scan too: DOM text could hide a leak in aria-label/title.
  const attributeScan = await evaluate(page.cdp, `(() => {
    const needles = ${JSON.stringify(PRIVACY_NEEDLES)};
    const hits = [];
    for (const element of document.querySelectorAll('*')) {
      for (const attribute of element.attributes ?? []) {
        for (const needle of needles) {
          if (attribute.value.includes(needle)) hits.push({ needle, location: 'attribute', attribute: attribute.name, tag: element.tagName });
        }
      }
    }
    return hits;
  })()`);
  result.hits.push(...attributeScan);
  result.pass = result.hits.length === 0;
  return result;
}


export { MEASURE_EXPRESSION, measure, intersectionArea, intersectsViewport, layoutChecks, privacyScan };
