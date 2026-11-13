/** @jest-environment jsdom */
import { act, fireEvent, render, screen } from '@testing-library/react';
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
  await screen.findByText('群正文');
  expect(screen.getByRole('tab', { name: '群组级 1' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.click(screen.getByRole('tab', { name: '会话级 1' }));
  expect(screen.getByText('会话正文')).toBeTruthy();
  fireEvent.click(screen.getByRole('tab', { name: '轮次级 1' }));
  expect(screen.getByText('轮次正文')).toBeTruthy();
  expect(screen.queryByText('群正文')).toBeNull();
  expect(screen.getByText(/排查 Bot 更新于/)).toBeTruthy();
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

test('collapses long content and copies the full body while collapsed', async () => {
  const content = '第一行\n第二行\n第三行\n第四行\n第五行\n第六行';
  const height = jest.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(120);
  const computedStyle = window.getComputedStyle.bind(window);
  const style = jest.spyOn(window, 'getComputedStyle').mockImplementation((element) => {
    const result = computedStyle(element);
    if (element.tagName === 'P') result.lineHeight = '20px';
    return result;
  });
  const writeText = jest.fn().mockResolvedValue(undefined);
  const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  try {
    load.mockResolvedValue({ items: [item('group', 'group', content)] });
    render(<SharedInfoList view={{ groupId: 'group' }} names={names} />);
    const expand = await screen.findByRole('button', { name: '展开正文' });
    const body = screen.getByText(/第六行/);
    expect(body.classList.contains('line-clamp-5')).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '复制正文' })); });
    expect(writeText).toHaveBeenCalledWith(content);
    fireEvent.click(expand);
    expect(body.classList.contains('line-clamp-5')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '收起正文' }));
    expect(body.classList.contains('line-clamp-5')).toBe(true);
  } finally {
    height.mockRestore();
    style.mockRestore();
    if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
  }
});
