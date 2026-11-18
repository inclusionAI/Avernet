/** @jest-environment jsdom */
import { WorkspaceWorkflowPicker } from '@/pages/TaskEscort/parity/WorkspaceWorkflowPicker';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
const workflows = [
  { id: 'Alpha-ID', title: 'First workflow', runCount: null, lastStatus: null, ownerId: null },
  { id: 'beta-id', title: 'Second workflow', runCount: 2, lastStatus: 'failed', ownerId: 'owner' },
];
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.scrollIntoView ??= () => undefined;

it('searches actual title/ID case-insensitively, highlights the selection, closes and resets after selection', async () => {
  const user = userEvent.setup();
  const select = jest.fn();
  render(<WorkspaceWorkflowPicker workflows={workflows} selected={workflows[0]} onSelect={select} />);
  await user.click(screen.getByRole('combobox', { name: /切换工作流/ }));
  const dialog = screen.getByRole('dialog', { name: '选择工作流' });
  expect(within(dialog).getByRole('button', { name: /First workflow/ })).toHaveAttribute('aria-pressed', 'true');
  await user.type(screen.getByRole('textbox', { name: '搜索工作流' }), 'BETA-ID');
  expect(within(dialog).queryByRole('button', { name: /First workflow/ })).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole('button', { name: /Second workflow/ }));
  expect(select).toHaveBeenCalledWith('beta-id');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await user.click(screen.getByRole('combobox', { name: /切换工作流/ }));
  expect(screen.getByRole('textbox', { name: '搜索工作流' })).toHaveValue('');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索工作流' }), { target: { value: 'fIRst' } });
  expect(screen.getByRole('button', { name: /First workflow/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Second workflow/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索工作流' }), { target: { value: 'missing' } });
  expect(screen.getByText('无匹配工作流')).toBeInTheDocument();
});
