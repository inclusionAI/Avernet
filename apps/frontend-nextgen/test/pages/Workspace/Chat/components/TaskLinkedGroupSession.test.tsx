/** @jest-environment jsdom */
// 对话页主区的跨域联动群会话视图:点任务步骤群节点后原位展示群会话内容(成员+消息流),
// 关闭返回当前单聊;页面不跳转(PRD 左右联动)。
import { TaskLinkedGroupSession } from '@/pages/Workspace/Chat/components/TaskLinkedGroupSession';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

const onClose = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  // 群成员/消息接口:空信封(成员兜底合成 + 空消息),视图片区正常渲染
  (global as unknown as { fetch: unknown }).fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({ code: 0, message: 'ok', data: null, request_id: 'r' }),
    }),
  );
});

describe('TaskLinkedGroupSession(对话页主区联动群会话视图)', () => {
  it('渲染覆盖层:标题带群会话标识 + 群成员区(GroupSessionView 承载)', async () => {
    render(
      <TaskLinkedGroupSession
        target={{ groupId: 'g-902', sessionId: 'bcs_grp_902:round:2' }}
        userId="u-1"
        onClose={onClose}
      />,
    );
    expect(screen.getByText(/任务联动 · 群会话/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/群成员/)).toBeInTheDocument());
  });

  it('关闭按钮 → onClose 返回当前单聊', async () => {
    render(
      <TaskLinkedGroupSession
        target={{ groupId: 'g-902', sessionId: 'bcs_grp_902:round:2' }}
        userId="u-1"
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '返回当前会话' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('拉取指定群会话的消息(fetch URL 含群会话端点与 sessionId 形态)', async () => {
    render(
      <TaskLinkedGroupSession
        target={{ groupId: 'g-902', sessionId: 'bcs_grp_902:round:2' }}
        userId="u-1"
        onClose={onClose}
      />,
    );
    const fetchMock = (global as unknown as { fetch: jest.Mock }).fetch;
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]));
    const _typedUrls: string[] = urls;
    void _typedUrls;
    expect(
      (urls as string[]).some((u: string) => u.includes('bcs_grp_902') || u.includes('/groups/') || u.includes('/bots/')),
    ).toBe(true);
  });
});