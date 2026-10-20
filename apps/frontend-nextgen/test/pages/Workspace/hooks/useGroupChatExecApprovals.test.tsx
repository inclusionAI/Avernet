/** @jest-environment jsdom */
import {
  useGroupChatExecApprovals,
  type GroupExecApprovalsProvider,
} from '@/pages/Workspace/hooks/useGroupChatExecApprovals';
import type { GroupExecApprovalSnapshot } from '@/services/workspace/groupExecApprovalService';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';
jest.mock('sonner');

function fake() {
  let snapshot: (value: GroupExecApprovalSnapshot) => void = () => {};
  const provider: GroupExecApprovalsProvider = {
    isConnected: true,
    subscribeToExecApprovals: (listener) => {
      snapshot = listener;
      return () => {};
    },
    subscribeToConnectionStatus: () => () => {},
    resolveExecApproval: jest.fn().mockResolvedValue(undefined),
  };
  return { provider, publish: (value: GroupExecApprovalSnapshot) => snapshot(value) };
}
const approval = {
  key: 'key',
  runId: 'run',
  interactionId: 'i',
  botId: 'bot',
  toolName: 'bash',
  options: [{ decision: 'deny' as const, label: 'Deny' }],
};

test('provider replacement isolates old events and late errors', async () => {
  const first = fake(),
    second = fake();
  let fail!: (error: Error) => void;
  (first.provider.resolveExecApproval as jest.Mock).mockImplementation(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  const hook = renderHook(({ provider }) => useGroupChatExecApprovals(provider), {
    initialProps: { provider: first.provider },
  });
  act(() => first.publish({ reason: 'update', interactions: [approval] }));
  expect(hook.result.current.interactions).toHaveLength(1);
  let submit!: Promise<boolean>;
  act(() => {
    submit = hook.result.current.resolve('key', 'deny');
  });
  expect(hook.result.current.submittingKeys).toEqual(['key']);
  hook.rerender({ provider: second.provider });
  expect(hook.result.current.interactions).toEqual([]);
  act(() => first.publish({ reason: 'update', interactions: [approval] }));
  await act(async () => {
    fail(new Error('late'));
    await submit;
  });
  expect(hook.result.current.interactions).toEqual([]);
  expect(toast.error).not.toHaveBeenCalled();
});
