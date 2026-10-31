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
  sessions.mockResolvedValue({ ok: true, data: [] });
  models.mockResolvedValue({ ok: true, data: [] });
  jest.mocked(useBotChat).mockReturnValue({
    chat: { messages: [], isRequesting: false }, connectionStatus: 'disconnected',
    send: jest.fn(), stop: jest.fn(),
  } as unknown as ReturnType<typeof useBotChat>);
  jest.mocked(useBotCapabilitySets).mockReturnValue(null);
});

afterEach(() => useWorkspaceStore.setState({ activeIdentityId: null }));

test('调试对话标题区:无下边框、无 Human-to-Agent 副标题', async () => {
  render(
    <DebugChatPanel
      bot={bot}
      authenticatedUserId="197262"
      onClose={jest.fn()}
      onToggleFullscreen={jest.fn()}
    />,
  );
  await waitFor(() => expect(sessions).toHaveBeenCalled());

  // 标题区行:56px 定高、不描下边框(借助关闭按钮定位所在行)
  const header = screen.getByLabelText('关闭调试对话').closest('div.h-14')!;
  expect(header).not.toBeNull();
  expect(header.className).not.toContain('border-b');

  // 副标题已删除
  expect(screen.queryByText(/Human-to-Agent/)).not.toBeInTheDocument();
  expect(screen.queryByText(/草稿环境|线上环境/)).not.toBeInTheDocument();
});

test('工具栏顺序:新建会话 → 刷新 → 全屏 → 历史会话 → 关闭,关闭前有短分割线', async () => {
  render(
    <DebugChatPanel
      bot={bot}
      authenticatedUserId="197262"
      onClose={jest.fn()}
      onToggleFullscreen={jest.fn()}
    />,
  );
  await waitFor(() => expect(sessions).toHaveBeenCalled());

  const labels = ['新建会话', '刷新调试会话', '全屏调试对话窗口', '历史会话', '关闭调试对话'];
  const nodes = labels.map((label) => screen.getByLabelText(label));
  for (let index = 0; index < nodes.length - 1; index += 1) {
    expect(
      nodes[index].compareDocumentPosition(nodes[index + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  }

  // 关闭按钮左侧是短分割线(1px 宽的竖条)
  const close = screen.getByLabelText('关闭调试对话');
  const divider = close.previousElementSibling;
  expect(divider?.className).toContain('w-px');
  expect(divider?.className).toContain('h-5');
});
