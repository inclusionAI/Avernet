/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import { useChatEngineVersionNotice } from '@/pages/Workspace/Chat/hooks/useChatEngineVersionNotice';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import * as bots from '@/services/backendApi/bots/botController';
import * as spaces from '@/services/backendApi/admin/spaceController';

jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/admin/spaceController');
const bot: ChatBotView = { botId: 'bot:owner', realBotId: 'bot', ownerId: 'owner',
  displayName: 'Bot', online: true, chatable: true, engine: 'openclaw', actions: ['restart'] };
function Notice({ selected = bot }: { selected?: ChatBotView }) {
  const { notice, dialogs } = useChatEngineVersionNotice({ bot: selected, userId: 'caller', sessionId: 's1',
    origin: 'mine', section: selected.isTeamBot ? 'team' : 'managed' });
  return <>{notice}{dialogs}<input aria-label="chat" disabled={!selected.chatable} /></>;
}
beforeEach(() => { jest.clearAllMocks(); });
function expectNoSupplement() {
  expect(spaces.listSpaces).not.toHaveBeenCalled();
  expect(bots.listBotInventory).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: 'chat' })).toBeEnabled();
}
test.each([
  { ...bot, actions: undefined },
  { ...bot, actions: [] },
  { ...bot, actions: ['engine_restart'] },
  { ...bot, disabledActions: { restart: 'disabled' } },
  { ...bot, isTeamBot: true, engineOwnerId: 'owner', actions: [] },
  { ...bot, botType: 'desktop' },
  { ...bot, botType: 'service', runtimeStage: 'draft' as const },
  { ...bot, botType: 'service', runtimeStage: 'draft' as const, serviceLifecycle: 'running' as const },
])('missing permission or lifecycle evidence stays silent without supplemental requests %j', async (selected) => {
  render(<Notice selected={selected} />);
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'chat' })).toBeEnabled());
  expectNoSupplement();
  expect(bots.getBotEngineVersion).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '引擎有更新' })).not.toBeInTheDocument();
});
test('uses existing restart permission to inspect only the selected Bot; no spaces or inventory calls', async () => {
  jest.mocked(bots.getBotEngineVersion).mockResolvedValue({ code: 200000, data: {
    detection: { status: 'known' }, current: { raw_version: '1.0.0' },
    release_target: { status: 'available', release_id: 'REL-2' }, comparison: 'older', restart_hint: 'recommended',
    context: { bot_id: 'bot', stage: 'draft', checked_at: '2026-10-21T00:00:00Z' },
  } } as never);
  const view = render(<Notice />);
  expect(await screen.findByRole('button', { name: '引擎有更新' })).toBeInTheDocument();
  expect(bots.getBotEngineVersion).toHaveBeenCalledTimes(1);
  expect(bots.getBotEngineVersion).toHaveBeenCalledWith('bot', expect.objectContaining({ userId: 'caller', ownerId: 'owner' }));
  expectNoSupplement();
  view.rerender(<Notice selected={{ ...bot, botId: 'next:owner', realBotId: 'next' }} />);
  await waitFor(() => expect(bots.getBotEngineVersion).toHaveBeenCalledTimes(2));
  expect(jest.mocked(bots.getBotEngineVersion).mock.calls.map(([id]) => id)).toEqual(['bot', 'next']);
  expectNoSupplement();
});
