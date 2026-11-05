/** @jest-environment jsdom */
import { InlineIdentityList } from '@/components/Workspace/IdentitySelector/InlineIdentityList';
import type { Identity } from '@/services/workspace/workspaceModel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const identities: Identity[] = [
  { id: 'human_900004', kind: 'user', name: '示例用户', avatar: '' },
  { id: 'bot-helper:900004', kind: 'bot', name: '协作 Bot', avatar: '', engine: 'OpenClaw', chatStatus: 'online' },
  { id: 'bot-coder:900004', kind: 'bot', name: '代码专家', avatar: '', engine: 'ClaudeCode', reachability: 'unreachable' },
  { id: 'bot-risk:900004', kind: 'bot', name: '风险监控服务', avatar: '', engine: 'OpenClaw', reachability: 'reachable' },
];

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
});

describe('InlineIdentityList（dmore artboard-002 选择当前身份）', () => {
  const onSelect = jest.fn();

  function setup(activeId = 'bot-helper:900004') {
    return render(
      <InlineIdentityList
        userIdentities={identities.filter((identity) => identity.kind === 'user')}
        botIdentities={identities.filter((identity) => identity.kind === 'bot')}
        activeId={activeId}
        onSelect={onSelect}
      />,
    );
  }

  it('渲染搜索框与用户/Bot 分组标签（10px 弱化灰）', () => {
    setup();
    const search = screen.getByRole('textbox', { name: '搜索 Bot 名称、Bot ID 或引擎' });
    expect(search).toHaveAttribute('placeholder', '搜索 Bot 名称、Bot ID 或引擎');
    // 用户行还有「用户」身份小标签，分组标签用 p 选择器区分。
    expect(screen.getByText('用户', { selector: 'p' })).toHaveClass('text-[10px]', 'text-muted-foreground');
    expect(screen.getByText('Bot', { selector: 'p' })).toHaveClass('text-[10px]');
    // 默认展示全量身份。
    identities.forEach((identity) => {
      expect(screen.queryAllByText(identity.name)).toHaveLength(1);
      expect(screen.getByText(identity.name)).toBeInTheDocument();
    });
  });

  it('选中行有 aria-current 与引擎描边标签，在线 Bot 显示 6px 状态点', async () => {
    setup();
    const active = screen.getByRole('button', { name: /协作 Bot/ });
    expect(active).toHaveAttribute('aria-current', 'true');
    expect(active).toHaveTextContent('OpenClaw');
    // 名称超长截断，悬浮 Tooltip 展示「名称（工号）」。
    await userEvent.setup().hover(screen.getByText('协作 Bot', { selector: 'span' }));
    expect(await screen.findByRole('tooltip')).toHaveTextContent('协作 Bot（900004）');
    // 协作 Bot online(chatStatus)与风险监控服务 reachable 在线，代码专家 unreachable 不显示。
    expect(screen.getByLabelText('协作 Bot 在线')).toBeInTheDocument();
    expect(screen.getByLabelText('风险监控服务 在线')).toBeInTheDocument();
    expect(screen.queryByLabelText('代码专家 在线')).not.toBeInTheDocument();
  });

  it('按 Bot 名称、Bot ID 关键词过滤', () => {
    setup();
    const search = screen.getByRole('textbox', { name: '搜索 Bot 名称、Bot ID 或引擎' });
    fireEvent.change(search, { target: { value: '代码' } });
    expect(screen.getByText('代码专家')).toBeInTheDocument();
    expect(screen.queryByText('协作 Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('示例用户')).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: 'bot-risk' } });
    expect(screen.getByText('风险监控服务')).toBeInTheDocument();
    expect(screen.queryByText('代码专家')).not.toBeInTheDocument();
  });

  it('按引擎关键词过滤 Bot 分组', () => {
    setup();
    fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot 名称、Bot ID 或引擎' }), {
      target: { value: 'ClaudeCode' },
    });
    expect(screen.getByText('代码专家')).toBeInTheDocument();
    expect(screen.queryByText('协作 Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('示例用户')).not.toBeInTheDocument();
  });

  it('无匹配身份时展示空态提示', () => {
    setup();
    fireEvent.change(screen.getByRole('textbox', { name: '搜索 Bot 名称、Bot ID 或引擎' }), {
      target: { value: '不存在' },
    });
    expect(screen.getByText('没有匹配的身份，换个关键词试试。')).toBeInTheDocument();
  });

  it('点击未选中身份立即回调并携带 id；点击当前选中不触发', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: /风险监控服务/ }));
    expect(onSelect).toHaveBeenCalledWith('bot-risk:900004');
    fireEvent.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
