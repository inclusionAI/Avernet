/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GroupIdentityDisplay } from '@/components/CollaborationSquare/PublicGroupSquareSection/GroupIdentityDisplay';

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: { userId: 'user-1', displayName: '测试用户' }, status: 'ready' }),
}));

test('公开群固定显示用户、浅灰背景且不提供身份切换，悬停展示原因', async () => {
  const user = userEvent.setup();
  render(<GroupIdentityDisplay />);
  const identity = screen.getByRole('group', { name: '当前协作身份：测试用户' });
  expect(identity).toHaveClass('bg-muted', 'h-9');
  expect(identity).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByText('用户')).toHaveClass('text-primary');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  await user.click(identity);
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  await user.unhover(identity);
  await user.hover(identity);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('公开协作群不支持切换bot身份');
});
