/** @jest-environment jsdom */
import { ParticipantBindingPanel } from '@/pages/Workspace/components/Modals/ParticipantBindingPanel';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const definitions: ParticipantDefinition[] = [
  { key: 'edit', displayName: '编辑', required: true },
  { key: 'write', displayName: '写作者', required: true },
  { key: 'review', displayName: '审核者', required: false },
];

it('renders roles as a horizontal selector with binding status and previous/next navigation', () => {
  const onActiveKeyChange = jest.fn();
  render(
    <ParticipantBindingPanel
      definitions={definitions}
      bindings={{ edit: 'bot-1' }}
      activeKey="write"
      onActiveKeyChange={onActiveKeyChange}
    />,
  );

  expect(screen.getByText('已绑定 1 / 3 个角色，共 1 个 Bot')).toBeInTheDocument();
  expect(screen.getByTestId('role-binding-strip')).toHaveClass('overflow-x-auto');
  expect(screen.getByRole('button', { name: '选择角色 编辑' })).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('button', { name: '选择角色 写作者' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('已绑定')).toHaveClass('text-success');
  expect(screen.getByText('需绑定')).toHaveClass('text-warning');

  fireEvent.click(screen.getByRole('button', { name: '上一个角色' }));
  expect(onActiveKeyChange).toHaveBeenCalledWith('edit');
  fireEvent.click(screen.getByRole('button', { name: '下一个角色' }));
  expect(onActiveKeyChange).toHaveBeenCalledWith('review');
});
