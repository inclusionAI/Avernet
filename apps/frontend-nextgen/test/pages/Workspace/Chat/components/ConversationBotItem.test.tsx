/** @jest-environment jsdom */
// Bot 行折叠契约:展开箭头是鼠标可点的折叠入口(转发同一 toggle),
// 主按钮仍是唯一的键盘/读屏控件(箭头不进 Tab 序)。
import { ConversationBotItem } from '@/pages/Workspace/Chat/components/ConversationBotItem';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const bot: ChatBotView = {
  botId: 'bot-1',
  realBotId: 'bot-1',
  displayName: '测试 Bot',
  online: true,
  chatable: true,
};

const noop = () => undefined;

function renderBotItem(overrides: { expanded?: boolean; onToggle?: () => void } = {}) {
  const onToggle = overrides.onToggle ?? jest.fn();
  const utils = render(
    <ConversationBotItem
      bot={bot}
      section="managed"
      expanded={overrides.expanded ?? false}
      selectedSessionId={null}
      origin="mine"
      scope="all"
      sessionKey=""
      sessions={undefined}
      onToggle={onToggle}
      onOriginChange={noop}
      onScopeChange={noop}
      onSearchSession={noop}
      onCreateSession={noop}
      onSelectSession={noop}
      onLoadMore={noop}
      onToggleFavorite={noop}
      isFavoritePending={() => false}
      onOpenAgentCodingBot={noop}
    />,
  );
  return { ...utils, onToggle };
}

describe('ConversationBotItem 操作区', () => {
  it('遮盖底撤除:操作区不再叠 sidebar-actions-cover 底色,筛选/加号/行尾箭头保持等宽 2px 间隙', () => {
    const { container } = renderBotItem();
    const cover = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('inset-y-0'),
    ) as HTMLElement;
    expect(cover).toBeTruthy();
    // 不再叠与行底不一致的操作区底色。
    expect(cover.className).not.toContain('sidebar-actions-cover-hover');
    expect(cover.className).not.toContain('sidebar-actions-cover-selected');
    // 间隙节奏统一:按钮间 gap-0.5(2px),行尾为箭头按钮(w-6 + px-2)让出同宽的 2px。
    expect(cover.className).toContain('gap-0.5');
    expect(cover.className).toContain('right-[34px]');
  });

  it('展开或筛选激活时文字让位:行内标题获得 pr-24 预留,图标不被文字压底', () => {
    const { container } = renderBotItem({ expanded: true });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(title?.parentElement).toHaveClass('pr-24');
  });
});

