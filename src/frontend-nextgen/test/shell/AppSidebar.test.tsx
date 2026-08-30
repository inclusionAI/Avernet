/** @jest-environment jsdom */
import { AppSidebar } from '@/shell/AppSidebar';
import type { NavigationArea } from '@/shell/navigation';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/shell/SpaceSwitcher', () => ({
  SpaceSwitcher: () => <div data-testid="space-switcher">选择空间</div>,
}));

const renderSidebar = (area: NavigationArea) =>
  render(
    <AppSidebar
      area={area}
      activePath="/workspace"
      collapsed={false}
      items={[]}
      onNavigate={jest.fn()}
      onExpand={jest.fn()}
    />,
  );

it('工作区域不展示空间切换器', () => {
  renderSidebar('work');
  expect(screen.queryByTestId('space-switcher')).not.toBeInTheDocument();
});

it('管理区域展示空间切换器', () => {
  renderSidebar('manage');
  expect(screen.getByTestId('space-switcher')).toBeInTheDocument();
});
