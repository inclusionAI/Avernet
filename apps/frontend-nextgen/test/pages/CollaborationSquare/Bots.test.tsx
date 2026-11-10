/** @jest-environment jsdom */
// 审批入口深链（?panel=friend-requests）：发现页只把查询参数翻译为好友申请面板的初始展开态，
// 面板自身的展开/收起仍由用户交互控制。
import CollaborationSquareBotsPage from '@/pages/CollaborationSquare/Bots';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const mockLocation = { pathname: '/collaboration-square/bots', search: '' };

jest.mock('@umijs/max', () => ({
  useLocation: () => mockLocation,
}));
jest.mock('@/components/CollaborationSquare/SquarePageShell', () => ({
  SquarePageShell: ({ friendRequestsDefaultOpen }: { friendRequestsDefaultOpen?: boolean }) => (
    <div data-testid="shell" data-friend-requests-default-open={String(Boolean(friendRequestsDefaultOpen))} />
  ),
}));

describe('公开 Bot 发现页好友申请深链', () => {
  beforeEach(() => {
    mockLocation.search = '';
  });

  it('带 panel=friend-requests 时好友申请面板默认展开', () => {
    mockLocation.search = '?panel=friend-requests';
    render(<CollaborationSquareBotsPage />);
    expect(screen.getByTestId('shell')).toHaveAttribute('data-friend-requests-default-open', 'true');
  });

  it('不带面板参数时保持默认收起', () => {
    render(<CollaborationSquareBotsPage />);
    expect(screen.getByTestId('shell')).toHaveAttribute('data-friend-requests-default-open', 'false');
  });
});