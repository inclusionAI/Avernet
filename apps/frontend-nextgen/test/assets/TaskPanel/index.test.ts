/** @jest-environment node */
// TaskPanel 本地注册入口会注册主组件名和 legacy 别名。
import { registerTaskPanel } from '@/assets/TaskPanel';
import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import { describe, expect, it } from '@jest/globals';

jest.mock('@/assets/TaskPanel/GroupDrillDown', () => ({ GroupSessionView: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanel', () => ({ TaskPanel: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanelAdapter', () => ({ TaskPanelAdapter: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanelFetcher', () => ({ TaskPanelFetcher: () => null }));
jest.mock('@/assets/TaskPanel/taskPanelMapper', () => ({
  mapDashboard: () => null,
  mapTaskStatus: () => null,
}));
jest.mock('@/assets/TaskPanel/v2/TaskArtifactsList', () => ({
  TaskArtifactsList: () => null,
  collectTaskArtifacts: () => [],
}));
jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({ registerPanelContent: jest.fn() }));

const registerPanelContentMock = registerPanelContent as unknown as jest.MockedFunction<
  typeof registerPanelContent
>;

describe('registerTaskPanel', () => {
  it('注册 taskPanel 主组件名与 task-loop legacy 别名', () => {
    registerTaskPanel();

    const names = registerPanelContentMock.mock.calls.map((call) => call[0]);
    expect(names).toEqual(['taskPanel.TaskLoopView', 'task-loop']);
  });
});
