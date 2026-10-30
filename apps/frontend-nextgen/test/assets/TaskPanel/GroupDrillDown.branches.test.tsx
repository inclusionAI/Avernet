/** @jest-environment jsdom */
import {
  GroupSessionView,
  isNonMessageGroupContent,
  resolveOwnedViewBot,
  type GroupMessage,
} from '@/assets/TaskPanel/GroupDrillDown';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest, beforeEach, afterEach } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

const mockGetHumanIdentity = jest.fn();
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({ getHumanIdentity: mockGetHumanIdentity }),
}));

// 测试 mock:所有 mock 均以字符串 URL 调用,init 可选;放宽到 (url: string, ...) 以匹配 mock 形态。
type FetchImpl = (url: string, init?: RequestInit) => Promise<Response>;

const okJson = (data: unknown): Response =>
  ({ ok: true, status: 200, json: async () => data } as Response);
const badResp = (status: number): Response =>
  ({ ok: false, status, json: async () => ({}) } as Response);

function makeNode(overrides: Partial<TaskNodeView>): TaskNodeView {
  return {
    id: 'node-1',
    name: '执行会话',
    sequence: 1,
    status: 'running',
    executor: '执行 Bot',
    runMode: 'coop_group',
    groupId: 'group-1',
    sessionId: 'bcs_grp_1:round-1',
    hasSubTask: false,
    subTaskId: null,
    stepTraces: [],
    acceptanceResult: null,
    artifacts: [],
    ...overrides,
  } as TaskNodeView;
}

const groupNode = makeNode({});
const singleNode = makeNode({
  runMode: 'single_bot',
  groupId: null,
  sessionId: 'agent:main:session:sess-1:user:123',
  assignee: 'doudi:user:123',
});

let originalFetch: typeof global.fetch;

beforeEach(() => {
  originalFetch = global.fetch;
  mockGetHumanIdentity.mockReset();
});

afterEach(() => {
  global.fetch = originalFetch;
});

function setFetch(impl: FetchImpl) {
  global.fetch = jest.fn(impl) as unknown as typeof global.fetch;
}

describe('GroupDrillDown resolveCurrentUserId fallthrough', () => {
  it('userId=me 时回退 getHumanIdentity 取工号并命中本人 owned bot', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: '建', avatarUrl: '', online: true },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
                { actor_id: 'consultant:999', actor_kind: 'bot', name: '顾问', role: 'consultant', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(
        okJson({
          data: {
            items: [
              { id: 'm1', sender: '执行 Bot', message_type: 'bot', content: '可读消息', timestamp: 1 },
            ],
          },
        }),
      );
    });

    render(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('可读消息')).toBeInTheDocument();
  });

  it('getHumanIdentity available 但 value 无 userId → ?? 兜底空串(L29 idx1),群内无 owned bot 置无权限', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });

    render(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId={undefined}
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText(/当前用户在该协作群中没有可用的本人身份/)).toBeInTheDocument();
  });

  it('getHumanIdentity unsupported → 返回空串,群内无 owned bot 置无权限', async () => {
    mockGetHumanIdentity.mockReturnValue({ status: 'unsupported', value: null, reason: '未登录' });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });

    render(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText(/当前用户在该协作群中没有可用的本人身份/)).toBeInTheDocument();
  });
});

describe('GroupDrillDown extractMessageList 兜底', () => {
  it('消息信封为无列表键的 record → 返回空数组(L76)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      // 会话消息接口返回只含 unrelated 的 record → unwrap 到 { unrelated } → 无 items/messages/result
      return Promise.resolve(okJson({ data: { unrelated: 'x' } }));
    });

    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('暂无对话消息')).toBeInTheDocument();
  });

  it('消息信封 payload 是字符串(L70 非 record 分支) → 返回空数组', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson('raw-string-payload'));
    });

    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('暂无对话消息')).toBeInTheDocument();
  });
});

