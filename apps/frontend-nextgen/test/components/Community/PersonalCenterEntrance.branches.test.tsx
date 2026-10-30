/** @jest-environment jsdom */
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const mockedCenter: UsePersonalCenterResult = {
  ownerUserId: 'id-149844',
  identity: null,
  activityType: 'AUTHORED',
  page: 1,
  pageSize: 10,
  activityPage: { items: [], total: 0 },
  loading: false,
  error: null,
  setActivityType: jest.fn(),
  goToPage: jest.fn(),
  reload: jest.fn(),
  unfollow: jest.fn(),
};

jest.mock('@/hooks/usePersonalCenter', () => ({
  usePersonalCenter: () => mockedCenter,
}));

it('shows identity fallbacks and supports returning from personal center', () => {
  const onBack = jest.fn();
  render(<CommunityPersonalCenter onBack={onBack} onOpenTopic={jest.fn()} />);
  expect(screen.queryByAltText('id-149844')).not.toBeInTheDocument();
  expect(screen.getByText('id-149844')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '返回' }));
  expect(onBack).toHaveBeenCalledTimes(1);
});
