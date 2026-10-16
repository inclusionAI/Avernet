/** @jest-environment jsdom */
// 全局搜索弹窗（设计稿 artboard-016「Dialog」）行为验证：
// 三路数据装配（Bot/对话/协作群）、筛选 chips、关键词过滤（群走服务端 q）、行深链跳转与关闭。
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mockHistoryPush = jest.fn();
jest.mock('@umijs/max', () => ({
  history: { push: mockHistoryPush, replace: jest.fn() },
}));

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: 'human-1', displayName: '验收用户', avatarUrl: undefined, online: true },
    status: 'ready' as const,
  }),
}));

const mockListOwnedBotsWithMeta = jest.fn();
const mockListSessions = jest.fn();
jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: {
    listOwnedBotsWithMeta: mockListOwnedBotsWithMeta,
    listSessions: mockListSessions,
  },
}));

const mockLoadGroups = jest.fn();
jest.mock('@/services/workspace/groupService', () => ({
  groupService: { loadGroups: mockLoadGroups },
}));

const botFixture = {
  botId: 'data-bot',
  realBotId: 'data-bot',
  displayName: '数据助手',
  online: true,
  chatable: true,
  engine: 'OpenClaw',
};

const sessionFixture = {
  sessionId: 'session-1',
  botId: 'data-bot',
  title: '平台接入联调',
  messageCount: 3,
  gmtModified: '2026-10-13 10:00:00',
  gmtCreate: '2026-10-12 09:00:00',
};

const groupFixture = {
  groupId: 'group-1',
  name: '运营协作',
  kind: 'free_chat' as const,
  status: 'active' as const,
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 3,
  isPublic: false,
  deliveryPolicy: 'send_to_driver' as const,
};

function seedSources() {
  mockListOwnedBotsWithMeta.mockResolvedValue({
    ok: true as const,
    data: { bots: [botFixture], hasAgentCodingBots: false },
  });
  mockListSessions.mockResolvedValue({ ok: true as const, data: [sessionFixture] });
  mockLoadGroups.mockResolvedValue({ ok: true as const, data: [groupFixture] });
}

// 组件经延迟 require 拿到：其模块图 import @umijs/max，须等上方 const/factory 初始化完成。
const { GlobalSearchDialog } = require('@/shell/GlobalSearch') as typeof import('@/shell/GlobalSearch');

const renderDialog = () => render(<GlobalSearchDialog open onOpenChange={jest.fn()} />);

beforeEach(() => {
  jest.clearAllMocks();
  seedSources();
});

it('关闭态彻底卸载：open=false 不渲染任何内容', () => {
  const { container } = render(<GlobalSearchDialog open={false} onOpenChange={jest.fn()} />);
  expect(container).toBeEmptyDOMElement();
  expect(mockListOwnedBotsWithMeta).not.toHaveBeenCalled();
});

it('打开即渲染设计稿头部/筛选/结果：placeholder、4 段筛选、「全部」按对话→协作群→Bot 装配', async () => {
  renderDialog();
  expect(screen.getByPlaceholderText('搜索对话名称或你的Agent名称')).toBeInTheDocument();
  for (const label of ['全部', 'Bot', '对话', '协作群']) {
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
  }
  await waitFor(() => expect(screen.getByText('平台接入联调')).toBeInTheDocument());
  // 行为可聚焦按钮（点击即深链跳转），副行含「类型 · 详情」结构
  const row = screen.getByText('平台接入联调').closest('button');
  expect(row).toBeInTheDocument();
  expect(row?.textContent).toContain('对话');
  expect(row?.textContent).toContain('数据助手');
  expect(screen.getByText('运营协作')).toBeInTheDocument();
  expect(screen.getByText('我的Bot')).toBeInTheDocument();
});

it('筛选切到「Bot」：仅保留 Bot 行，且不再请求服务端', async () => {
  renderDialog();
  await waitFor(() => expect(screen.getByText('平台接入联调')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Bot' }));
  expect(screen.queryByText('平台接入联调')).not.toBeInTheDocument();
  expect(screen.queryByText('运营协作')).not.toBeInTheDocument();
  expect(screen.getByText('数据助手')).toBeInTheDocument();
  // 筛选纯客户端切换：群接口调用次数不随筛选增长
  await waitFor(() => expect(mockLoadGroups).toHaveBeenCalledTimes(1));
});

it('关键词过滤：会话行客户端命中；群关键词经防抖走服务端 q', async () => {
  renderDialog();
  await waitFor(() => expect(screen.getByText('运营协作')).toBeInTheDocument());
  fireEvent.change(screen.getByPlaceholderText('搜索对话名称或你的Agent名称'), {
    target: { value: '运营' },
  });
  // 会话不匹配关键词即消失
  await waitFor(() => expect(screen.queryByText('平台接入联调')).not.toBeInTheDocument());
  // 群接口在防抖后携带 q 重新搜索
  await waitFor(
    () => expect(mockLoadGroups).toHaveBeenLastCalledWith(expect.anything(), { q: '运营' }),
    { timeout: 2000 },
  );
  expect(screen.getByText('运营协作')).toBeInTheDocument();
});

it('点击行经深链跳转：会话行带 bot+session 落对话页，关闭回调触发', async () => {
  const onOpenChange = jest.fn();
  render(<GlobalSearchDialog open onOpenChange={onOpenChange} />);
  await waitFor(() => expect(screen.getByText('平台接入联调')).toBeInTheDocument());
  fireEvent.click(screen.getByText('平台接入联调'));
  expect(mockHistoryPush).toHaveBeenCalledWith('/workspace/chat?bot=data-bot&session=session-1');
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

it('点击协作群行：落协作页 group 深链', async () => {
  renderDialog();
  await waitFor(() => expect(screen.getByText('运营协作')).toBeInTheDocument());
  fireEvent.click(screen.getByText('运营协作'));
  expect(mockHistoryPush).toHaveBeenCalledWith('/workspace/collaboration?group=group-1');
});

it('清空数据源：全部为空时展示未找到空态', async () => {
  mockListOwnedBotsWithMeta.mockResolvedValue({ ok: true as const, data: { bots: [], hasAgentCodingBots: false } });
  mockListSessions.mockReset();
  mockLoadGroups.mockResolvedValue({ ok: true as const, data: [] });
  renderDialog();
  await waitFor(() => expect(screen.getByText('未找到相关内容')).toBeInTheDocument());
  expect(screen.getByText('换个关键词，或切换上方筛选范围试试')).toBeInTheDocument();
});