describe('ConversationBotItem 展开箭头', () => {
  it('箭头指向同一 toggle:点击箭头触发 onToggle', () => {
    const { onToggle } = renderBotItem();
    const arrow = document.querySelector('button[aria-hidden="true"]') as HTMLElement;
    expect(arrow).toHaveAttribute('tabIndex', '-1');
    fireEvent.click(arrow);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('箭头不进 Tab 序(键盘折叠由主按钮承载),主按钮点击同样触发 onToggle', () => {
    const { onToggle } = renderBotItem({ expanded: true });
    const tabbables = Array.from(document.querySelectorAll('button')).filter(
      (el) => el.getAttribute('tabIndex') !== '-1',
    );
    // 主按钮唯一可聚焦;箭头按钮 tabIndex=-1 不算。
    expect(tabbables.map((el) => el.getAttribute('aria-label'))).toContain('测试 Bot');
    fireEvent(
      tabbables.find((el) => el.getAttribute('aria-label') === '测试 Bot') as HTMLElement,
      new MouseEvent('click', { bubbles: true }),
    );
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('不可chatable的 Bot 不渲染箭头', () => {
    const { container } = render(
      <ConversationBotItem
        bot={{ ...bot, chatable: false }}
        section="managed"
        expanded={false}
        selectedSessionId={null}
        origin="mine"
        scope="all"
        sessionKey=""
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={noop}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={noop}
      />,
    );
    // 整行只剩主按钮,无展开箭头按钮。
    expect(container.querySelectorAll('button').length).toBe(1);
  });
});

describe('ConversationBotItem 外跳 Bot', () => {
  it.each([['managed'], ['team'], ['friend']] as const)('%s 分组 Coding Bot 可点且不出现普通会话操作区', (section) => {
    const onOpenAgentCodingBot = jest.fn();
    const { container, getByRole } = render(
      <ConversationBotItem
        bot={{
          ...bot,
          isAgentCodingBot: true,
          templateName: 'Coding Bot',
          chatable: false,
        }}
        section={section}
        expanded={false}
        selectedSessionId={null}
        origin="mine"
        scope="all"
        sessionKey=""
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={noop}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={onOpenAgentCodingBot}
      />,
    );

    const trigger = getByRole('button', { name: '测试 Bot' });
    expect(trigger).not.toHaveClass('opacity-50', 'cursor-not-allowed');
    fireEvent.click(trigger);
    expect(onOpenAgentCodingBot).toHaveBeenCalled();
    expect(container.querySelector('button[aria-hidden="true"]')).toBeNull();
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });
});

describe('ConversationBotItem 会话ID检索入口', () => {
  const renderRow = (over: {
    scope?: 'all' | 'favorite';
    origin?: 'mine' | 'others';
    sessionKey?: string;
    expanded?: boolean;
    chatable?: boolean;
    isAgentCodingBot?: boolean;
    onSearchSession?: (sessionKey: string) => void;
  } = {}) => {
    const onSearchSession = over.onSearchSession ?? jest.fn();
    const utils = render(
      <ConversationBotItem
        bot={{
          ...bot,
          chatable: over.chatable ?? true,
          isAgentCodingBot: over.isAgentCodingBot,
        }}
        section="managed"
        expanded={over.expanded ?? false}
        selectedSessionId={null}
        origin={over.origin ?? 'mine'}
        scope={over.scope ?? 'all'}
        sessionKey={over.sessionKey ?? ''}
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={onSearchSession}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={noop}
      />,
    );
    return { ...utils, onSearchSession };
  };

  it('有效范围 all(含他人发起视角)始终提供搜索入口', () => {
    renderRow({ origin: 'mine', scope: 'all' });
    expect(screen.getByRole('button', { name: '会话ID检索' })).toBeInTheDocument();
  });

  it('仅 all 有效范围展示搜索切换到 favorite 后隐藏', () => {
    renderRow({ scope: 'favorite' });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });

  it('origin=others 沿用 all 有效范围,仍保留搜索入口', () => {
    renderRow({ origin: 'others' });
    expect(screen.getByRole('button', { name: '会话ID检索' })).toBeInTheDocument();
  });

  it('AgentCoding 与不可聊 Bot 不渲染搜索入口', () => {
    const first = renderRow({ isAgentCodingBot: true });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
    first.unmount();
    renderRow({ chatable: false });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });

  it('生效中的检索在收起时仍可见:操作区常驻并给行名称让位', () => {
    const { container } = renderRow({ sessionKey: 'agent:main:live', expanded: false });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    // 生效中的检索钉住操作区,行名称让位 pr-24。
    expect(title?.parentElement).toHaveClass('pr-24');
    const actions = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('right-[34px]'),
    ) as HTMLElement;
    expect(actions.className).toContain('opacity-100');
    // 🔍 按钮激活态主色,标记生效条件。
    expect(screen.getByRole('button', { name: '会话ID检索' })).toHaveClass('text-primary');
  });

  it('提交检索透传 onSearchSession(已 trim),清空提交传播空串', () => {
    const { onSearchSession } = renderRow({ expanded: true });
    fireEvent.click(screen.getByRole('button', { name: '会话ID检索' }));
    const input = screen.getByRole('textbox', { name: '会话ID检索' }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: ' agent:main:k1 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearchSession).toHaveBeenCalledWith('agent:main:k1');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearchSession).toHaveBeenCalledWith('');
  });
});
