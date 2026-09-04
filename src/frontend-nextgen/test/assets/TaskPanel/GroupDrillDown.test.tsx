/** @jest-environment jsdom */
import {
  GroupDrillDownPanel,
  GroupSessionView,
  filterGroupMessages,
  isNonMessageGroupContent,
  resolveMasterBot,
  resolveOwnedViewBot,
  shouldCollapseMessage,
  sortMessagesByTimestamp,
  type GroupMessage,
} from '@/assets/TaskPanel/GroupDrillDown';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

function makeMessage(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    id: 'message-1',
    sender: 'Bot A',
    bot_name: 'Bot A',
    content: '正常消息',
    message_type: 'bot',
    timestamp: '2026-08-22T10:00:00+08:00',
    ...overrides,
  };
}

describe('GroupDrillDown message filtering', () => {
  it('过滤 system 消息和 GroupContext 注入内容', () => {
    const systemMessage = makeMessage({ id: 'system', message_type: 'system', content: '节点开始执行' });
    const systemBotMessage = makeMessage({
      id: 'system-bot',
      message_type: 'bot',
      bot_name: 'system Bot',
      content: '系统执行回执',
    });
    const contextMessage = makeMessage({
      id: 'context',
      content: '<GroupContext>\n当前你在 bcn 群聊中，群聊相关信息如下\n## 群聊信息',
    });
    const normalMessage = makeMessage({ id: 'normal', content: '请分析当前数据架构。' });

    expect(isNonMessageGroupContent(systemMessage)).toBe(true);
    expect(isNonMessageGroupContent(systemBotMessage)).toBe(true);
    expect(isNonMessageGroupContent(contextMessage)).toBe(true);
    expect(isNonMessageGroupContent(normalMessage)).toBe(false);
    expect(filterGroupMessages([systemMessage, systemBotMessage, contextMessage, normalMessage])).toEqual([
      normalMessage,
    ]);
  });
});

describe('GroupDrillDown message ordering', () => {
  it('按消息时间递增展示群消息，接口倒序返回时也能纠正', () => {
    const newest = makeMessage({ id: 'newest', content: '较晚消息', timestamp: '2026-08-22T10:03:00+08:00' });
    const oldest = makeMessage({ id: 'oldest', content: '较早消息', timestamp: '2026-08-22T10:01:00+08:00' });
    const middle = makeMessage({ id: 'middle', content: '中间消息', timestamp: '2026-08-22T10:02:00+08:00' });

    expect(sortMessagesByTimestamp([newest, middle, oldest]).map((message) => message.id)).toEqual([
      'oldest',
      'middle',
      'newest',
    ]);
  });
});

describe('GroupDrillDown owner display', () => {
  it('使用 manager Bot 作为群 Owner，不回退到 group_id', () => {
    const owner = resolveMasterBot([
      { actor_id: 'group-id', actor_kind: 'bot', name: 'bcs_grp_xxx', role: 'worker', mode: 'auto' },
      { actor_id: 'master-id', actor_kind: 'bot', name: '业务架构视角Bot', role: 'manager', mode: 'auto' },
    ]);

    expect(owner).toMatchObject({ name: '业务架构视角Bot', role: 'manager', actor_kind: 'bot' });
  });
});

describe('GroupDrillDownPanel tabs', () => {
  it('点击标题前的关闭图标关闭对应群 Tab', async () => {
    const node = {
      id: 'node-group-1',
      name: '风险模型协同验证',
      sequence: 1,
      status: 'done' as const,
      executor: '群主 Bot',
      executorColor: '#165DFF',
      runMode: 'coop_group',
      startedAt: null,
      endAt: null,
      timeConsuming: null,
      output: null,
      outputSummary: null,
      artifacts: [],
      groupId: 'mock_group_1',
      sessionId: 'mock_session_1',
      hasSubTask: false,
      subTaskId: null,
      stepTraces: [],
      acceptanceResult: null,
    };
    const onClose = jest.fn();

    render(
      <GroupDrillDownPanel
        nodes={[node]}
        activeNodeId={node.id}
        bcsBaseUrl=""
        apiBaseUrl=""
        onSelect={jest.fn()}
        onClose={onClose}
      />,
    );
    const tab = screen.getByRole('tab');
    expect(tab).toHaveStyle('width: 190px');
    expect(tab).toHaveStyle('background: transparent');

    const closeButton = screen.getByRole('button', { name: '关闭 风险模型协同验证' });
    expect(closeButton.querySelector('svg')).toHaveAttribute('width', '15');
    fireEvent.mouseEnter(closeButton);
    expect(closeButton.querySelector('svg')).toHaveAttribute('width', '14');
    await waitFor(() => expect(screen.getAllByText('风险模型协同验证').length).toBeGreaterThanOrEqual(2));
    fireEvent.click(closeButton);

    await waitFor(() => expect(onClose).toHaveBeenCalledWith(node.id));
  });
});

