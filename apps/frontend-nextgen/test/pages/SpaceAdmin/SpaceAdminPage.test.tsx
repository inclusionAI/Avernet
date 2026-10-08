/** @jest-environment jsdom */
// 空间管理独立页壳（split-admin-space-ticket-pages）：页面体复用 AdminSpacesView；
// Open Core（getAdminSections.spaces=false）直达 /space-admin 时 replace 回落 /ticket-center
// （与原 /admin 单页「隐藏 Tab 深链回落」语义同构）。
import { extendCapabilities } from '@/capabilities';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';

const mockReplace = jest.fn();
jest.mock('@umijs/max', () => ({
  history: { replace: mockReplace, push: jest.fn() },
}));

jest.mock('@/pages/Admin/Spaces', () => ({
  AdminSpacesView: () => <div data-testid="admin-spaces" />,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const SpaceAdminPage = require('@/pages/SpaceAdmin').default as React.FC;

describe('SpaceAdmin 页（Open Core 默认 getAdminSections={spaces:false}）', () => {
  it('Open Core 直达 /space-admin：replace 回落 /ticket-center，不渲染空间管理视图', async () => {
    render(<SpaceAdminPage />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/ticket-center'));
    expect(screen.queryByTestId('admin-spaces')).not.toBeInTheDocument();
  });
});

// extendCapabilities 合并后无法恢复，internal override 用例置于文件末尾（navigation/AppSidebar 约定）。
describe('SpaceAdmin 页（internal overlay getAdminSections={spaces:true}）', () => {
  it('internal 直达：渲染空间管理视图，不触发回落', async () => {
    extendCapabilities({
      getAdminSections: () => ({ status: 'available', value: { spaces: true, workOrders: true } }),
    });
    mockReplace.mockClear(); // 清掉上一用例（Open Core 回落）的调用计数
    render(<SpaceAdminPage />);
    await waitFor(() => expect(screen.getByTestId('admin-spaces')).toBeInTheDocument());
    expect(mockReplace).not.toHaveBeenCalledWith('/ticket-center');
  });
});
