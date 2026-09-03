/** @jest-environment jsdom */
import { useBotWorkshop } from '@/hooks/useBotWorkshop';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import type { BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopRequestIdentity: jest.fn(),
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: jest.fn(),
}));
jest.mock('@/services/botWorkshop/agentCodingTemplateService', () => ({
  agentCodingTemplateService: {
    list: jest.fn(),
  },
  supportsServiceBot: jest.fn(() => false),
}));
jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: {
    list: jest.fn(),
    getCreateSpaces: jest.fn(() => []),
  },
  getBotActionAvailability: jest.fn(() => []),
}));
jest.mock('@/services/botHealthCheck', () => ({
  botHealthCheckService: {
    toTarget: jest.fn((bot: { id: string }) => ({
      botId: bot.id,
      userId: 'u1',
      context: { entityId: 'e1' },
    })),
    resolveAvailability: jest.fn(() => ({ visible: true, enabled: true })),
  },
}));

const mockedIdentity = useBotWorkshopRequestIdentity as jest.MockedFunction<typeof useBotWorkshopRequestIdentity>;
const mockedSpaceContext = useSpaceContext as jest.MockedFunction<typeof useSpaceContext>;
const mockedList = botWorkshopService.list as jest.MockedFunction<typeof botWorkshopService.list>;
const { agentCodingTemplateService } = jest.requireMock('@/services/botWorkshop/agentCodingTemplateService') as {
  agentCodingTemplateService: { list: jest.Mock };
};
let currentSpaceId: number | undefined;
let spaceInitialized: boolean;

beforeEach(() => {
  currentSpaceId = 10001;
  spaceInitialized = true;
  mockedSpaceContext.mockImplementation((selector) =>
    selector({
      currentSpaceId,
      currentSpace: undefined,
      spaces: [],
      loading: false,
      error: undefined,
      initialized: spaceInitialized,
      setInitialized: jest.fn(),
      setCurrentSpaceId: jest.fn(),
      setSpaces: jest.fn(),
      setLoading: jest.fn(),
      setError: jest.fn(),
      reset: jest.fn(),
    }),
  );
});

afterEach(() => {
  jest.clearAllMocks();
  useBotWorkshopStore.getState().reset();
  useWorkspaceStore.getState().resetWorkspace();
});

it('首次进入时等待用户身份就绪后再加载 Bot 列表', async () => {
  mockedIdentity.mockReturnValue({ ready: false, loading: true, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });

  const { result, rerender } = renderHook(() => useBotWorkshop());

  expect(result.current.loading).toBe(true);
  expect(mockedList).not.toHaveBeenCalled();

  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  rerender();

  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(result.current.loading).toBe(false));
});

it('Open Core 打开云端创建弹窗时不加载 AgentCoding 模板', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  agentCodingTemplateService.list.mockResolvedValue([]);

  const { result } = renderHook(() => useBotWorkshop());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));

  act(() => {
    result.current.openCreateCloud();
  });

  await act(async () => {
    await Promise.resolve();
  });
  expect(agentCodingTemplateService.list).not.toHaveBeenCalled();
});

it('刷新页面时等待空间初始化完成，只加载一次 Bot 列表', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  spaceInitialized = false;
  currentSpaceId = undefined;

  const { rerender } = renderHook(() => useBotWorkshop());
  expect(mockedList).not.toHaveBeenCalled();

  currentSpaceId = 10001;
  spaceInitialized = true;
  rerender();

  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  expect(mockedList).toHaveBeenCalledWith(expect.objectContaining({ spaceId: '10001' }));
});

it('列表每 30 秒静默同步状态，不受微前端容器的 visibilityState 影响', async () => {
  jest.useFakeTimers();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });

  const { unmount } = renderHook(() => useBotWorkshop());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));

  act(() => jest.advanceTimersByTime(30_000));
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
  unmount();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  jest.useRealTimers();
});

it('按全局当前空间加载 Bot，切换空间后自动重新加载', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  currentSpaceId = 10001;

  const { rerender } = renderHook(() => useBotWorkshop());

  await waitFor(() => expect(mockedList).toHaveBeenCalledWith(expect.objectContaining({ spaceId: '10001' })));

  currentSpaceId = 10002;
  rerender();

  await waitFor(() => expect(mockedList).toHaveBeenCalledWith(expect.objectContaining({ spaceId: '10002' })));
});

it('切换页码后使用后端分页参数重新加载', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], total: 45, page: 1, pageSize: 20, warnings: [] });

  const { result } = renderHook(() => useBotWorkshop());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));

  result.current.setPage(2);

  await waitFor(() => expect(mockedList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, pageSize: 20 })));
});

it('从列表进入编辑页时携带列表 display_state 映射出的运行阶段', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  const { result } = renderHook(() => useBotWorkshop());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));

  result.current.openDetail({ id: 'service-bot-1', lifecycle: 'draft' } as BotDomain, 'edit');

  expect(history.push).toHaveBeenCalledWith('/bot-workshop/detail?type=edit&id=service-bot-1&runtime_stage=draft');
});

it('点击健康检查时导航到独立健康检查页面', async () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });

  const { result } = renderHook(() => useBotWorkshop());
  const bot = {
    id: 'bot-1',
    name: '测试 Bot',
    runtime: { engine: 'openclaw', visibleInOpenCore: true },
    harnessContext: { entityId: 'e1' },
  } as unknown as BotDomain;

  result.current.openHealthCheck(bot);

  await waitFor(() => expect(history.push).toHaveBeenCalledWith('/bot-workshop/health-check?id=bot-1'));
});

it('Coding Bot 点击去使用时跳转到 Coding Chat，并携带当前 Bot ID', () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  const { result } = renderHook(() => useBotWorkshop());
  const bot = {
    id: 'coding-bot:2088',
    spaceId: '73',
    spaceName: '测试空间',
    name: '应用 Bot',
    runtime: { engine: 'claude_code', isAgentCodingBot: true, visibleInOpenCore: true },
  } as unknown as BotDomain;

  act(() => {
    result.current.openConversation(bot);
  });

  expect(history.push).toHaveBeenCalledWith(
    '/coding/coding-chat?botId=coding-bot%3A2088&space_id=73&space_name=%E6%B5%8B%E8%AF%95%E7%A9%BA%E9%97%B4',
  );
});

it('点击对话时跳转到用户单聊并展开对应 Bot', () => {
  mockedIdentity.mockReturnValue({ ready: true, loading: false, error: undefined, userId: 'u1' });
  mockedList.mockResolvedValue({ items: [], page: 1, pageSize: 20, warnings: [] });
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_u1', kind: 'user', displayName: '我', online: true },
      { id: 'bot_old:u1', kind: 'bot', displayName: '旧 Bot', online: true },
    ],
    activeIdentityId: 'bot_old:u1',
    view: 'group',
  });
  const { result } = renderHook(() => useBotWorkshop());
  const bot = {
    id: 'bot-1',
    ownerId: '2088',
    name: '测试 Bot',
    runtime: { engine: 'openclaw', visibleInOpenCore: true },
  } as unknown as BotDomain;

  act(() => {
    result.current.openConversation(bot);
  });

  expect(history.push).toHaveBeenCalledWith('/workspace?tab=chat&bot=bot-1%3A2088');
  const state = useWorkspaceStore.getState();
  expect(state.activeIdentityId).toBe('human_u1');
  expect(state.view).toBe('chat');
  expect(state.expandedBotIds).toEqual({ 'bot-1:2088': true });
  expect(state.expandedBotSectionKey['bot-1:2088']).toBe('mine');
  expect(state.selectedBotSessionId).toBeNull();
});
