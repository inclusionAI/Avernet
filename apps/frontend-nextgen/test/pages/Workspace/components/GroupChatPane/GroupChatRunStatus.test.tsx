/** @jest-environment jsdom */
import { GroupChatRunStatus } from '@/pages/Workspace/components/GroupChatPane/GroupChatRunStatus';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const participants = [{ actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' }] as never;

describe('GroupChatRunStatus', () => {
  it('retains the stopped result without an abort action', () => {
    render(
      <GroupChatRunStatus
        message={{ id: 'm1', role: 'assistant', content: 'partial', status: 'aborted', extra: { botUuid: 'bot-a' } }}
        participants={participants}
      />,
    );
    expect(screen.getByText('已停止')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('does not append duplicate progress text beneath a streaming reply', () => {
    const { container } = render(
      <GroupChatRunStatus
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'working',
          status: 'streaming',
          extra: { botUuid: 'bot-a', runId: 'run-1' },
        }}
        participants={participants}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
