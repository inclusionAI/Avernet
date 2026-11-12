/** @jest-environment jsdom */
import { act, render, screen } from '@testing-library/react';
import { SharedInfoList } from '@/pages/Workspace/components/ManagePanel/SharedInfoList';
import { listSharedInfo } from '@/services/backendApi/collaboration/groupContextController';
import type { SharedInfoItem } from '@/services/backendApi/collaboration/groupContextController';

jest.mock('@/services/backendApi/collaboration/groupContextController', () => ({
  listSharedInfo: jest.fn(),
}));

const load = jest.mocked(listSharedInfo);
const view = { groupId: 'group', sessionId: 'group:session' };
const names = new Map([['bot-1', '排查 Bot']]);

function item(scope_level: SharedInfoItem['scope_level'], scope: string, content: string): SharedInfoItem {
  return {
    name: '排查结论',
    scope_level,
    scope,
    content,
    creator: 'bot-1',
    gmt_modified: '2026-11-12T01:00:00Z',
  };
}

afterEach(() => jest.resetAllMocks());

test('shows scope groups, content and the updater for each readable item', async () => {
  load.mockResolvedValue({ items: [
    item('group', 'group', '群正文'),
    item('session', 'group:session', '会话正文'),
    item('round', 'group:session:2', '轮次正文'),
  ] });
  render(<SharedInfoList view={view} names={names} />);
  await screen.findByText('轮次正文');
  expect(screen.getByText('群级')).toBeTruthy();
  expect(screen.getByText('会话级')).toBeTruthy();
  expect(screen.getByText('轮次级')).toBeTruthy();
  expect(screen.getAllByText(/排查 Bot 更新于/)).toHaveLength(3);
});

test('discards an older identity response when switching to a Bot', async () => {
  let completeHuman: ((value: { items: SharedInfoItem[] }) => void) | undefined;
  load.mockReturnValueOnce(new Promise((resolve) => { completeHuman = resolve; }));
  load.mockResolvedValueOnce({ items: [item('group', 'group', 'Bot 可见正文')] });

  const { rerender } = render(<SharedInfoList view={view} viewerId="human_123" names={names} />);
  rerender(<SharedInfoList view={{ ...view, view_bot_id: 'bot-1' }} viewerId="bot-1" names={names} />);

  await screen.findByText('Bot 可见正文');
  expect(load.mock.calls[0][1].aborted).toBe(true);
  await act(async () => { completeHuman?.({ items: [item('group', 'group', '仅 Human 可见正文')] }); });
  expect(screen.queryByText('仅 Human 可见正文')).toBeNull();
});
