/** @jest-environment jsdom */
import { GroupChatRunStatus } from '@/pages/Workspace/components/GroupChatPane/GroupChatRunStatus';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const participants = [{ actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' }] as never;

describe('GroupChatRunStatus', () => {
  it('shows streaming progress without a moving abort action', () => {
    render(
      <GroupChatRunStatus
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'working',
          status: 'streaming',
          createdAt: 1_000,
          extra: { botUuid: 'bot-a', runId: 'run-1' },
        }}
        participants={participants}
        now={4_000_000}
      />,
    );

    expect(screen.getByText('输出中')).toBeInTheDocument();
    expect(screen.getByText('总耗时 3000s')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows terminal state without an abort action', () => {
    render(
      <GroupChatRunStatus
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'partial',
          status: 'aborted',
          extra: { botUuid: 'bot-a' },
        }}
        participants={participants}
        now={0}
      />,
    );
    expect(screen.getByText('已终止')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows streaming status before session participants finish hydrating', () => {
    render(
      <GroupChatRunStatus
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'working',
          status: 'streaming',
          extra: { botUuid: 'bot-a', runId: 'run-1' },
        }}
        participants={[]}
        now={0}
      />,
    );
    expect(screen.getByText('输出中')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
