/** @jest-environment jsdom */
import { useBotWorkshop } from '@/hooks/useBotWorkshop';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import type { BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopRequestIdentity: jest.fn(),
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: jest.fn(),
}));
jest.mock('@/hooks/useBotHealthCheck', () => ({
  useBotHealthCheck: () => ({ open: false, openHealthCheck: jest.fn() }),
}));
jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: {
    list: jest.fn(),
    getCreateSpaces: jest.fn(() => []),
  },
  getBotActionAvailability: jest.fn(() => []),
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
