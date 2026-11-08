/** @jest-environment jsdom */
import { useBotBrowseConfig } from '@/hooks/useBotBrowseConfig';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useWorkIdentityAccess } from '@/hooks/useWorkIdentityAccess';
import { botConfigService } from '@/services/lab';
import { botSessionService } from '@/services/workspace/botSessionService';
import '@testing-library/jest-dom';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/hooks/useWorkIdentityAccess', () => ({ useWorkIdentityAccess: jest.fn() }));
jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: {
    listOwnedBotsWithMeta: jest.fn(),
  },
}));
jest.mock('@/services/lab', () => ({
  botConfigService: {
    listSubscriptions: jest.fn(),
    enableSubscription: jest.fn(),
    disableSubscription: jest.fn(),
    updateNote: jest.fn(),
  },
  BROWSE_NOTE_MAX_LENGTH: 512,
}));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedUseWorkIdentityAccess = useWorkIdentityAccess as jest.MockedFunction<
  typeof useWorkIdentityAccess
>;
const mockedBotConfigService = botConfigService as jest.Mocked<typeof botConfigService>;
const mockedListOwnedBotsWithMeta = botSessionService.listOwnedBotsWithMeta as jest.MockedFunction<
  typeof botSessionService.listOwnedBotsWithMeta
>;

const ownedBot = {
  botId: 'owned:b1',
  realBotId: 'b1',
  displayName: '我的 Bot',
  ownerId: '149844',
  online: true,
  chatable: true,
};

describe('useBotBrowseConfig', () => {
  beforeEach(() => {
    jest.clearAllMocks();

    mockedUseHumanIdentity.mockReturnValue({
      identity: {
        userId: ' 149844 ',
        displayName: '当前用户',
        avatarUrl: 'avatar.png',
        online: true,
      },
      status: 'ready',
    });
    mockedUseWorkIdentityAccess.mockReturnValue({
      activeIdentity: null,
      activeIdentityKind: null,
    });
    mockedListOwnedBotsWithMeta.mockResolvedValue({
      ok: true,
      data: { bots: [ownedBot], hasAgentCodingBots: false },
    });
    mockedBotConfigService.listSubscriptions.mockResolvedValue([]);
  });

  test('模块级 human 身份已就绪但全局工作身份为空时，也拉取我的 Bot 列表', async () => {
    const { result } = renderHook(() => useBotBrowseConfig());

    await waitFor(() => expect(mockedListOwnedBotsWithMeta).toHaveBeenCalledWith('149844'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.rows).toMatchObject([{ botId: 'b1', botName: '我的 Bot' }]));
    expect(result.current.gated).toBe(false);
  });

  test('Bot 身份下不拉取我的 Bot 列表，并保持面板提示', async () => {
    mockedUseWorkIdentityAccess.mockReturnValue({
      activeIdentity: {
        id: 'bot:b1',
        kind: 'bot',
        displayName: '我的 Bot',
        online: true,
      },
      activeIdentityKind: 'bot',
    });

    const { result } = renderHook(() => useBotBrowseConfig());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockedListOwnedBotsWithMeta).not.toHaveBeenCalled();
    expect(result.current.gated).toBe(true);
  });
});
