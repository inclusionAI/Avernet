/** @jest-environment jsdom */
// Load Jest before the Umi-dependent module graph.
import { expect, it, jest } from '@jest/globals';

import RecoveryWorkspace from '@/pages/EvolutionFactory/Recovery';
import { EvolutionModeSwitch, EvolutionTabs } from '@/pages/EvolutionFactory/shared';
import { recoveryApi } from '@/services/backendApi/clawinsight/recoveryController';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@/capabilities', () => ({
  ...jest.requireActual<typeof import('@/capabilities')>('@/capabilities'),
  getCapabilities: () => ({
    ...jest.requireActual<typeof import('@/capabilities')>('@/capabilities').getCapabilities(),
    getSessionRecoveryEnabled: () => ({ value: true }),
  }),
}));
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useLocation: () => ({ pathname: '/evolution-factory', search: '?tab=recovery' }),
}));
jest.mock('@/services/backendApi/clawinsight/recoveryController', () => ({
  recoveryApi: { list: jest.fn(), detail: jest.fn(), bots: jest.fn() },
}));

it('opens recovery under intelligent evolution and does not load settings or task data before space readiness', () => {
  render(
    <>
      <EvolutionModeSwitch tab="recovery" />
      <EvolutionTabs tab="recovery" />
      <RecoveryWorkspace loading={false} ready={false} />
    </>,
  );
  const tab = screen.getByRole('button', { name: '自愈' });
  expect(tab).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: '自愈' })).toHaveTextContent('请先选择可访问的空间');
  expect(screen.queryByRole('button', { name: '自愈设置' })).not.toBeInTheDocument();
  expect(recoveryApi.list).not.toHaveBeenCalled();
  expect(recoveryApi.bots).not.toHaveBeenCalled();
  fireEvent.click(tab);
  expect(history.push).toHaveBeenCalledWith('/evolution-factory?tab=recovery');
});

it('loads the list at the recovery entry and defers bot settings reads until opened', async () => {
  const mock = jest.mocked(recoveryApi);
  mock.list.mockResolvedValue({ items: [], page: 1, pageSize: 20, total: 0 });
  render(
    <>
      <EvolutionModeSwitch tab="recovery" />
      <RecoveryWorkspace spaceId={42} loading={false} ready />
    </>,
  );
  expect(await screen.findByText('暂无自愈任务')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '自愈设置' })).toBeInTheDocument();
  expect(recoveryApi.bots).not.toHaveBeenCalled();
});

it('keeps the three Bot evolution tabs together and underlined in TeamClaw', () => {
  render(<><EvolutionModeSwitch tab="templates" /><EvolutionTabs tab="templates" /></>);
  expect(screen.getByRole('button', { name: 'Bot进化' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('tablist', { name: 'Bot进化入口' })).toHaveClass('border-b');
  expect(screen.getByRole('tab', { name: '评测模板' })).toHaveClass('border-primary');
  expect(screen.getAllByRole('tab')).toHaveLength(3);
});
