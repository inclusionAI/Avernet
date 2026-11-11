/** @jest-environment jsdom */
import { DebugChatPanel } from '@/components/BotWorkshop/Editor/DebugChatPanel';
import type { BotDomain } from '@/domain/botWorkshop';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotCapabilitySets } from '@/pages/Workspace/hooks/useBotCapabilitySets';
import { DetailDebugChat } from '@/pages/BotWorkshop/Detail/DetailDebugChat';
import { botSessionService } from '@/services/workspace/botSessionService';
import { clearBotModelCache } from '@/services/workspace/botModelCache';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

jest.mock('@/pages/Workspace/hooks/useBotChat');
jest.mock('@/pages/Workspace/hooks/useBotCapabilitySets');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@umijs/max', () => ({ history: { push: jest.fn(), replace: jest.fn() } }));
jest.mock('@/services/workspace/botSessionService', () => ({
  ...jest.requireActual('@/services/workspace/botSessionService'),
  botSessionService: { listSessions: jest.fn(), listModels: jest.fn(), createSession: jest.fn() },
}));
jest.mock('@/components/BotWorkshop/Editor/DebugChatComposer', () => ({
  DebugChatComposer: ({ userId }: { userId: string | null }) => {
    const { useState } = require('react');
    const [draft, setDraft] = useState('');
    return <div data-testid="composer" data-user-id={userId}>
      <input aria-label="调试草稿" value={draft} onChange={(event) => setDraft(event.target.value)} />
    </div>;
  },
}));
jest.mock('@/components/ui/Select', () => ({
  Select: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: () => null,
  SelectItem: () => null,
}));

const bot = {
  id: 'default', name: 'Default Bot', ownerId: '197262',
  lifecycle: 'running', serviceMode: 'non-service',
} as BotDomain;
const sessions = jest.mocked(botSessionService.listSessions);
const models = jest.mocked(botSessionService.listModels);
const create = jest.mocked(botSessionService.createSession);
const chat = jest.mocked(useBotChat);

beforeEach(() => {
  jest.mocked(useBotSessionFileUpload).mockReturnValue({ tasks: [], isUploading: false,
    stageFiles: jest.fn(), submit: jest.fn(), removeTask: jest.fn(), cancelAll: jest.fn(),
  });
  clearBotModelCache();
  jest.clearAllMocks();
  sessions.mockResolvedValue({ ok: true, data: [] });
  models.mockResolvedValue({ ok: true, data: [] });
  create.mockResolvedValue({ ok: true, data: {
    sessionId: 's1', botId: 'default', title: 'Debug', messageCount: 0, gmtCreate: '', gmtModified: '',
  } });
  chat.mockReturnValue({
    chat: { messages: [], isRequesting: false }, connectionStatus: 'disconnected',
    send: jest.fn(), stop: jest.fn(),
  } as unknown as ReturnType<typeof useBotChat>);
  jest.mocked(useBotCapabilitySets).mockReturnValue(null);
});

afterEach(() => useWorkspaceStore.setState({ activeIdentityId: null }));

it.each(['bot_03e6abae', 'tc-bot:888888', 'human_888888'])(
  '协作身份 %s 不影响编辑调试请求的登录用户', async (activeIdentityId) => {
    useWorkspaceStore.setState({ activeIdentityId });
    render(<DetailDebugChat bot={bot} authenticatedUserId="197262" />);
    await waitFor(() => expect(sessions).toHaveBeenCalledWith(expect.objectContaining({ ownerId: '197262' }), '197262'));
    expect(models).toHaveBeenCalledWith(expect.any(Object), '197262');
    expect(chat).toHaveBeenLastCalledWith(expect.any(Object), null, undefined, expect.any(Function), '197262');
    expect(jest.mocked(useBotCapabilitySets)).toHaveBeenCalledWith(expect.any(Object), '197262');
    expect(screen.getByTestId('composer')).toHaveAttribute('data-user-id', '197262');
    expect(useBotSessionFileUpload).toHaveBeenLastCalledWith('default', null, '197262', '197262');
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.any(Object), '197262', 'Bot 工坊调试'));
    await act(async () => useWorkspaceStore.setState({ activeIdentityId: 'another-external-bot' }));
    expect(sessions).toHaveBeenCalledTimes(1);
    expect(models).toHaveBeenCalledTimes(1);
  },
);

it('登录身份未就绪时不回退到协作 Bot 身份，身份就绪后再请求', async () => {
  useWorkspaceStore.setState({ activeIdentityId: 'bot_03e6abae' });
  const { rerender } = render(<DebugChatPanel bot={bot} authenticatedUserId={null} />);
  expect(sessions).not.toHaveBeenCalled();
  expect(models).not.toHaveBeenCalled();
  expect(chat).toHaveBeenLastCalledWith(expect.any(Object), null, undefined, expect.any(Function), null);
  rerender(<DebugChatPanel bot={bot} authenticatedUserId="197262" />);
  await waitFor(() => expect(sessions).toHaveBeenCalledWith(expect.any(Object), '197262'));
});

it('协作者编辑时保留 Bot owner，不用 owner 冒充登录用户', async () => {
  useWorkspaceStore.setState({ activeIdentityId: 'tc-bot:146836' });
  render(<DetailDebugChat bot={{ ...bot, ownerId: '146836' }} authenticatedUserId="197262" />);
  await waitFor(() => expect(sessions).toHaveBeenCalledWith(expect.objectContaining({ ownerId: '146836' }), '197262'));
  expect(models).toHaveBeenCalledWith(expect.objectContaining({ ownerId: '146836' }), '197262');
});

it.each(['bot', 'owner', 'viewer', 'stage', 'session'] as const)('changing %s resets draft and cancels the previous input upload scope', async (change) => {
  const current = { ...bot, serviceMode: 'service' as const };
  sessions.mockResolvedValue({ ok: true, data: [{ sessionId: 'same-session', botId: current.id, title: 'Debug', messageCount: 0, gmtCreate: '', gmtModified: '' }] });
  const { rerender } = render(<DebugChatPanel bot={current} authenticatedUserId="viewer" runtimeStage="draft" />);
  await waitFor(() => expect(chat.mock.calls.at(-1)?.[1]?.sessionId).toBe('same-session'));
  const cancel = jest.mocked(useBotSessionFileUpload).mock.results.at(-1)!.value.cancelAll;
  (cancel as jest.Mock).mockClear();
  fireEvent.change(screen.getByLabelText('调试草稿'), { target: { value: '旧消息和引用' } });
  if (change === 'session') {
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    await waitFor(() => { if (chat.mock.calls.at(-1)?.[1]?.sessionId !== 's1') throw new Error('New debug session not selected'); });
  } else {
    rerender(<DebugChatPanel
      bot={{ ...current, id: change === 'bot' ? 'new-bot' : current.id, ownerId: change === 'owner' ? 'new-owner' : current.ownerId }}
      authenticatedUserId={change === 'viewer' ? 'new-viewer' : 'viewer'}
      runtimeStage={change === 'stage' ? 'verify' : 'draft'}
    />);
    await waitFor(() => { if (sessions.mock.calls.length !== 2) throw new Error('Session reload not finished'); });
  }
  expect(screen.getByLabelText('调试草稿')).toHaveValue('');
  expect(cancel).toHaveBeenCalledTimes(1);
});