describe('GroupDrillDown 缺 sessionId 早返回', () => {
  it('节点缺少 session_id → 渲染错误态,不渲染成员分区(L506-510)', async () => {
    setFetch(() => Promise.resolve(okJson({})));

    render(
      <GroupSessionView
        node={makeNode({ sessionId: null, runMode: 'single_bot', groupId: null })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="user-1"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('节点缺少 session_id，无法下钻')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /群成员/ })).not.toBeInTheDocument();
  });
});

describe('GroupDrillDown 群消息 fetch 异常处理', () => {
  function groupFetchWithMessages(msgStatus: number) {
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(badResp(msgStatus));
    });
  }

  it('群消息 403 → 置无权限态(群)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    groupFetchWithMessages(403);
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText(/当前用户在该协作群中没有可用的本人身份/)).toBeInTheDocument();
  });

  it('群消息 500 → 外抛 → setError', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    groupFetchWithMessages(500);
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('会话消息请求失败（500）')).toBeInTheDocument();
  });

  it('群消息 reject 非 Error 串 → L584 兜底 + 外抛"加载执行会话信息失败"', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.reject('boom-string');
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('加载执行会话信息失败')).toBeInTheDocument();
  });
});

describe('GroupDrillDown 单聊消息 fetch 异常处理', () => {
  function singleFetchWithMessages(msgStatus: number) {
    setFetch(() => Promise.resolve(badResp(msgStatus)));
  }

  it('单聊消息 403 → 置无权限态(单聊)', async () => {
    singleFetchWithMessages(403);
    render(
      <GroupSessionView
        node={singleNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="123"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText(/该节点的执行 Bot 属于其他用户/)).toBeInTheDocument();
  });

  it('单聊消息 500 → 外抛 → setError', async () => {
    singleFetchWithMessages(500);
    render(
      <GroupSessionView
        node={singleNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="123"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('会话消息请求失败（500）')).toBeInTheDocument();
  });
});

describe('GroupDrillDown catch 中 cancelled 退出', () => {
  it('消息 fetch reject 时组件已卸载 → L583 cancelled return(L583 cancelled)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    let rejectMessages!: (err: Error) => void;
    const messagesInFlight = new Promise<Response>((_, rej) => {
      rejectMessages = rej;
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return messagesInFlight;
    });

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
      );
    });
    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      rejectMessages(new Error('会话消息请求失败（403）'));
      await messagesInFlight.catch(() => undefined);
    });
  });

  it('群详情 fetch reject 时组件已卸载 → L608 cancelled return(L608 cancelled 分支)', async () => {
    let rejectGroup!: (err: Error) => void;
    const groupInFlight = new Promise<Response>((_, rej) => {
      rejectGroup = rej;
    });
    setFetch(() => groupInFlight);

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />,
      );
    });
    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      rejectGroup(new Error('群详情请求失败（500）'));
      await groupInFlight.catch(() => undefined);
    });
  });

  it('群详情 resolve 时组件已卸载 → L560 cancelled return(L560 cancelled)', async () => {
    let resolveGroup!: (resp: Response) => void;
    const groupInFlight = new Promise<Response>((res) => {
      resolveGroup = res;
    });
    setFetch(() => groupInFlight);
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />,
      );
    });
    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      resolveGroup(okJson({ data: { group_id: 'group-1', name: '执行会话', status: 'active', participants: [] } }));
      await groupInFlight;
    });
  });

  it('消息 resolve 时组件已卸载 → L602 cancelled return(L602 cancelled)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    let resolveMessages!: (resp: Response) => void;
    const messagesInFlight = new Promise<Response>((res) => {
      resolveMessages = res;
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return messagesInFlight;
    });
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
      );
    });
    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      resolveMessages(okJson({ data: { items: [{ id: 'm', sender: '执行 Bot', message_type: 'bot', content: '后到', timestamp: 1 }] } }));
      await messagesInFlight;
    });
  });
});

