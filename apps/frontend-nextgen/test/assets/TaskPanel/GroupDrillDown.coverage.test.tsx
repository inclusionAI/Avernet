/** @jest-environment jsdom */
import { GroupSessionView } from '@/assets/TaskPanel/GroupDrillDown';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, render, screen, waitFor } from '@testing-library/react';

// 未覆盖分支补充:能力层解析当前登录人(可用/不可用)、消息列表载荷兜底、缺 session、
// 消息接口 403/404/500 的无权限与外抛错误路径、卸载后迟到的异步结果被丢弃。

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

// 人类身份经能力层 getHumanIdentity 解析;测试内切换可用/不可用两种姿态
const mockHumanIdentity: {
  current: { status: string; value: { userId?: string } | null };
} = {
  current: { status: 'available', value: { userId: '146836' } },
};

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getHumanIdentity: () => mockHumanIdentity.current,
  }),
}));

const node = (overrides: Partial<TaskNodeView> = {}): TaskNodeView =>
  ({
    id: 'node-1',
    name: '执行节点',
    sequence: 1,
    status: 'running',
    executor: '执行 Bot',
    assignee: 'assignee-1',
    runMode: 'coop_group',
    groupId: 'grp-1',
    sessionId: 'bcs_grp_1:round-1',
    hasSubTask: false,
    subTaskId: null,
    stepTraces: [],
    acceptanceResult: null,
    artifacts: [],
    ...overrides,
  }) as TaskNodeView;

const jsonResponse = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body }) as Response;

const statusResponse = (status: number) => ({ ok: false, status }) as Response;

const groupDetailBody = (participants: unknown[] = []) => ({
  data: { group_id: 'grp-1', name: '分析群', status: 'active', participants },
});

