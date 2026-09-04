/** @jest-environment jsdom */
// 管理后台单页 Tab 可见性 + 默认/深链回退（getAdminSections capability）。
// Open Core（阿里云部署）默认 spaces=false：仅渲染【工单中心】Tab，默认 Tab=work-orders，
// ?tab=spaces 深链回落 work-orders；internal override spaces=true：两 Tab 均在，默认 spaces。
import { extendCapabilities } from '@/capabilities';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

// useSearchParams 桩：用 React state 承载可变 URLSearchParams，setSearchParams 触发重渲染。
// mockRouteState.search 控制每个用例的初始 query（`mock` 前缀满足 jest.mock 闭包引用要求）。
const mockRouteState: { search: string } = { search: '' };
jest.mock('@umijs/max', () => ({
  useSearchParams: () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const React = require('react');
    const [params, setParams] = React.useState(() => new URLSearchParams(mockRouteState.search));
    const setSearchParams = (next: Record<string, string>) => {
      const p = new URLSearchParams();
      Object.entries(next).forEach(([k, v]) => p.set(k, String(v)));
      setParams(p);
    };
    return [params, setSearchParams];
  },
}));

jest.mock('@/components/Admin/Tabs', () => ({
  UnderlineTabs: (props: {
    value: string;
    options: { value: string; label: string }[];
    onChange: (v: string) => void;
  }) => (
    <div data-testid="admin-tabs">
      {props.options.map((o) => (
        <button key={o.value} type="button" data-testid={`tab-${o.value}`} onClick={() => props.onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  ),
}));
jest.mock('@/pages/Admin/Spaces', () => ({
  AdminSpacesView: () => <div data-testid="admin-spaces" />,
}));
jest.mock('@/pages/Admin/WorkOrders', () => ({
  AdminWorkOrdersView: () => <div data-testid="admin-work-orders" />,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const AdminPage = require('@/pages/Admin').default as React.FC;

function renderAdmin(search = '') {
  mockRouteState.search = search;
  render(<AdminPage />);
}

describe('Admin 页 Tab 可见性（Open Core 默认 getAdminSections={spaces:false,workOrders:true}）', () => {
  it('仅渲染【工单中心】Tab，不渲染【空间管理】', () => {
    renderAdmin();
    expect(screen.getByTestId('tab-work-orders')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-spaces')).not.toBeInTheDocument();
  });

  it('默认 Tab=work-orders：渲染工单中心视图，不渲染空间管理视图', () => {
    renderAdmin();
    expect(screen.getByTestId('admin-work-orders')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-spaces')).not.toBeInTheDocument();
  });

  it('深链 ?tab=spaces 回落 work-orders（不展示隐藏 Tab 也不渲染其视图）', () => {
    renderAdmin('tab=spaces');
    expect(screen.queryByTestId('tab-spaces')).not.toBeInTheDocument();
    expect(screen.getByTestId('admin-work-orders')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-spaces')).not.toBeInTheDocument();
  });
});

// extendCapabilities 合并后无法恢复，internal override 用例置于文件末尾（沿用 navigation/AppHeader 约定）。
describe('Admin 页 Tab 可见性（internal overlay getAdminSections={spaces:true,workOrders:true}）', () => {
  it('两个 Tab 均在，默认 Tab=spaces：渲染空间管理视图', () => {
    extendCapabilities({
      getAdminSections: () => ({ status: 'available', value: { spaces: true, workOrders: true } }),
    });
    renderAdmin();
    expect(screen.getByTestId('tab-spaces')).toBeInTheDocument();
    expect(screen.getByTestId('tab-work-orders')).toBeInTheDocument();
    expect(screen.getByTestId('admin-spaces')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-work-orders')).not.toBeInTheDocument();
  });

  it('深链 ?tab=work-orders 命中可见 Tab：渲染工单中心视图', () => {
    // 上一用例已 extendCapabilities（spaces=true），沿用 internal 语义。
    renderAdmin('tab=work-orders');
    expect(screen.getByTestId('admin-work-orders')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-spaces')).not.toBeInTheDocument();
  });
});