describe('GroupDrillDown 后台刷新吞错误', () => {
  it('背景刷新时整页错误被吞掉,保留现有快照(L608 backgroundRefresh return)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    const groupResp = okJson({
      data: {
        group_id: 'group-1',
        name: '执行会话',
        status: 'active',
        participants: [
          { actor_id: 'worker-bot:emp-1', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
        ],
      },
    });
    const msgResp = okJson({
      data: {
        items: [{ id: 'old', sender: '执行 Bot', message_type: 'bot', content: '已有消息', timestamp: 1 }],
      },
    });
    const initialFetch: FetchImpl = (url: string) => {
      if (url.includes('/collaboration/groups/')) return Promise.resolve(groupResp);
      return Promise.resolve(msgResp);
    };
    let fetchImpl: FetchImpl = initialFetch;
    setFetch((...args) => fetchImpl(...args));

    const view = render(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
        refreshVersion={0}
      />,
    );
    expect(await screen.findByText('已有消息')).toBeInTheDocument();

    // 刷新:仅 refreshVersion 变化 → sameScope+hasSnapshot → backgroundRefresh=true;群详情 reject 走外层 catch 被吞
    fetchImpl = async (url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.reject(new Error('群详情请求失败（500）'));
      }
      return Promise.reject(new Error('会话消息请求失败（500）'));
    };
    view.rerender(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
        refreshVersion={1}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('已有消息')).toBeInTheDocument();
      expect(screen.queryByText('正在加载执行信息…')).not.toBeInTheDocument();
      expect(screen.queryByText('群详情请求失败（500）')).not.toBeInTheDocument();
    });
  });
});

describe('GroupDrillDown 群详情失败', () => {
  it('群详情 !ok → 抛错并 setError(L45)', async () => {
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(badResp(404));
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="user-1" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('群详情请求失败（404）')).toBeInTheDocument();
  });

  it('bcsBaseUrl 非空 → raw 直接取 json(L41/L46)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/groups/group-1')) {
        // bcsBaseUrl 非空分支:返回 raw 对象(不剥 .data)
        return Promise.resolve(
          okJson({
            group_id: 'group-1',
            name: '执行会话',
            status: 'active',
            participants: [
              { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
            ],
          }),
        );
      }
      return Promise.resolve(
        okJson({ data: { items: [{ id: 'm', sender: '执行 Bot', message_type: 'bot', content: '群详情直取', timestamp: 1 }] } }),
      );
    });
    render(
      <GroupSessionView
        node={groupNode}
        bcsBaseUrl="http://bcs.example"
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('群详情直取')).toBeInTheDocument();
  });
});

describe('GroupDrillDown mock id / raw null / 字段兜底', () => {
  it('mock groupId → fetchGroupDetail 返回 null(L40),mock sessionId → fetchSessionMessages 返回 [](L109)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    const calls: string[] = [];
    setFetch((url: string) => {
      calls.push(url);
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ groupId: 'mock_group-1', sessionId: 'mock_sess-1', name: '' })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    // 群详情端点(bcsBaseUrl 空)和会话消息端点都不应被命中(isMockId 提前返回)
    await waitFor(() => expect(screen.getByText('暂无对话消息')).toBeInTheDocument());
    expect(calls.some((u) => u.includes('/collaboration/groups/mock_group-1'))).toBe(false);
    expect(calls.some((u) => u.includes('/collaboration/sessions/mock_sess-1/messages'))).toBe(false);
  });

  it('群详情 raw 为 null → fetchGroupDetail 返回 null(L47)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: 'emp-1', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(okJson({ data: null }));
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('暂无对话消息')).toBeInTheDocument();
  });

  it('raw 用 id 兜底 group_id,用 label 兜底 name,无 status → unknown(L49/L50/L51)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              id: 'gid-from-id',
              label: 'Label名',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ name: '' })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    // groupName = node.name('') || group.name('Label名') → 'Label名'
    expect(await screen.findByText('Label名')).toBeInTheDocument();
  });

  it('raw 无 group_id/id → group_id 兜底 groupId;name 兜底 group_name(L49/L50)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_name: 'GroupName兜底',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ name: '' })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('GroupName兜底')).toBeInTheDocument();
  });

  it('raw 无 name/label/group_name → name=空串(L50 空兜底)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ name: '' })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    // node.name '' || group.name '' || (isGroup ? '协作群执行详情'…) → '协作群执行详情'(L633 cond-expr idx0)
    expect(await screen.findByText('协作群执行详情')).toBeInTheDocument();
  });

  it('raw 无 participants → participants=[](L52)+展开成员显示"暂无成员数据"(L773)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(okJson({ data: { group_id: 'group-1', name: '执行会话' } }));
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    const memberBtn = await screen.findByRole('button', { name: /群成员/ });
    expect(memberBtn).toHaveTextContent('群成员（0）');
    await act(async () => {
      fireEvent.click(memberBtn);
    });
    expect(await screen.findByText('暂无成员数据')).toBeInTheDocument();
  });
});

