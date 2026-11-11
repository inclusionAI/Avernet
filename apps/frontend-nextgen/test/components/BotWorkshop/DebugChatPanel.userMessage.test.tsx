/** @jest-environment jsdom */
import { DebugChatPanel } from '@/components/BotWorkshop/Editor/DebugChatPanel';
import type { BotDomain } from '@/domain/botWorkshop';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotCapabilitySets } from '@/pages/Workspace/hooks/useBotCapabilitySets';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('@/pages/Workspace/hooks/useBotChat');
jest.mock('@/pages/Workspace/hooks/useBotCapabilitySets');
jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { listSessions: jest.fn(), listModels: jest.fn(), createSession: jest.fn() },
}));
jest.mock('@/components/BotWorkshop/Editor/DebugChatComposer', () => ({
  DebugChatComposer: () => <div data-testid="composer" />,
}));
jest.mock('@/pages/Workspace/hooks/useBotModels', () => ({
  useBotModels: () => ({ models: [], activeModelId: null, isLoadingModels: false, selectModel: jest.fn() }),
}));

const bot = {
  id: 'default', name: 'Default Bot', ownerId: '197262',
  lifecycle: 'running', serviceMode: 'non-service',
} as BotDomain;
const sessions = jest.mocked(botSessionService.listSessions);
const models = jest.mocked(botSessionService.listModels);

beforeEach(() => {
  jest.clearAllMocks();
  sessions.mockResolvedValue({
    ok: true,
    data: [{ sessionId: 's1', botId: 'default', title: '调试', messageCount: 1, gmtModified: '', gmtCreate: '' }],
  });
  models.mockResolvedValue({ ok: true, data: [] });
  jest.mocked(useBotChat).mockReturnValue({
    chat: { messages: [{ id: 'm1', role: 'user', content: '你好调试' }], isRequesting: false },
    connectionStatus: 'disconnected',
    send: jest.fn(), stop: jest.fn(),
  } as unknown as ReturnType<typeof useBotChat>);
  jest.mocked(useBotCapabilitySets).mockReturnValue(null);
});

afterEach(() => useWorkspaceStore.setState({ activeIdentityId: null }));

test('自身消息右置:整行 justify-end,内容列在前、头像殿后', async () => {
  render(
    <DebugChatPanel
      bot={bot}
      authenticatedUserId="197262"
      onClose={jest.fn()}
      onToggleFullscreen={jest.fn()}
    />,
  );
  await waitFor(() => expect(sessions).toHaveBeenCalled());

  const row = screen.getByText('你好调试').closest('div.group')!;
  expect(row).not.toBeNull();
  // 右置行:justify-end;头像殿后(内容列在前)
  expect(row.className).toContain('justify-end');
  const bubbles = row.querySelector('.bg-muted');
  expect(bubbles).not.toBeNull();
  const lastChild = row.lastElementChild!;
  expect(lastChild.className).toContain('bg-brand/15');

  // 内容列在前(含正文)、头像殿后
  expect(row.firstElementChild).toContainHTML('你好调试');
  expect(row.lastElementChild!.className).toContain('bg-brand/15');
});
