/** @jest-environment jsdom */
import { render, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import path from 'path';
import { SidePanel } from '@tc-chat/ui/es/SidePanel';

// Keep the actual SDK tab chrome; only replace the remote business content.
jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({
  SidePanelContent: () => <div data-task-panel-root="true" />,
}));

const css = readFileSync(path.join(__dirname, '../../../../src/services/tasks/panel/taskPanelShell.css'), 'utf8');
const hiddenSelector = css.replace(/\/\*[\s\S]*?\*\//g, '').split('{')[0].trim();
const taskIds = ['task-new', 'task-old'];
function isTabBarHidden(count: number) {
  const { container } = render(
    <div className="workspace-sdk-panel">
      <SidePanel tabs={taskIds.slice(0, count).map(id => ({ id, type: 'taskPanel.TaskLoopView', title: id }))}
        activeTabId="task-new" onTabChange={() => {}} onTabClose={() => {}} />
    </div>,
  );
  const topBar = container.querySelector('[class*="TopBarRoot"]');
  expect(topBar).not.toBeNull();
  return topBar!.matches(hiddenSelector);
}
afterEach(cleanup);
it('keeps the SDK tab bar accessible when multiple task tabs are restored', () => {
  expect(isTabBarHidden(2)).toBe(false);
});
it('preserves the compact task header for a single task tab', () => {
  expect(isTabBarHidden(1)).toBe(true);
});
