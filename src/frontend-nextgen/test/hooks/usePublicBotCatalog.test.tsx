/** @jest-environment jsdom */
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { usePublicBotCatalog } from '@/pages/Workspace/hooks/usePublicBotCatalog';
import { collaborationSquareBotService } from '@/services/collaborationSquare';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;
const mockedNotifySuccess = notifySuccess as jest.MockedFunction<typeof notifySuccess>;

const resultBot = (id: string): PublicBot => ({
  id,
  name: id,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
});

describe('usePublicBotCatalog', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (history.push as jest.Mock).mockClear();
    useWorkspaceStore.getState().reset();
    useWorkspaceStore
      .getState()
      .setIdentities([{ id: 'human_327325', kind: 'user', displayName: '当前用户', online: true }], 'human_327325');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '327325', displayName: '当前用户', online: true },
      status: 'ready',
    });
    mockedNotifyError.mockClear();
    mockedNotifySuccess.mockClear();
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  test('human 角色下 viewer 使用 humanIdentity.userId', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('b1')]);
    const { unmount } = renderHook(() =>
      usePublicBotCatalog({
        activeIdentity: { id: 'human_327325', kind: 'user', displayName: '当前用户', online: true },
        enabled: true,
      }),
    );
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listBots).toHaveBeenCalledWith(
      expect.objectContaining({ viewerActorType: 'human', viewerActorId: '327325' }),
      expect.objectContaining({ actorId: 'human_327325', userId: '327325' }),
      expect.any(AbortSignal),
    );
    unmount();
  });

  test('bot 角色下 viewer 使用 bot 身份 id', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('b1')]);
    const { unmount } = renderHook(() =>
      usePublicBotCatalog({
        activeIdentity: { id: 'bot-xyz', kind: 'bot', displayName: 'Bot', online: true },
        enabled: true,
      }),
    );
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listBots).toHaveBeenCalledWith(
      expect.objectContaining({ viewerActorType: 'bot', viewerActorId: 'bot-xyz' }),
      expect.any(Object),
      expect.any(AbortSignal),
    );
    unmount();
  });

  test('智能搜索空关键词不发请求并清空列表', async () => {
    const listBots = jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('b1')]);
    const discoverBots = jest.spyOn(collaborationSquareBotService, 'discoverBots').mockResolvedValue([]);
    const { result, unmount } = renderHook(() => usePublicBotCatalog({ enabled: true }));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listBots).toHaveBeenCalledTimes(1);

    act(() => result.current.setMode('smart'));
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(discoverBots).not.toHaveBeenCalled();
    expect(result.current.bots).toHaveLength(0);
    expect(result.current.loading).toBe(false);
    unmount();
  });

  test('智能搜索输入关键词后调用 discoverBots 并带回 viewer', async () => {
    const discoverBots = jest
      .spyOn(collaborationSquareBotService, 'discoverBots')
      .mockResolvedValue([resultBot('smart')]);
    const { result, unmount } = renderHook(() => usePublicBotCatalog({ enabled: true }));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    act(() => result.current.setMode('smart'));
    act(() => result.current.setQuery('代码'));
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(discoverBots).toHaveBeenCalledWith(
      expect.objectContaining({ keyword: '代码', viewerActorType: 'human', viewerActorId: '327325' }),
      expect.any(Object),
      expect.any(AbortSignal),
    );
    expect(result.current.bots.map((bot) => bot.id)).toEqual(['smart']);
    unmount();
  });

  test('对话协作 Bot 广场分享链接同样携带名称提示', async () => {
    const bot = { ...resultBot('bot:shared'), name: '项目助手' };
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([bot]);
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const { result, unmount } = renderHook(() => usePublicBotCatalog({ enabled: true }));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.share(bot);
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/collaboration-square/bots?resource=bot&id=bot%3Ashared&name=%E9%A1%B9%E7%9B%AE%E5%8A%A9%E6%89%8B`,
    );
    expect(mockedNotifySuccess).toHaveBeenCalledWith('分享链接已复制');
    expect(mockedNotifyError).not.toHaveBeenCalled();
    unmount();
  });

  test('human 角色下申请好友 from_actor 为登录人类 userId', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('bot-1')]);
    const requestFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const { result, unmount } = renderHook(() => usePublicBotCatalog({ enabled: true }));

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.primaryAction(result.current.bots[0]);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(requestFriendship).toHaveBeenCalledWith('bot-1', expect.any(Object), undefined, {
      type: 'human',
      id: '327325',
    });
    expect(result.current.bots[0].relationshipStatus).toBe('applying');
    unmount();
  });

  test('bot 角色下申请好友 from_actor 为当前 bot 身份', async () => {
    jest.spyOn(collaborationSquareBotService, 'listBots').mockResolvedValue([resultBot('bot-1')]);
    const requestFriendship = jest
      .spyOn(collaborationSquareBotService, 'requestBotFriendship')
      .mockResolvedValue({ status: 'applying' });
    const { result, unmount } = renderHook(() =>
      usePublicBotCatalog({
        activeIdentity: { id: 'bot-xyz', kind: 'bot', displayName: 'Bot', online: true },
        enabled: true,
      }),
    );

    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await act(async () => {
      result.current.primaryAction(result.current.bots[0]);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(requestFriendship).toHaveBeenCalledWith('bot-1', expect.any(Object), undefined, {
      type: 'bot',
      id: 'bot-xyz',
    });
    unmount();
  });
});
