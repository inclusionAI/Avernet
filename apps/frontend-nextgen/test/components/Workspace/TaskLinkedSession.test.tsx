/** @jest-environment jsdom */
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

const onClose = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  (global as unknown as { fetch: unknown }).fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({ code: 0, message: 'ok', data: null, request_id: 'r' }),
    }),
  );
});

describe('TaskLinkedSession(任务节点会话联动视图)', () => {
  it('群目标展示群会话并拉取指定 collaboration session', async () => {
    render(
      <TaskLinkedSession
        target={{
          sessionId: 'bcs_grp_902:round:2',
          kind: 'group',
          groupId: 'g-902',
          botId: null,
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    expect(screen.getByText('任务联动 · 群会话')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('群成员（0）')).toBeInTheDocument());

    const urls = ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).map((call) =>
      String(call[0]),
    );
    expect(urls).toContain('/openapi/v1/collaboration/groups/g-902');
    expect(
      urls.some((url) => url.includes('/openapi/v1/collaboration/sessions/bcs_grp_902%3Around%3A2/messages?')),
    ).toBe(true);
  });

  it('Bot 目标展示单聊并拉取指定 bot session', async () => {
    render(
      <TaskLinkedSession
        target={{
          sessionId: 'agent:main:session:s-1:user:u-1',
          kind: 'bot',
          groupId: null,
          botId: 'bot-9:u-1',
        }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    expect(screen.getByText('任务联动 · Bot 会话')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('执行者（1）')).toBeInTheDocument());

    const urls = ((global as unknown as { fetch: jest.Mock }).fetch.mock.calls as unknown[][]).map((call) =>
      String(call[0]),
    );
    expect(
      urls.some(
        (url) =>
          url.includes('/openapi/v1/bots/bot-9/sessions/agent%3Amain%3Asession%3As-1%3Auser%3Au-1/messages?') &&
          url.includes('user_id=u-1') &&
          url.includes('owner_id=u-1'),
      ),
    ).toBe(true);
  });

  it('返回当前会话按钮调用 onClose', () => {
    render(
      <TaskLinkedSession
        target={{ sessionId: 'bcs_grp_902:round:2', kind: 'group', groupId: 'g-902' }}
        userId="u-1"
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '返回当前会话' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