describe('GroupDrillDown message typography', () => {
  it('只对超长消息启用收起态', () => {
    expect(shouldCollapseMessage('短消息')).toBe(false);
    expect(shouldCollapseMessage('一'.repeat(200))).toBe(false);
    expect(shouldCollapseMessage('一'.repeat(201))).toBe(true);
  });

  it('消息正文保持与“对话消息”字段一致的紧凑字号', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        data: {
          group_id: 'group-1',
          name: '执行会话',
          status: 'active',
          participants: [
            {
              actor_id: 'worker-bot:user-1',
              actor_kind: 'bot',
              name: '执行 Bot',
              role: 'worker',
              mode: 'auto',
            },
          ],
        },
      }),
    } as Response);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        data: {
          result: [
            {
              message_id: 'message-1',
              role: 'assistant',
              content: JSON.stringify({ data: { result: '一'.repeat(201) } }),
              gmt_create: '2026-08-22T10:00:00+08:00',
            },
          ],
        },
      }),
    } as Response);
    global.fetch = fetchMock;

    const node = {
      id: 'node-1',
      name: '执行会话',
      sequence: 1,
      status: 'done' as const,
      executor: '执行 Bot',
      executorColor: '#165DFF',
      runMode: 'bot',
      startedAt: null,
      endAt: null,
      timeConsuming: null,
      output: null,
      outputSummary: null,
      artifacts: [],
      groupId: 'group-1',
      sessionId: 'session-1',
      hasSubTask: false,
      subTaskId: null,
      stepTraces: [],
      acceptanceResult: null,
    };

    try {
      render(<GroupSessionView node={node} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />);
      const content = await screen.findByTestId('task-panel-message-content');
      expect(content).toHaveStyle('font-size: 12px');
      expect(content).toHaveStyle('--aix-markdown-font-size: 12px');
      expect(content).toHaveStyle('overflow: visible');
      expect(content).not.toHaveStyle('max-height: 120px');
      expect(screen.getByText(`${'一'.repeat(199)}…`)).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '展开消息' }));
      expect(screen.getByRole('button', { name: '收起消息' })).toBeInTheDocument();
      expect(screen.getByText('一'.repeat(201))).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '收起消息' }));
      expect(screen.getByRole('button', { name: '展开消息' })).toBeInTheDocument();
    } finally {
      global.fetch = originalFetch;
    }
  });
});

describe('GroupSessionView root session fallback', () => {
  it('run_mode=coop_group 但缺少 groupId 时仍按 sessionId 请求群消息', async () => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>();
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        data: {
          items: [
            {
              id: 'message-root',
              sender: 'root-bot',
              message_type: 'bot',
              content: '根节点协作群消息',
              timestamp: 1,
            },
          ],
        },
      }),
    } as Response);
    global.fetch = fetchMock;

    try {
      render(
        <GroupSessionView
          node={{
            id: 'root-node',
            name: '根节点',
            sequence: 1,
            status: 'running',
            executor: 'root-bot',
            assignee: 'root-bot',
            runMode: 'coop_group',
            sessionId: 'bcs_grp_root:round-1',
            hasSubTask: false,
            subTaskId: null,
            stepTraces: [],
            acceptanceResult: null,
            artifacts: [],
          }}
          bcsBaseUrl=""
          apiBaseUrl=""
          userId="user-1"
          onBack={jest.fn()}
        />,
      );

      expect(await screen.findByText('根节点协作群消息')).toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/openapi/v1/collaboration/sessions/bcs_grp_root%3Around-1/messages'),
        expect.objectContaining({ credentials: 'include' }),
      );
      // 群内无本人 owned bot → 省略 view_bot_id,用认证 Human Actor 视角拉取(后端按名册校验)
      expect(fetchMock).toHaveBeenCalledWith(
        expect.not.stringContaining('view_bot_id'),
        expect.objectContaining({ credentials: 'include' }),
      );
    } finally {
      global.fetch = originalFetch;
    }
  });
});

describe('GroupDrillDown view bot resolution', () => {
  const participants = [
    { actor_id: 'bcs_grp_abc:round-1', actor_kind: 'bot', name: '群id占位', role: 'driver', mode: 'auto' },
    { actor_id: 'default:35983', actor_kind: 'bot', name: '蔣建', role: 'worker', mode: 'auto' },
    { actor_id: '20260826_20rphqo0:146836', actor_kind: 'bot', name: '金庸', role: 'worker', mode: 'auto' },
    { actor_id: '20260826_q3tbj2da:146836', actor_kind: 'bot', name: '数据架构视角Bot', role: 'manager', mode: 'auto' },
  ];

  it('选取群成员中归属本人工号的 bot 作 view_bot_id,不回退 group_id', () => {
    // 工号 146836 本人:优先 worker(任务执行侧),次选 manager/master/driver
    expect(resolveOwnedViewBot(participants, '146836')?.actor_id).toBe('20260826_20rphqo0:146836');
  });

  it('群成员中无归属本人工号的 bot → null(不回退 group_id 或 driver)', () => {
    expect(resolveOwnedViewBot(participants, '999999')).toBeNull();
  });

  it('本人仅有 manager 身份时选 manager', () => {
    const onlyManager = participants.filter((p) => p.actor_id !== '20260826_20rphqo0:146836');
    expect(resolveOwnedViewBot(onlyManager, '146836')?.actor_id).toBe('20260826_q3tbj2da:146836');
  });
});
