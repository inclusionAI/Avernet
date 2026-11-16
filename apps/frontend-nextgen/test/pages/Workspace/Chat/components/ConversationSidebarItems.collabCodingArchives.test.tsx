/** @jest-environment jsdom */
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getConversationInstanceScope: () => ({ value: null }),
    getCodingBotConversationHost: () => ({
      value: {
        runSessionAction: jest.fn(async () => ({ ok: true })),
        sessionArchive: {
          list: jest.fn(async () => ({ ok: true })),
          restore: jest.fn(async () => ({ ok: true })),
          preview: jest.fn(async () => ({ ok: true })),
          listRestored: jest.fn(async () => ({ ok: true })),
        },
      },
    }),
  }),
}));

import type { CodingBotSessionArchiveItem, ConversationBotView } from '@/domain/conversation/types';
import type { ConversationSidebarProps } from '@/pages/Workspace/Chat/components/ConversationSidebar';
import { ManagedItem } from '@/pages/Workspace/Chat/components/ConversationSidebarItems';
import { useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { emptyList, seedStore, sessionsModel, sidebarProps } from './ConversationSidebar.testUtils';

const bot = {
  botId: 'coding-team:entity-other',
  realBotId: 'coding-team',
  ownerId: 'entity-other',
  displayName: '协作 Coding Bot',
  online: true,
  chatable: true,
  isFriendBot: false,
  isAgentCodingBot: true,
  engine: 'aicoding',
};
const view: ConversationBotView = { bot, section: 'team' };
const botId = bot.botId;

const collabSessionId = 'agent:main:session:collab:agent:member-task';
const archiveItem: CodingBotSessionArchiveItem = {
  sessionKey: collabSessionId,
  archiveId: 'archive-collab-1',
  title: '协作归档会话',
  sessionKind: 'user',
  createdAt: '2026-11-01T08:00:00.000Z',
  updatedAt: '2026-11-01T08:00:00.000Z',
  lastActiveAt: '2026-11-01T08:00:00.000Z',
  archivedAt: '2026-11-02T08:00:00.000Z',
  messageCount: 6,
  restoredAt: null,
  restoreStatus: 'archived',
  restore: null,
  sessionSource: 'collab',
};

const emptyArchives = () => ({
  items: [],
  nextCursor: null,
  catalogRevision: null,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
  availability: 'supported' as const,
  restoreAvailability: 'supported' as const,
  operationIds: [],
  isSubmitting: false,
  restoreError: null,
  restoredItems: [],
});

beforeEach(() => {
  useConversationStore.getState().reset();
});

function setup(overrides: Partial<ConversationSidebarProps> = {}) {
  const props = sidebarProps({ sessions: sessionsModel(), ...overrides });
  return { props };
}

describe('协作 Coding Bot 的协作会话与归档能力', () => {
  it('即使不是当前用户 owner，也在协作来源下展示普通协作会话', () => {
    const { props } = setup();
    seedStore((store) => {
      store.setManagedBotOrigin(botId, 'collab', { supportsScopesOutsideMine: true });
      store.setManagedBotCache(botId, {
        botId,
        origin: 'collab',
        scope: 'all',
        sessions: {
          ...emptyList(),
          total: 1,
          items: [
            {
              sessionId: collabSessionId,
              botId,
              title: '协作派发会话',
              messageCount: 3,
              gmtCreate: '2026-11-01T08:00:00.000Z',
              gmtModified: '2026-11-01T08:10:00.000Z',
            },
          ],
        },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      });
      store.setExpandedBot(botId, true);
      store.selectConversation({
        botId,
        section: 'team',
        origin: 'collab',
        scope: 'all',
        friendUserId: null,
        sessionId: null,
      });
    });

    render(<ManagedItem view={view} props={{ ...props, store: useConversationStore.getState() }} />);

    expect(screen.getByText('协作派发会话')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    expect(screen.getByRole('button', { name: '协作' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已归档' })).toBeInTheDocument();
  });

  it('协作来源的已归档 scope 能渲染协作归档列表', () => {
    const { props } = setup();
    seedStore((store) => {
      store.setManagedBotOrigin(botId, 'collab', { supportsScopesOutsideMine: true });
      store.setManagedBotScope(botId, 'archived');
      store.setManagedBotCache(botId, {
        botId,
        origin: 'collab',
        scope: 'archived',
        sessions: emptyList(),
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
        sessionArchives: { ...emptyArchives(), items: [archiveItem] },
      });
      store.setExpandedBot(botId, true);
      store.selectConversation({
        botId,
        section: 'team',
        origin: 'collab',
        scope: 'archived',
        friendUserId: null,
        sessionId: null,
      });
    });

    render(<ManagedItem view={view} props={{ ...props, store: useConversationStore.getState() }} />);

    expect(screen.getByText('协作归档会话')).toBeInTheDocument();
    expect(screen.queryByLabelText('搜索归档会话')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '会话ID检索' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量恢复' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发起人与会话筛选' }));
    expect(screen.getByRole('button', { name: '协作', hidden: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已归档', hidden: false })).toBeInTheDocument();
  });
});