describe('GroupDrillDown 群参与者字段兜底映射', () => {
  it('参与者各字段缺失走 ?? 兜底链(L53-57)且展开渲染非 bot 成员(L791/817)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                // 本人 owned worker(确保触发消息拉取)
                { actor_id: 'worker:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
                // 仅 bot_uuid 兜底 actor_id;name 走 bot_name
                { bot_uuid: 'bu-only', bot_name: '按bot_name', actor_kind: 'bot', role: 'worker', mode: 'auto' },
                // 仅 bot_id 兜底;name 空兜底 ''
                { bot_id: 'bid-only', actor_kind: 'bot', role: 'worker', mode: 'auto' },
                // actor_id 兜底 name(无 name/bot_name)
                { actor_id: 'aid-only', actor_kind: 'bot', role: 'worker', mode: 'auto' },
                // bot_uuid 兜底 name(无 name/bot_name/actor_id)
                { bot_uuid: 'bu-name', actor_kind: 'bot', role: 'worker', mode: 'auto' },
                // 全空 → actor_id '',name ''(L53 idx3/L55 idx4)
                { actor_kind: 'bot', role: 'worker', mode: 'auto' },
                // 无 actor_kind → 'bot'(L54 idx1)
                { actor_id: 'no-kind:123', role: 'worker', mode: 'auto' },
                // 无 role → 'worker'(L56 idx1)
                { actor_id: 'no-role:123', actor_kind: 'bot', mode: 'auto' },
                // 无 mode → 'auto'(L57 idx1)
                { actor_id: 'no-mode:123', actor_kind: 'bot', role: 'worker' },
                // human 成员(actor_kind!='bot')触发 L791/792/817 else 分支
                { actor_id: 'human:123', actor_kind: 'human', name: '人类成员', role: 'consultant', mode: 'auto' },
                // role 不在 ROLE_LABELS → 兜底 participant.role(L816 idx1)
                { actor_id: 'custom:123', actor_kind: 'bot', name: '自定义角色', role: 'custom_role', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });

    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    const memberBtn = await screen.findByRole('button', { name: /群成员/ });
    await act(async () => {
      fireEvent.click(memberBtn);
    });
    expect(await screen.findByText('人类成员')).toBeInTheDocument();
    expect(screen.getByText('自定义角色')).toBeInTheDocument();
    expect(screen.getByText(/custom_role/)).toBeInTheDocument();
  });

  it('群仅 driver owned bot → resolveOwnedViewBot 选 driver(L207 idx1)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'driver-bot:123', actor_kind: 'bot', name: '驱动 Bot', role: 'driver', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(
        okJson({ data: { items: [{ id: 'm', sender: '驱动 Bot', message_type: 'bot', content: 'driver消息', timestamp: 1 }] } }),
      );
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('driver消息')).toBeInTheDocument();
  });

  it('群仅 consultant owned bot → resolveOwnedViewBot 走 owned[0](L207 idx2)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'consultant-bot:123', actor_kind: 'bot', name: '顾问 Bot', role: 'consultant', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(
        okJson({ data: { items: [{ id: 'm', sender: '顾问 Bot', message_type: 'bot', content: 'consultant消息', timestamp: 1 }] } }),
      );
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('consultant消息')).toBeInTheDocument();
  });

  it('群无 manager/driver 且 node.executor 空 → ownerName 兜底 "未识别"(L640 idx2)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ executor: null })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText(/Owner: 未识别/)).toBeInTheDocument();
  });

  it('群无 manager/driver bot → ownerName 兜底 node.executor(L640 idx1)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(okJson({ data: { items: [] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({ executor: 'owner-exec' })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText(/Owner: owner-exec/)).toBeInTheDocument();
  });
});

