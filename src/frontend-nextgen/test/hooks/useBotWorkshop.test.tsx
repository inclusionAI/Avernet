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
