/** @jest-environment jsdom */
// 协作群成员名 Hook 的成功、空态、失败与取消分支。
import { useGroupMemberNames } from '@/assets/TaskPanel/hooks/useGroupMemberNames';
import { fetchGroupDetail } from '@/assets/TaskPanel/GroupDrillDown';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/jest-globals';
import { describe, expect, it } from '@jest/globals';

jest.mock('@/assets/TaskPanel/GroupDrillDown', () => ({
  fetchGroupDetail: jest.fn(),
  sortGroupMembers: (input: unknown[]) => [...input],
}));

const fetchMock = fetchGroupDetail as jest.Mock;
const participants = [
  { actor_id: 'bot-1', actor_kind: 'bot', name: 'Bot A', role: 'manager', mode: 'auto' },
  { actor_id: 'bot-1', actor_kind: 'bot', name: 'Bot A', role: 'worker', mode: 'auto' },
  { actor_id: 'bot-2', actor_kind: 'bot', name: 'Bot B', role: 'unknown', mode: 'auto' },
  { actor_id: 'bot-3', actor_kind: 'bot', name: '', role: 'worker', mode: 'auto' },
  { actor_id: '', actor_kind: 'human', name: 'Human', role: 'worker', mode: 'auto' },
];

const Probe = ({ ids }: { ids: string[] }) => {
  const groups = useGroupMemberNames(ids);
  return <div data-testid="result">{JSON.stringify(groups)}</div>;
};

describe('useGroupMemberNames', () => {
  it('无群 ID 时立即返回空对象', () => {
    render(<Probe ids={[]} />);
    expect(screen.getByTestId('result')).toHaveTextContent('{}');
  });

  it('成功时去重成员名并按 actor id 建立映射', async () => {
    fetchMock.mockResolvedValueOnce({ participants });
    render(<Probe ids={['g-1']} />);

    await waitFor(() => {
      expect(screen.getByTestId('result')).toHaveTextContent('Bot A');
    });
    expect(screen.getByTestId('result')).toHaveTextContent('Bot B');
    expect(screen.getByTestId('result')).toHaveTextContent('bot-2');
  });

  it('空成员、无效成员与请求失败都降级为空对象', async () => {
    fetchMock.mockResolvedValueOnce(null);
    const { rerender } = render(<Probe ids={['g-empty']} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('g-empty', ''));
    expect(screen.getByTestId('result')).toHaveTextContent('{}');

    fetchMock.mockResolvedValueOnce({ participants: [] });
    rerender(<Probe ids={['g-invalid']} />);
    await waitFor(() => expect(screen.getByTestId('result')).toHaveTextContent('{}'));

    fetchMock.mockRejectedValueOnce(new Error('network'));
    rerender(<Probe ids={['g-error']} />);
    await waitFor(() => expect(screen.getByTestId('result')).toHaveTextContent('{}'));
  });

  it('切换群列表后旧请求结果不会写入新状态', async () => {
    fetchMock.mockReturnValueOnce({ participants: [] });
    const { rerender } = render(<Probe ids={['g-old']} />);

    fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolve({ participants });
      }),
    );
    rerender(<Probe ids={['g-new']} />);
    await waitFor(() => expect(screen.getByTestId('result')).toHaveTextContent('Bot A'));
  });
});
