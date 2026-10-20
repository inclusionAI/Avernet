/** @jest-environment jsdom */
import { GroupExecApprovalPanel } from '@/pages/Workspace/components/GroupChatPane/GroupExecApprovalPanel';
import type { GroupChatExecApprovalsModel } from '@/pages/Workspace/hooks/useGroupChatExecApprovals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

function model(): GroupChatExecApprovalsModel {
  return {
    connected: true,
    submittingKeys: [],
    resolve: jest.fn().mockResolvedValue(true),
    interactions: [
      {
        key: 'key',
        runId: 'run',
        botId: 'bot',
        interactionId: 'approval',
        toolName: 'bash',
        command: '/usr/bin/top -l 2',
        reason: '申请一次性更宽权限',
        options: [
          { decision: 'allow_once', label: 'Allow once' },
          { decision: 'deny', label: 'Deny' },
        ],
      },
    ],
  };
}

test('shows command and reason without approving automatically, and sends explicit choices', () => {
  const value = model();
  render(<GroupExecApprovalPanel model={value} participants={[]} />);
  expect(screen.getByText(value.interactions[0].command!)).toBeInTheDocument();
  expect(screen.getByText('申请一次性更宽权限')).toBeInTheDocument();
  expect(value.resolve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '允许一次' }));
  expect(value.resolve).toHaveBeenLastCalledWith('key', 'allow_once');
  fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
  expect(value.resolve).toHaveBeenLastCalledWith('key', 'deny');
});

test('disables actions offline or submitting; removes terminal approval', () => {
  const value = model();
  const view = render(<GroupExecApprovalPanel model={{ ...value, connected: false }} participants={[]} />);
  expect(screen.getByRole('button', { name: '允许一次' })).toBeDisabled();
  view.rerender(<GroupExecApprovalPanel model={{ ...value, submittingKeys: ['key'] }} participants={[]} />);
  expect(screen.getByRole('button', { name: '拒绝' })).toBeDisabled();
  view.rerender(<GroupExecApprovalPanel model={{ ...value, interactions: [] }} participants={[]} />);
  expect(screen.queryByRole('region', { name: '待处理执行审批' })).not.toBeInTheDocument();
});
