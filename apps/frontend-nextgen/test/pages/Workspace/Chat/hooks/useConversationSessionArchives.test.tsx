/** @jest-environment jsdom */
import {
  defaultCapabilities,
  extendCapabilities,
  type CodingBotSessionArchiveListInput,
  type CodingBotSessionArchiveListResult,
  type CodingBotSessionArchivePreviewInput,
  type CodingBotSessionArchivePreviewResult,
  type CodingBotSessionArchiveRestoreInput,
  type CodingBotSessionArchiveRestoreResult,
  type CodingBotRestoredSessionListInput,
  type CodingBotRestoredSessionListResult,
} from '@/capabilities';
import type {
  ConversationBotView,
  CodingBotSessionArchiveItem,
  CodingBotRestoredSessionItem,
} from '@/domain/conversation/types';
import { useConversationSessionArchives } from '@/pages/Workspace/Chat/hooks/useConversationSessionArchives';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: {
    listManagedSessions: require('jest-mock').fn(),
  },
}));

const listManagedSessions = conversationService.listManagedSessions as unknown as jest.Mock;

const codingBot: ConversationBotView = {
  bot: {
    botId: 'coding-bot:2088',
    realBotId: 'coding-bot',
    ownerId: '2088',
    displayName: 'Coding Bot',
    online: true,
    chatable: true,
    engine: 'claude_code',
    isAgentCodingBot: true,
    botType: 'service',
    runtimeStage: 'online',
  },
  section: 'managed',
};

const restoredAt = '2026-11-16T10:00:00.000Z';

const archiveItem = (sessionKey: string, over: Partial<CodingBotSessionArchiveItem> = {}): CodingBotSessionArchiveItem => ({
  sessionKey,
  archiveId: `archive-${sessionKey}`,
  title: `归档 ${sessionKey}`,
  sessionKind: 'user',
  createdAt: restoredAt,
  updatedAt: restoredAt,
  lastActiveAt: restoredAt,
  archivedAt: restoredAt,
  messageCount: 3,
  restoredAt: null,
  restoreStatus: 'archived',
  restore: null,
  sessionSource: 'mine',
  ...over,
});

const restoredItem = (
  sessionKey: string,
  conversationSessionId = sessionKey,
): CodingBotRestoredSessionItem => ({
  sessionKey,
  archiveId: `archive-${sessionKey}`,
  restoredAt,
  conversation: {
    sessionId: conversationSessionId,
    title: `恢复 ${sessionKey}`,
    messageCount: 3,
    gmtModified: restoredAt,
    gmtCreate: restoredAt,
  },
  sessionSource: 'mine',
});

const sessionOf = (sessionId: string) => ({
  sessionId,
  botId: codingBot.bot.botId,
  title: `会话 ${sessionId}`,
  messageCount: 3,
  gmtModified: restoredAt,
  gmtCreate: restoredAt,
});

function activateArchiveScope() {
  useConversationStore.getState().reset();
  const store = useConversationStore.getState();
  const botId = codingBot.bot.botId;
  store.setManagedBotScope(botId, 'archived');
  store.setExpandedBot(botId, true);
  store.selectConversation({
    botId,
    section: 'managed',
    origin: 'mine',
    scope: 'archived',
    friendUserId: null,
    sessionId: null,
  });
}

function renderArchives(onRestoredSessionFocus = jest.fn()) {
  return renderHook(() =>
    useConversationSessionArchives({
      userId: '327325',
      managedBots: [codingBot],
      onRestoredSessionFocus,
    }),
  );
}

function installHost(archiveList: CodingBotSessionArchiveItem[]) {
  const host = {
    list: jest.fn<Promise<CodingBotSessionArchiveListResult>, [CodingBotSessionArchiveListInput]>(
      async (input) => ({
        ok: true,
        items: input.operationId ? [] : archiveList,
        nextCursor: null,
        catalogRevision: null,
        availability: 'supported',
        restoreAvailability: 'supported',
      }),
    ),
    restore: jest.fn<Promise<CodingBotSessionArchiveRestoreResult>, [CodingBotSessionArchiveRestoreInput]>(),
    preview: jest.fn<Promise<CodingBotSessionArchivePreviewResult>, [CodingBotSessionArchivePreviewInput]>(),
    listRestored: jest.fn<Promise<CodingBotRestoredSessionListResult>, [CodingBotRestoredSessionListInput]>(
      async () => ({ ok: true, items: [] }),
    ),
  };
  extendCapabilities({
    getCodingBotConversationHost: () => ({
      status: 'available' as const,
      value: { Stage: () => null, sessionArchive: host },
    }),
  });
  return host;
}