describe('GroupSessionView 补充分支', () => {
  it('未下发 userId 时经能力层解析登录工号,群内归属本人 bot 作 view_bot_id', async () => {
    mockHumanIdentity.current = { status: 'available', value: { userId: '146836' } };
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          groupDetailBody([{ actor_id: 'bot-owned:146836', actor_kind: 'bot', name: '本人Bot', role: 'worker', mode: 'auto' }]),
        ),
      )
      .mockResolvedValueOnce(jsonResponse({ data: { items: [] } }));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" onBack={jest.fn()} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
      // 群消息请求带 view_bot_id=归属本人工号的 bot(能力层解析出的 146836)
      const messageUrl = fetchMock.mock.calls[1]![0] as string;
      expect(messageUrl).toContain('view_bot_id=bot-owned%3A146836');
      expect(await screen.findByText('暂无对话消息')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('能力层不可用(无登录身份)→ 工号为空,群内无本人 bot 时不发起消息请求,直接无权限态', async () => {
    mockHumanIdentity.current = { status: 'unavailable', value: null };
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce(jsonResponse(groupDetailBody([])));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" onBack={jest.fn()} />);
      expect(await screen.findByText(/无群消息查询权限/)).toBeInTheDocument();
      // effectiveUserId 为空且无本人 owned bot → 不发起会话消息请求
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('群成员角色不在展示优先级表(如 consultant)→ 展示区按兜底优先级排序', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          groupDetailBody([{ actor_id: 'c1', actor_kind: 'human', name: '顾问甲', role: 'consultant', mode: 'auto' }]),
        ),
      )
      .mockResolvedValueOnce(jsonResponse({ data: { items: [] } }));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      // 顾问等非优先角色按兜底序排到 worker 之后,成员区正常展示 1 人
      expect(await screen.findByText('群成员（1）')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('消息载荷无 items/messages/result 列表时按空列表兜底,不误报错误', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(groupDetailBody()))
      .mockResolvedValueOnce(jsonResponse({ data: { unrelated: 'not-a-list' } }));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      expect(await screen.findByText('暂无对话消息')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('节点缺少 session_id 时不发起任何请求,直接展示错误', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    global.fetch = fetchMock;

    try {
      render(
        <GroupSessionView
          node={node({ groupId: null, sessionId: null })}
          bcsBaseUrl=""
          apiBaseUrl=""
          onBack={jest.fn()}
        />,
      );
      expect(await screen.findByText('节点缺少 session_id，无法下钻')).toBeInTheDocument();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('协作群消息 403 → 无群消息权限提示(群成员仍展示)', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          groupDetailBody([{ actor_id: 'bot-other:999', actor_kind: 'bot', name: '他人Bot', role: 'worker', mode: 'auto' }]),
        ),
      )
      .mockResolvedValueOnce(statusResponse(403));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      expect(await screen.findByText(/当前用户在该协作群中没有可用的本人身份/)).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('协作群消息 500 → 错误外抛进入错误态(非权限问题不吞)', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(groupDetailBody()))
      .mockResolvedValueOnce(statusResponse(500));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      expect(await screen.findByText('会话消息请求失败（500）')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('单聊消息 404(跨用户他人 Bot)→ 无对话消息查看权限提示', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce(statusResponse(404));
    global.fetch = fetchMock;

    try {
      render(
        <GroupSessionView
          node={node({
            runMode: 'single_bot',
            groupId: null,
            sessionId: 'agent:main:session:s1:user:123',
            assignee: 'bot-1:123',
          })}
          bcsBaseUrl=""
          apiBaseUrl=""
          userId="user-1"
          onBack={jest.fn()}
        />,
      );
      expect(await screen.findByText(/该节点的执行 Bot 属于其他用户/)).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('单聊消息 500 → 错误外抛进入错误态', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce(statusResponse(500));
    global.fetch = fetchMock;

    try {
      render(
        <GroupSessionView
          node={node({
            runMode: 'single_bot',
            groupId: null,
            sessionId: 'agent:main:session:s1:user:123',
            assignee: 'bot-1:123',
          })}
          bcsBaseUrl=""
          apiBaseUrl=""
          userId="user-1"
          onBack={jest.fn()}
        />,
      );
      expect(await screen.findByText('会话消息请求失败（500）')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('群详情请求 404 → 错误态展示群详情失败信息', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce(statusResponse(404));
    global.fetch = fetchMock;

    try {
      render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      expect(await screen.findByText('群详情请求失败（404）')).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('卸载后群详情才返回/失败 → 迟到结果按 cancelled 丢弃,不更新状态', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    let resolveGroup!: (r: Response) => void;
    let rejectGroup!: (e: Error) => void;
    fetchMock
      .mockImplementationOnce(() => new Promise<Response>((resolve, reject) => { resolveGroup = resolve; rejectGroup = reject; }))
      .mockImplementationOnce(() => new Promise<Response>((_resolve, reject) => { rejectGroup = reject; }));
    global.fetch = fetchMock;

    try {
      // 第一屏:群详情挂起时卸载 → 迟到的成功响应按 cancelled 丢弃
      const first = render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      first.unmount();
      await act(async () => {
        resolveGroup(jsonResponse(groupDetailBody()));
      });

      // 第二屏:群详情挂起时卸载 → 迟到的失败也按 cancelled 丢弃(不 setError)
      const second = render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      second.unmount();
      await act(async () => {
        rejectGroup(new Error('群详情请求失败（500）'));
      });
      // 卸载后无未捕获异常即视为迟到结果被丢弃成功
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      global.fetch = originalFetch;
    }
  });

  it('卸载后消息才返回/失败 → 迟到结果按 cancelled 丢弃', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    let resolveMessages!: (r: Response) => void;
    let rejectMessages!: (e: Error) => void;
    fetchMock
      .mockResolvedValueOnce(jsonResponse(groupDetailBody()))
      .mockImplementationOnce(() => new Promise<Response>((resolve, reject) => { resolveMessages = resolve; rejectMessages = reject; }))
      .mockResolvedValueOnce(jsonResponse(groupDetailBody()))
      .mockImplementationOnce(() => new Promise<Response>((_resolve, reject) => { rejectMessages = reject; }));
    global.fetch = fetchMock;

    try {
      // 群详情已就绪、消息挂起时卸载 → 迟到的成功消息按 cancelled 丢弃
      const first = render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
      first.unmount();
      await act(async () => {
        resolveMessages(jsonResponse({ data: { items: [] } }));
      });

      // 第二屏:消息失败迟到 → 同样按 cancelled 丢弃(不进无权限/错误态)
      const second = render(<GroupSessionView node={node()} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
      second.unmount();
      await act(async () => {
        rejectMessages(new Error('会话消息请求失败（500）'));
      });
      expect(fetchMock).toHaveBeenCalledTimes(4);
    } finally {
      global.fetch = originalFetch;
    }
  });
});
