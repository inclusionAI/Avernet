/** @jest-environment jsdom */
import { ConversationSessionRow } from '@/pages/Workspace/Chat/components/ConversationSessionRow';
import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const session = { sessionId: 's1', botId: 'b', title: '原会话', messageCount: 10, gmtCreate: '', gmtModified: '' };
const renderRow = (
  run = jest.fn<Promise<boolean>, [ConversationSessionAction]>().mockResolvedValue(true),
  readOnly = false,
) => {
  const onSelect = jest.fn();
  const view = render(
    <ConversationSessionRow
      session={session}
      selected={false}
      readOnly={readOnly}
      onSelect={onSelect}
      actions={{ run, pending: false }}
    />,
  );
  return { ...view, onSelect, run };
};
async function openAction(name: string) {
  await userEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
  await userEvent.click(await screen.findByRole('button', { name }));
}
it('offers all three actions even without favorites, and clicking menu does not select the row', async () => {
  const { onSelect } = renderRow();
  const more = screen.getByRole('button', { name: '会话更多操作' });
  expect(more.closest('[data-session-meta-actions]')).toHaveClass(
    'group-hover/row:opacity-100',
    'group-focus-within/row:opacity-100',
  );
  await userEvent.click(more);
  expect(await screen.findByRole('button', { name: '编辑标题' })).toBeVisible();
  expect(screen.getByRole('button', { name: '清除上下文' })).toBeVisible();
  expect(screen.getByRole('button', { name: '删除会话' })).toBeVisible();
  expect(onSelect).not.toHaveBeenCalled();
});
it('read-only session has no menu even if callbacks were provided', () => {
  const { run } = renderRow(undefined, true);
  expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
  expect(run).not.toHaveBeenCalled();
});
it('prefills the current title, trims input and saves without changing selection', async () => {
  const { run, onSelect } = renderRow();
  await openAction('编辑标题');
  const dialog = await screen.findByRole('dialog', { name: '编辑会话标题' });
  const input = within(dialog).getByRole('textbox', { name: '会话标题' });
  expect(input).toHaveValue('原会话');
  fireEvent.change(input, { target: { value: ' 新标题 ' } });
  await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(run).toHaveBeenCalledWith({ type: 'rename', title: '新标题' });
  expect(onSelect).not.toHaveBeenCalled();
});
it('rejects blank titles, and unchanged title closes without requesting', async () => {
  const { run } = renderRow();
  await openAction('编辑标题');
  const input = await screen.findByRole('textbox', { name: '会话标题' });
  fireEvent.change(input, { target: { value: '   ' } });
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  fireEvent.change(input, { target: { value: ' 原会话 ' } });
  await userEvent.click(screen.getByRole('button', { name: '保存' }));
  expect(run).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
it.each([
  ['清除上下文', '确认清除', 'clear'],
  ['删除会话', '确认删除', 'delete'],
] as const)('%s requires confirmation, cancellation sends nothing', async (name, confirm, type) => {
  const { run, onSelect } = renderRow();
  await openAction(name);
  const dialog = await screen.findByRole('alertdialog', { name });
  expect(within(dialog).getByText(/无法恢复/)).toBeVisible();
  expect(run).not.toHaveBeenCalled();
  await userEvent.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(run).not.toHaveBeenCalled();
  await openAction(name);
  await userEvent.click(await screen.findByRole('button', { name: confirm }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  expect(run).toHaveBeenCalledWith({ type });
  expect(onSelect).not.toHaveBeenCalled();
});
it.each([
  ['编辑标题', '保存', 'dialog'],
  ['清除上下文', '确认清除', 'alertdialog'],
  ['删除会话', '确认删除', 'alertdialog'],
] as const)('failed %s remains open for retry', async (name, confirm, role) => {
  const run = jest
    .fn<Promise<boolean>, [ConversationSessionAction]>()
    .mockResolvedValueOnce(false)
    .mockResolvedValue(true);
  renderRow(run);
  await openAction(name);
  if (name === '编辑标题')
    fireEvent.change(await screen.findByRole('textbox', { name: '会话标题' }), { target: { value: 'new' } });
  await userEvent.click(await screen.findByRole('button', { name: confirm }));
  expect(await screen.findByRole(role)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: confirm }));
  await waitFor(() => expect(screen.queryByRole(role)).not.toBeInTheDocument());
  expect(run).toHaveBeenCalledTimes(2);
});
it('confirmation is locked while pending, preventing duplicate submission and cancellation', async () => {
  let resolve!: (ok: boolean) => void;
  const run = jest.fn<Promise<boolean>, [ConversationSessionAction]>(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  renderRow(run);
  await openAction('删除会话');
  await userEvent.click(await screen.findByRole('button', { name: '确认删除' }));
  expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  await userEvent.keyboard('{Escape}');
  expect(screen.getByRole('alertdialog')).toBeVisible();
  await act(async () => resolve(true));
  expect(run).toHaveBeenCalledTimes(1);
});
it('more action remains keyboard-accessible and is not nested inside session selection', async () => {
  renderRow();
  const more = screen.getByRole('button', { name: '会话更多操作' });
  expect(more.parentElement?.closest('button')).toBeNull();
  more.focus();
  await userEvent.keyboard('{Enter}');
  expect(await screen.findByRole('button', { name: '编辑标题' })).toBeVisible();
});

it('opening another session menu dismisses the previous menu and targets only the new session', async () => {
  const runA = jest.fn<Promise<boolean>, [ConversationSessionAction]>().mockResolvedValue(true);
  const runB = jest.fn<Promise<boolean>, [ConversationSessionAction]>().mockResolvedValue(true);
  const onSelect = jest.fn();
  render(
    <>
      <ConversationSessionRow
        session={session}
        selected={false}
        onSelect={onSelect}
        actions={{ run: runA, pending: false }}
      />
      <ConversationSessionRow
        session={{ ...session, sessionId: 's2', botId: 'another-bot', title: '另一个会话' }}
        selected={false}
        onSelect={onSelect}
        actions={{ run: runB, pending: false }}
      />
    </>,
  );
  const [first, second] = screen.getAllByRole('button', { name: '会话更多操作' });
  await userEvent.click(first);
  expect(first).toHaveAttribute('aria-expanded', 'true');
  await userEvent.click(second);
  await waitFor(() => expect(first).toHaveAttribute('aria-expanded', 'false'));
  expect(second).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  await userEvent.click(screen.getByRole('button', { name: '编辑标题' }));
  expect(await screen.findByRole('textbox', { name: '会话标题' })).toHaveValue('另一个会话');
  fireEvent.change(screen.getByRole('textbox', { name: '会话标题' }), { target: { value: '修改第二个' } });
  await userEvent.click(screen.getByRole('button', { name: '保存' }));
  expect(runB).toHaveBeenCalledWith({ type: 'rename', title: '修改第二个' });
  expect(runA).not.toHaveBeenCalled();
  expect(onSelect).not.toHaveBeenCalled();
});

it('switches session menus by keyboard and closes on repeat click, Escape or outside click', async () => {
  const actions = { run: jest.fn(async () => true), pending: false };
  const onSelect = jest.fn();
  render(
    <>
      <ConversationSessionRow session={session} selected={false} onSelect={onSelect} actions={actions} />
      <ConversationSessionRow
        session={{ ...session, sessionId: 's2', title: '另一个会话' }}
        selected={false}
        onSelect={onSelect}
        actions={actions}
      />
      <button type="button">菜单外按钮</button>
    </>,
  );
  const [first, second] = screen.getAllByRole('button', { name: '会话更多操作' });
  await userEvent.click(first);
  act(() => second.focus());
  await userEvent.keyboard('{Enter}');
  await waitFor(() => expect(first).toHaveAttribute('aria-expanded', 'false'));
  expect(second).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  await userEvent.keyboard('{Escape}');
  await waitFor(() => expect(second).toHaveAttribute('aria-expanded', 'false'));
  await userEvent.click(first);
  await userEvent.click(first);
  await waitFor(() => expect(first).toHaveAttribute('aria-expanded', 'false'));
  await userEvent.click(second);
  await userEvent.click(screen.getByRole('button', { name: '菜单外按钮' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(onSelect).not.toHaveBeenCalled();
  expect(actions.run).not.toHaveBeenCalled();
});