beforeEach(() => {
  listManagedSessions.mockReset();
  listManagedSessions.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  activateArchiveScope();
});

afterEach(() => {
  extendCapabilities(defaultCapabilities);
});

describe('useConversationSessionArchives', () => {
  it('supports service Coding Bot archives with real bot, owner, user and runtime stage context', async () => {
    const host = installHost([archiveItem('s1')]);
    renderArchives();

    await waitFor(() => expect(useConversationStore.getState().sessionsByBotId[codingBot.bot.botId]?.sessionArchives?.loading).toBe(false));

    expect(host.list).toHaveBeenCalledWith(
      expect.objectContaining({
        botId: 'coding-bot',
        ownerId: '2088',
        userId: '327325',
        stage: 'online',
        limit: 50,
      }),
    );
    expect(useConversationStore.getState().sessionsByBotId[codingBot.bot.botId]?.sessionArchives).toMatchObject({
      availability: 'supported',
      restoreAvailability: 'supported',
    });
  });

  it('uses listRestored and auto-focuses the actual restored conversation after a single restore', async () => {
    const archive = archiveItem('s1');
    const host = installHost([archive]);
    host.restore.mockResolvedValue({
      ok: true,
      requested: [
        {
          sessionKey: 's1',
          archiveId: archive.archiveId,
          operationId: 'op-single',
          status: 'queued' as const,
          errorCode: null,
          updatedAt: restoredAt,
          sequence: 1,
        },
      ],
      operationIds: ['op-single'],
    });
    host.list.mockImplementation(async (input: { operationId?: string }) => {
      if (!input.operationId) {
        return { ok: true, items: [archive], nextCursor: null, catalogRevision: null, availability: 'supported', restoreAvailability: 'supported' };
      }
      return {
        ok: true,
        items: [
          archiveItem('s1', {
            restoreStatus: 'succeeded',
            restoredAt,
            restore: {
              sessionKey: 's1',
              archiveId: archive.archiveId,
              operationId: 'op-single',
              status: 'succeeded' as const,
              errorCode: null,
              updatedAt: restoredAt,
              sequence: 2,
            },
          }),
        ],
        nextCursor: null,
        catalogRevision: null,
        availability: 'supported',
        restoreAvailability: 'supported',
      };
    });
    host.listRestored.mockResolvedValue({ ok: true, items: [restoredItem('s1', 'session-restored')] });
    listManagedSessions.mockResolvedValue({ ok: true, data: { items: [sessionOf('session-restored')], total: 1 } });

    const onRestoredSessionFocus = jest.fn();
    const { result } = renderArchives(onRestoredSessionFocus);
    await waitFor(() => expect(result.current.host).not.toBeNull());
    await act(async () => {
      const restore = await result.current.restore({ view: codingBot, origin: 'mine', sessionKeys: ['s1'], focusRestored: true });
      expect(restore.ok).toBe(true);
    });

    await waitFor(() => expect(onRestoredSessionFocus).toHaveBeenCalledTimes(1));
    expect(host.list).toHaveBeenCalledWith(expect.objectContaining({ operationId: 'op-single' }));
    expect(host.listRestored).toHaveBeenCalledWith(
      expect.objectContaining({ botId: 'coding-bot', ownerId: '2088', userId: '327325', stage: 'online' }),
    );
    expect(onRestoredSessionFocus).toHaveBeenCalledWith({
      view: codingBot,
      origin: 'mine',
      sessionId: 'session-restored',
    });
    const state = useConversationStore.getState();
    const archives = state.sessionsByBotId[codingBot.bot.botId]?.sessionArchives;
    expect(archives?.restoredItems).toMatchObject([
      { sessionKey: 's1', conversation: { sessionId: 'session-restored' } },
    ]);
    expect(archives?.items[0]?.restoreStatus).toBe('succeeded');
  });

  it('refreshes and highlights a batch restore without auto-switching every session', async () => {
    const first = archiveItem('s1');
    const second = archiveItem('s2');
    const host = installHost([first, second]);
    host.restore.mockResolvedValue({
      ok: true,
      requested: [first, second].map((item, index) => ({
        sessionKey: item.sessionKey,
        archiveId: item.archiveId,
        operationId: 'op-batch',
        status: 'queued' as const,
        errorCode: null,
        updatedAt: restoredAt,
        sequence: index + 1,
      })),
      operationIds: ['op-batch'],
    });
    host.list.mockImplementation(async (input: { operationId?: string }) => ({
      ok: true,
      items: input.operationId
        ? [first, second].map((item, index) =>
            archiveItem(item.sessionKey, {
              restoreStatus: 'succeeded',
              restoredAt,
              restore: {
                sessionKey: item.sessionKey,
                archiveId: item.archiveId,
                operationId: 'op-batch',
                status: 'succeeded' as const,
                errorCode: null,
                updatedAt: restoredAt,
                sequence: index + 3,
              },
            }),
          )
        : [first, second],
      nextCursor: null,
      catalogRevision: null,
      availability: 'supported',
      restoreAvailability: 'supported',
    }));
    host.listRestored.mockResolvedValue({ ok: true, items: [restoredItem('s1'), restoredItem('s2')] });
    listManagedSessions.mockResolvedValue({ ok: true, data: { items: [sessionOf('s1'), sessionOf('s2')], total: 2 } });

    const onRestoredSessionFocus = jest.fn();
    const { result } = renderArchives(onRestoredSessionFocus);
    await waitFor(() => expect(result.current.host).not.toBeNull());
    await act(async () => {
      const restore = await result.current.restore({ view: codingBot, origin: 'mine', sessionKeys: ['s1', 's2'], focusRestored: false });
      expect(restore.ok).toBe(true);
    });

    await waitFor(() => {
      const archives = useConversationStore.getState().sessionsByBotId[codingBot.bot.botId]?.sessionArchives;
      expect(archives?.operationIds).toHaveLength(0);
      expect(archives?.restoredItems).toHaveLength(2);
      expect(archives?.items.map((item) => item.restoreStatus)).toEqual(['succeeded', 'succeeded']);
    });
    expect(onRestoredSessionFocus).not.toHaveBeenCalled();
    expect(listManagedSessions).toHaveBeenCalledWith(codingBot.bot, '327325', 'all', 1, 10, '');
    expect(useConversationStore.getState().selectedScope).toBe('archived');
  });

  it('does not finish restore polling before the operation result is terminal', async () => {
    const archive = archiveItem('s1');
    const host = installHost([archive]);
    host.restore.mockResolvedValue({
      ok: true,
      requested: [
        {
          sessionKey: 's1',
          archiveId: archive.archiveId,
          operationId: 'op-pending',
          status: 'queued' as const,
          errorCode: null,
          updatedAt: restoredAt,
          sequence: 1,
        },
      ],
      operationIds: ['op-pending'],
    });
    host.list.mockImplementation(async (input: { operationId?: string }) => ({
      ok: true,
      items: input.operationId
        ? [
            archiveItem('s1', {
              restoreStatus: 'restoring',
              restore: {
                sessionKey: 's1',
                archiveId: archive.archiveId,
                operationId: 'op-pending',
                status: 'restoring' as const,
                errorCode: null,
                updatedAt: restoredAt,
                sequence: 2,
              },
            }),
          ]
        : [archive],
      nextCursor: null,
      catalogRevision: null,
      availability: 'supported',
      restoreAvailability: 'supported',
    }));

    const { result, unmount } = renderArchives();
    await waitFor(() => expect(result.current.host).not.toBeNull());
    await act(async () => {
      const restore = await result.current.restore({ view: codingBot, origin: 'mine', sessionKeys: ['s1'], focusRestored: true });
      expect(restore.ok).toBe(true);
    });

    await waitFor(() => expect(host.list).toHaveBeenCalledWith(expect.objectContaining({ operationId: 'op-pending' })));
    await waitFor(() => {
      const archives = useConversationStore.getState().sessionsByBotId[codingBot.bot.botId]?.sessionArchives;
      expect(archives?.operationIds).toEqual(['op-pending']);
      expect(archives?.items[0]?.restoreStatus).toBe('restoring');
    });
    unmount();
  });
});
