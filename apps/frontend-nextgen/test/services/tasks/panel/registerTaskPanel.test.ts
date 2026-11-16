// 任务副屏注册入口(index.ts)单测:验证 registerTaskPanel 把 TaskPanelAdapter 以
// 双 key(taskPanel.TaskLoopView + legacy 别名 task-loop)注册进副屏 SDK registerPanelContent。
// 注册模块只依赖 SDK 与宿主 adapter，不再 re-export 业务 UI。
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({
  registerPanelContent: jest.fn(),
}));
jest.mock('@/components/Workspace/TaskPanelAdapter', () => ({ TaskPanelAdapter: () => null }));

import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import { TaskPanelAdapter } from '@/components/Workspace/TaskPanelAdapter';
import { registerTaskPanel } from '@/services/tasks/panel/registerTaskPanel';

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