describe('GroupDrillDown 消息归一化/ChatBubble 多形态渲染', () => {
  it('一次性覆盖 normalizeMessage 与 ChatBubble 各分支(L327/329/335/343/352/359/372/383/384/399/401/412/429/81/84/85/86/90)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { userId: '123', displayName: 'x' },
    });
    setFetch((url: string) => {
      if (url.includes('/collaboration/groups/')) {
        return Promise.resolve(
          okJson({
            data: {
              group_id: 'group-1',
              name: '执行会话',
              status: 'active',
              participants: [
                { actor_id: 'worker-bot:123', actor_kind: 'bot', name: '执行 Bot', role: 'worker', mode: 'auto' },
              ],
            },
          }),
        );
      }
      return Promise.resolve(
        okJson({
          data: {
            items: [
              // M1: sender present + message_type present + number timestamp + id → 含正常可读 Bot 消息
              { id: 'm1', sender: 'botA', message_type: 'bot', content: 'botA 消息', timestamp: 1700000000000 },
              // M2: role user,无 message_type → messageType 'human'(L81 true);无 sender → 'user'(L85 role ternary true);
              //     无 id 有 message_id → L84 idx1;gmt_create 字符串 → L84 inner idx0 + L90 string→new Date
              { message_id: 'm2', role: 'user', content: '人类消息', gmt_create: '2026-01-01T00:00:00Z' },
              // M3: 无 id/message_id/gmt_create → Math.random(L84 inner idx1) + Date.now(L90 falsy 分支);
              //     无 sender/bot_name → '未知成员'(L327 idx2)
              { role: 'assistant', content: '无id消息' },
              // M5: 有 bot_name → L327 idx0
              { id: 'm5', bot_name: 'BotName', message_type: 'bot', content: '有 bot_name', timestamp: 1 },
              // M6: 无 content → normalizeMessage content '' (L86 idx1);空内容被 filter 过滤
              { id: 'noc', role: 'assistant', sender: 'b' },
              // Mnan1/Mnan2: NaN 时间戳 → aSortable/bSortable = MAX (L266/267 idx0);两条均 NaN 相等 → L268 tiebreak;
              //   formatMessageTime NaN → return String(timestamp||'') (L281);data-ts undefined (L343 idx0)
              { id: 'nan1', content: '无效时间1', sender: 'b1', message_type: 'bot', timestamp: 'bad-date' },
              { id: 'nan2', content: '无效时间2', sender: 'b2', message_type: 'bot', timestamp: 'also-bad' },
              // Mlonghuman: 人类长消息(201字符)>200 → collapsible;折叠态渲染展开按钮渐变(isHuman → surfaceRaised,L429 idx0)
              { id: 'longhuman', role: 'user', content: '人'.repeat(201) },
              // Mjsonempty: 内容为 JSON 空字符串 '""' → renderableSource 返回 null → 兜底 msg.content(L338 idx1)
              { id: 'jsonempty', sender: 'b', message_type: 'bot', content: '""', timestamp: 1 },
              // 过滤路径触发 isNonMessageGroupContent 各分支(消息被过滤,不渲染但函数已执行):
              { id: 't1', role: 'tool_result', content: 'x', message_type: 'bot', sender: 'b' },
              { id: 't2', role: 'tool_call', content: 'x', message_type: 'bot', sender: 'b' },
              { id: 't3', role: 'tool', content: 'x', message_type: 'bot', sender: 'b' },
              { id: 't4', content: '   ', message_type: 'bot', sender: 'b' },
              { id: 't5', bot_name: 'system', message_type: 'bot', content: 'y' },
              { id: 't6', message_type: 'system_x', content: 'y', sender: 'b' },
              { id: 't7', message_type: 'bot', sender: 'b', content: '## 群聊信息汇总' },
            ],
          },
        }),
      );
    });
    render(
      <GroupSessionView node={groupNode} bcsBaseUrl="" apiBaseUrl="" userId="me" onBack={jest.fn()} />,
    );
    expect(await screen.findByText('人类消息')).toBeInTheDocument();
    expect(screen.getByText('botA 消息')).toBeInTheDocument();
    expect(screen.getByText('有 bot_name')).toBeInTheDocument();
  });
});

