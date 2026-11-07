/** @jest-environment jsdom */
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { useLegacyShareLinkGuard } from '@/pages/Workspace/Chat/hooks/useLegacyShareLinkGuard';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { collaborationSquareBotService } from '@/services/collaborationSquare/collaborationSquareService';
import { useConversationStore } from '@/stores/conversationStore';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/services/collaborationSquare/collaborationSquareService', () => ({
  collaborationSquareBotService: {
    resolvePublicBotById: jest.fn(),
    requestBotFriendship: jest.fn(),
  },
}));
jest.mock('@/stores/conversationStore', () => {
  const selectConversation = jest.fn();
  return { useConversationStore: { getState: () => ({ selectConversation }) } };
});

const mockedResolve = collaborationSquareBotService.resolvePublicBotById as unknown as jest.Mock;
const mockedRequestFriendship = collaborationSquareBotService.requestBotFriendship as unknown as jest.Mock;
const mockedNotifyError = notifyError as unknown as jest.Mock;
const mockedNotifySuccess = notifySuccess as unknown as jest.Mock;
const createSessionMock = jest.fn();
const selectConversation = (useConversationStore.getState() as unknown as {
  selectConversation: jest.Mock;
}).selectConversation;

const legacyId = '20260407_jmg31orw:152819';
const publicBotFixture: PublicBot = {
  id: 'bot-uuid-1',
  name: '老版分享目标',
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
  friendRequestBotId: legacyId,
};

// 目录加载生命周期：初始加载期（hydrated=false）→ 就绪（hydrated=true）。
// Guard 只信任"见过加载期之后的空闲"，renderGuard 统一走该序列。
const renderGuard = (overrides: Partial<Parameters<typeof useLegacyShareLinkGuard>[0]> = {}) => {
  const props: Parameters<typeof useLegacyShareLinkGuard>[0] = {
    hydrated: false,
    selectedBotId: legacyId,
    knownBotIds: new Set(['other:1']),
    friendBotIds: new Set<string>(),
    createSession: createSessionMock,
    friendSessionsLoaded: true,
    selectedSection: 'friend' as const,
    userId: '900003',
    ...overrides,
  };
  const utils = renderHook(
    ({ hydrated }: { hydrated: boolean }) => useLegacyShareLinkGuard({ ...props, hydrated }),
    { initialProps: { hydrated: false } },
  );
  utils.rerender({ hydrated: true });
  return utils;
};

describe('useLegacyShareLinkGuard', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/workspace/chat');
    mockedResolve.mockReset();
    mockedRequestFriendship.mockReset();
    createSessionMock.mockClear();
    mockedNotifyError.mockClear();
    mockedNotifySuccess.mockClear();
    selectConversation.mockClear();
  });

  it('老链接进入且目录无该 Bot 时反查并暴露弹窗状态', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue(publicBotFixture);
    const { result } = renderGuard();
    await waitFor(() => expect(result.current.legacyBot).toEqual(publicBotFixture));
    expect(mockedResolve).toHaveBeenCalledWith(legacyId, { actorId: 'human_900003', userId: '900003' });
  });

  it('目录命中（有好友关系）时不触发反查', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    const { result } = renderGuard({ knownBotIds: new Set([legacyId]) });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(mockedResolve).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
  });

  it('URL 无老链接 id 参数时完全不触发', async () => {
    const { result } = renderGuard({ selectedBotId: 'plain:1' });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(mockedResolve).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
  });

  it('反查无果时提示不可用并清参回退', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue(null);
    const { result } = renderGuard();
    await waitFor(() => expect(mockedNotifyError).toHaveBeenCalledWith('该 Bot 已下线或已取消公开'));
    expect(result.current.legacyBot).toBeNull();
    expect(selectConversation).toHaveBeenCalledWith(expect.objectContaining({ botId: null, section: null }));
  });

  it('反查到非 ACTIVE Bot 时同样走不可用分支', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue({ ...publicBotFixture, status: 'DISABLED' });
    renderGuard();
    await waitFor(() => expect(mockedNotifyError).toHaveBeenCalledWith('该 Bot 已下线或已取消公开'));
  });

  it('确认后发出好友申请并按 applying 反馈后清参回退', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue(publicBotFixture);
    mockedRequestFriendship.mockResolvedValue({ status: 'applying' });
    const { result } = renderGuard();
    await waitFor(() => expect(result.current.legacyBot).toEqual(publicBotFixture));
    result.current.onConfirm();
    await waitFor(() => expect(mockedRequestFriendship).toHaveBeenCalled());
    expect(mockedRequestFriendship).toHaveBeenCalledWith(
      publicBotFixture.id,
      { actorId: 'human_900003', userId: '900003' },
      legacyId,
      { type: 'human', id: '900003' },
    );
    await waitFor(() => expect(mockedNotifySuccess).toHaveBeenCalledWith('好友申请已提交'));
    await waitFor(() => expect(result.current.legacyBot).toBeNull());
  });

  it('目录未发起加载的初始假阳性空闲帧不触发反查（好友 Bot 时序竞争回归）', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue(publicBotFixture);
    // 全程 hydrated=true 且从未经历加载期：复现目录 hook 初始空态的假阳性帧。
    const { result } = renderHook(() =>
      useLegacyShareLinkGuard({
        hydrated: true,
        selectedBotId: legacyId,
        knownBotIds: new Set(['other:1']),
        friendBotIds: new Set<string>(),
        friendSessionsLoaded: true,
        selectedSection: 'friend' as const,
        userId: '900003',
      }),
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(mockedResolve).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
  });

  it('好友命中的老链接落地后走页内新建会话链路（列表前置并选中）', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    const { result } = renderGuard({ knownBotIds: new Set([legacyId]), friendBotIds: new Set([legacyId]) });
    await waitFor(() => expect(createSessionMock).toHaveBeenCalledWith(legacyId));
    expect(result.current.legacyBot).toBeNull(); // 好友命中不弹申请窗。
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it('会话列表缓存未就绪时暂缓创建会话，避免新会话无法前置左侧列表', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    const { result } = renderGuard({
      knownBotIds: new Set([legacyId]),
      friendBotIds: new Set([legacyId]),
      friendSessionsLoaded: false,
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it('分组归类未稳定（section 为 null）时暂缓创建会话（选中保护时序回归）', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    const { result } = renderGuard({
      knownBotIds: new Set([legacyId]),
      friendBotIds: new Set([legacyId]),
      selectedSection: null,
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it('未注入新建会话链路时好友命中仅定位不开会话', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    const { result } = renderGuard({
      knownBotIds: new Set([legacyId]),
      friendBotIds: new Set([legacyId]),
      createSession: undefined,
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(result.current.legacyBot).toBeNull();
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it('取消时不发申请并清参回退', async () => {
    window.history.replaceState(null, '', `/workspace/chat?id=${encodeURIComponent(legacyId)}`);
    mockedResolve.mockResolvedValue(publicBotFixture);
    const { result } = renderGuard();
    await waitFor(() => expect(result.current.legacyBot).toEqual(publicBotFixture));
    result.current.onClose();
    await waitFor(() => expect(result.current.legacyBot).toBeNull());
    expect(mockedRequestFriendship).not.toHaveBeenCalled();
    expect(selectConversation).toHaveBeenCalledWith(expect.objectContaining({ botId: null }));
  });
});
