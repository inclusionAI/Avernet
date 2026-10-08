/** @jest-environment jsdom */
// 通知中心独立页壳（split-admin-space-ticket-pages）：页面体复用 AdminWorkOrdersView；
// ?category= 深链在挂载时写入 workOrderStore（旧 /admin?tab=work-orders&category= 的 category
// 参数从未被消费，本 change 让深链真正生效），非法/缺省值不动 store。
import { useWorkOrderStore } from '@/stores/workOrderStore';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';

const mockRouteState: { search: string } = { search: '' };
jest.mock('@umijs/max', () => ({
  useSearchParams: () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const React = require('react');
    const [params] = React.useState(() => new URLSearchParams(mockRouteState.search));
    return [params, jest.fn()];
  },
}));

jest.mock('@/pages/Admin/WorkOrders', () => ({
  AdminWorkOrdersView: () => <div data-testid="admin-work-orders" />,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const TicketCenterPage = require('@/pages/TicketCenter').default as React.FC;

function renderTicketCenter(search = '') {
  useWorkOrderStore.getState().reset();
  mockRouteState.search = search;
  render(<TicketCenterPage />);
}

describe('TicketCenter 页', () => {
  it('渲染通知中心视图（无单页 tab 壳）', () => {
    renderTicketCenter();
    expect(screen.getByTestId('admin-work-orders')).toBeInTheDocument();
  });

  it('?category=APPROVAL 深链：挂载即写入分类筛选', async () => {
    renderTicketCenter('category=APPROVAL');
    await waitFor(() => expect(useWorkOrderStore.getState().category).toBe('APPROVAL'));
  });

  it('非法/缺省 category 不覆写 store 默认（ALL）', () => {
    renderTicketCenter('category=INVALID');
    expect(useWorkOrderStore.getState().category).toBe('ALL');
  });
});
