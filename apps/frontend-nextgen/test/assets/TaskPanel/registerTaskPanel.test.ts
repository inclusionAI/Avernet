// 任务副屏注册入口(index.ts)单测:验证 registerTaskPanel 把 TaskPanelAdapter 以
// 双 key(taskPanel.TaskLoopView + legacy 别名 task-loop)注册进副屏 SDK registerPanelContent。
// index.ts 还 re-export 一票兄弟模块,这里全部 mock 掉,只验证注册胶水本身。
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({
  registerPanelContent: jest.fn(),
}));
// 兄弟模块 mock:避免把 GroupDrillDown / TaskPanel / mapper 等 UI 大组件链拖进 node 环境。
jest.mock('@/assets/TaskPanel/GroupDrillDown', () => ({ GroupSessionView: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanel', () => ({ TaskPanel: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanelAdapter', () => ({ TaskPanelAdapter: () => null }));
jest.mock('@/assets/TaskPanel/TaskPanelFetcher', () => ({ TaskPanelFetcher: () => null }));
jest.mock('@/assets/TaskPanel/taskPanelMapper', () => ({
  mapDashboard: () => null,
  mapTaskStatus: () => 'EXECUTING',
}));
jest.mock('@/assets/TaskPanel/v2/TaskArtifactsList', () => ({
  collectTaskArtifacts: () => [],
  TaskArtifactsList: () => null,
}));

import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import { TaskPanelAdapter } from '@/assets/TaskPanel/TaskPanelAdapter';
import { registerTaskPanel } from '@/assets/TaskPanel';

const mockedRegister = registerPanelContent as unknown as jest.Mock;

describe('registerTaskPanel 副屏注册', () => {
  beforeEach(() => {
    mockedRegister.mockClear();
  });

  it('把 TaskPanelAdapter 以 taskPanel.TaskLoopView 注册(lib.Component 点号 key,CDN 优先命中用)', () => {
    registerTaskPanel();

    expect(mockedRegister).toHaveBeenCalledTimes(2);
    expect(mockedRegister).toHaveBeenNthCalledWith(1, 'taskPanel.TaskLoopView', TaskPanelAdapter);
  });

  it('把 TaskPanelAdapter 以 legacy 别名 task-loop 再注册一次(兼容已落库旧副屏消息)', () => {
    registerTaskPanel();

    expect(mockedRegister).toHaveBeenNthCalledWith(2, 'task-loop', TaskPanelAdapter);
  });
});