describe('GroupDrillDown 单聊 fetchSessionMessages 路径分支', () => {
  it('assignee 含 ":" → slice realBotId/botOwner(L121/L123 true);session 无 :user: → sessionUser 空(L122 true)', async () => {
    setFetch(() => Promise.resolve(okJson({ data: { items: [{ id: 'm', sender: 'b', message_type: 'bot', content: '委派消息', timestamp: 1 }] } })));
    render(
      <GroupSessionView
        node={makeNode({
          runMode: 'single_bot',
          groupId: null,
          sessionId: 'sess-no-user-segment',
          assignee: 'botReal:user1',
          executor: 'botReal',
        })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="user1"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('委派消息')).toBeInTheDocument();
  });

  it('assignee 无 ":" 且 session 无 :user: → scopeUser 走 userId(L124 idx2)', async () => {
    setFetch(() => Promise.resolve(okJson({ data: { items: [{ id: 'm', sender: 'b', message_type: 'bot', content: 'userId兜底', timestamp: 1 }] } })));
    render(
      <GroupSessionView
        node={makeNode({
          runMode: 'single_bot',
          groupId: null,
          sessionId: 'sess-no-user-segment',
          assignee: 'plainBot',
          executor: 'plainBot',
        })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="u2"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('userId兜底')).toBeInTheDocument();
  });

  it('单聊无 :user: 且 assignee 无 ":" 且 userId 为空 → scopeUser="" → 不带 user_id(L124 idx3/L126 false)', async () => {
    mockGetHumanIdentity.mockReturnValue({
      status: 'available',
      value: { displayName: 'x' }, // 无 userId → resolveCurrentUserId 返回 ''
    });
    let calledUrl = '';
    setFetch((url: string) => {
      calledUrl = url;
      return Promise.resolve(okJson({ data: { items: [{ id: 'm', sender: 'b', message_type: 'bot', content: '空 scopeUser', timestamp: 1 }] } }));
    });
    render(
      <GroupSessionView
        node={makeNode({
          runMode: 'single_bot',
          groupId: null,
          sessionId: 'sess-no-user-segment',
          assignee: 'plainBot',
          executor: 'plainBot',
        })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="me"
        onBack={jest.fn()}
      />,
    );
    expect(await screen.findByText('空 scopeUser')).toBeInTheDocument();
    // scopeUser 空 → 不附加 user_id/owner_id
    expect(calledUrl).not.toContain('user_id=');
  });

  it('单聊无 assignee 且无 executor → !botId return [](L119),group null → "执行会话详情"(L633 idx2/L640 idx1/L698 idx1)', async () => {
    setFetch(() => Promise.resolve(okJson({ data: { items: [{ id: 'm', sender: 'b', message_type: 'bot', content: 'should-not-render', timestamp: 1 }] } })));
    render(
      <GroupSessionView
        node={makeNode({
          name: '',
          runMode: 'single_bot',
          groupId: null,
          sessionId: 'sess-no-user-segment',
          assignee: null,
          executor: null,
          assigneeName: null,
        })}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId="user1"
        onBack={jest.fn()}
      />,
    );
    // node.name '' || group?.name (null) || (isGroup false → '执行会话详情')
    expect(await screen.findByText('执行会话详情')).toBeInTheDocument();
    expect(screen.getByText(/Owner: 未识别/)).toBeInTheDocument();
    expect(screen.queryByText('should-not-render')).not.toBeInTheDocument();
  });
});

describe('GroupDrillDown isNonMessageGroupContent 纯函数补分支', () => {
  it('tool_result/tool_call/tool 角色 → true', () => {
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: 'x', sender: 'b', role: 'tool_result' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: 'x', sender: 'b', role: 'tool_call' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: 'x', sender: 'b', role: 'tool' }),
    ).toBe(true);
  });

  it('空内容 → true(L232);content 包含 GroupContext 系列关键字 → true(L243)', () => {
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: '   ', sender: 'b', role: 'assistant' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: '当前你在 bcn 群聊中干活', sender: 'b', role: 'assistant' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: '## 参与者列表', sender: 'b', role: 'assistant' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: '<GroupContext>注入</GroupContext>正常结束', sender: 'b', role: 'assistant' }),
    ).toBe(true);
  });

  it('system_message_type / system sender 正常分支', () => {
    expect(
      isNonMessageGroupContent({ message_type: 'system-event', content: '有内容', sender: 'b', role: 'assistant' }),
    ).toBe(true);
    expect(
      isNonMessageGroupContent({ message_type: 'bot', content: '有内容', sender: 'b', bot_name: 'System Bot', role: 'assistant' }),
    ).toBe(true);
    // 普通消息为 false
    expect(
      isNonMessageGroupContent({ message_type: 'assistant', content: '正常内容', sender: 'b', role: 'assistant' }),
    ).toBe(false);
  });

  it('消息缺 message_type / bot_name / sender → 走 ?? ""  兜底(L233 idx1/236 idx2)', () => {
    // 无 message_type → messageType ?? '' 兜底 ''(L233 idx1);无 bot_name/sender → sender ?? '' 兜底(L236 idx2);
    //   内容非空且非系统/工具 → 返回 false
    expect(
      isNonMessageGroupContent({ content: '普通内容', role: 'assistant' } as unknown as GroupMessage),
    ).toBe(false);
  });

  it('消息 content 缺失 → content ?? "" 兜底返回 true(L232 idx1)', () => {
    // content 为 undefined(nullish)→ ?? "" 命中 idx1 → 空内容 → 返回 true
    expect(
      isNonMessageGroupContent({ message_type: 'bot', sender: 'b', role: 'assistant' } as unknown as GroupMessage),
    ).toBe(true);
  });
});

describe('GroupDrillDown resolveOwnedViewBot 纯函数补分支', () => {
  it('仅 driver 角色 owned → 走 manager||driver 的 driver 分支', () => {
    const r = resolveOwnedViewBot(
      [
        { actor_id: 'd:me', actor_kind: 'bot', name: 'd', role: 'driver', mode: 'auto' },
        { actor_id: 'x:other', actor_kind: 'bot', name: 'x', role: 'worker', mode: 'auto' },
      ],
      'me',
    );
    expect(r?.actor_id).toBe('d:me');
  });

  it('owner 仅有非 worker/manager/driver → owned[0]', () => {
    const r = resolveOwnedViewBot(
      [
        { actor_id: 'c:me', actor_kind: 'bot', name: 'c', role: 'consultant', mode: 'auto' },
      ],
      'me',
    );
    expect(r?.actor_id).toBe('c:me');
  });
});
