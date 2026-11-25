/** @jest-environment jsdom */
// CollapsibleUserText —— 单聊超长用户消息折叠的纯函数 + 组件测试。
// 见 OpenSpec change `add-long-user-message-collapse`(specs/long-user-message-collapse)。
//
// 复制全文不走折叠壳(2026-11-03 拍板删除行内按钮:气泡下方已有 MessageCopyAction),
// 故无剪贴板分支用例。
//
// 组件量高在 jsdom 无真实布局,scrollHeight 经原型 getter mock 注入;
// MarkdownRenderer/插件桩掉(渲染保真由 ChatMessageList 集成测试用真实 Bubble 链负责,
// 本套只测折叠壳行为),Button 桩掉以避开 @/components/ui barrel 重依赖链。
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';

// jest.mock 工厂不可引用 out-of-scope 变量 → 桩组件在工厂内内联。
jest.mock('@/components/ui', () => {
  const React = require('react');
  return {
    Button: ({ children, ...rest }: { children?: React.ReactNode }) =>
      React.createElement('button', { type: 'button', ...rest }, children),
  };
});

jest.mock('@tc-chat/ui/es/MarkdownRender', () => {
  const React = require('react');
  return {
    MarkdownRenderer: ({ content }: { content: string }) =>
      React.createElement('div', { 'data-testid': 'md-stub' }, content),
    aixUiPlugin: {},
    fileRefPlugin: {},
  };
});

jest.mock('@tc-chat/ui/es/MessageRenderer', () => ({
  markdownPresets: { full: [] },
}));

import {
  COLLAPSE_MAX_HEIGHT_PX,
  CollapsibleUserText,
  computeCollapseMetrics,
} from '@/components/Workspace/ChatPanel/CollapsibleUserText';

describe('CollapsibleUserText.computeCollapseMetrics(量高结论)', () => {
  it('高度不超过阈值时 overThreshold=false', () => {
    expect(
      computeCollapseMetrics(COLLAPSE_MAX_HEIGHT_PX, 22).overThreshold,
    ).toBe(false);
    expect(computeCollapseMetrics(100, 22).overThreshold).toBe(false);
  });

  it('超过阈值时 overThreshold=true 且行数四舍五入折算', () => {
    const metrics = computeCollapseMetrics(660, 22);
    expect(metrics.overThreshold).toBe(true);
    expect(metrics.lineCount).toBe(Math.round(660 / 22));
  });

  it('lineHeight 非法时兜底 22 折算,行数至少为 1', () => {
    expect(computeCollapseMetrics(0, 0)).toEqual({
      overThreshold: false,
      lineCount: 1,
    });
    expect(computeCollapseMetrics(44, Number.NaN).lineCount).toBe(2);
    expect(computeCollapseMetrics(-100, -1).overThreshold).toBe(false);
  });
});

/** 把整型高度注入 jsdom 的 scrollHeight getter(量高 → 折叠态)。 */
function mockScrollHeight(px: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get: () => px,
  });
}

const LONG_CONTENT =
  '参数说明\n' +
  Array.from({ length: 200 }, (_, i) => `第${i}行:配置值=${i}`).join('\n');

/** 折叠态的 maxHeight clamp 是活样式(inline style),经 DOM 直接断言。 */
function containerHasClampStyle(): boolean {
  const clampNodes = Array.from(
    document.querySelectorAll<HTMLElement>('div[style*="max-height"]'),
  );
  return clampNodes.length > 0;
}

describe('CollapsibleUserText 组件', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
    delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight;
  });

  it('不超阈值:零折叠 UI,正文经 MarkdownRenderer 照常渲染', () => {
    mockScrollHeight(120);
    const { container } = render(<CollapsibleUserText content="短消息" />);
    expect(screen.queryByText(/展开全部/)).toBeNull();
    expect(container.textContent).toContain('短消息');
  });

  it('超阈值:默认折叠,展开按钮含总行数,可展开后收起', () => {
    mockScrollHeight(660);
    render(<CollapsibleUserText content={LONG_CONTENT} />);

    // jsdom getComputedStyle 取不到 lineHeight → 兜底 22 → round(660/22)=30 行
    const expandBtn = screen.getByText(/展开全部 · 共 30 行/);
    // 折叠态正文容器带 clamp(collapse 入口的可观测行为);操作行仅展开/收起,无复制按钮
    expect(containerHasClampStyle()).toBe(true);

    act(() => {
      fireEvent.click(expandBtn);
    });
    const collapseBtn = screen.getByText('收起');
    act(() => {
      fireEvent.click(collapseBtn);
    });
    expect(screen.getByText(/展开全部 · 共 30 行/)).toBeTruthy();
  });

  it('forceExpanded(本轮回复期间)及内容完整渲染、零折叠 UI', () => {
    mockScrollHeight(660);
    render(<CollapsibleUserText content={LONG_CONTENT} forceExpanded />);
    expect(screen.queryByText(/展开全部/)).toBeNull();
    expect(screen.queryByText('收起')).toBeNull();
    expect(screen.getByText(/第199行:配置值=199/)).toBeTruthy();
  });
});
