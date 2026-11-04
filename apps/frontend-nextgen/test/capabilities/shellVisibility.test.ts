import { describe, expect, it } from '@jest/globals';
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

describe('getShellVisibility', () => {
  it('Open Core 默认含 globalSearch:true（现状行为不变）', () => {
    expect(defaultCapabilities.getShellVisibility()).toEqual({
      status: 'available',
      value: { spaceSwitcher: false, notificationBell: true, globalSearch: true },
    });
  });
});

// 空间上下文初始化总开关（collab-form，Task 2A）：Open 默认 true（现状行为零变化）；
// 消费方双初始化断言在 test/shell/AppShell.test.tsx（capability 覆盖为 false 的两用例），
// 此处只锁默认契约值。collab 装配（Task 12 落 src/extensions/collab.ts）覆盖为 false。
describe('getSpaceContextInitializationEnabled', () => {
  it('Open Core 默认 true：挂载与 Bot 分组两条空间初始化保持现状', () => {
    expect(defaultCapabilities.getSpaceContextInitializationEnabled()).toEqual({
      status: 'available',
      value: true,
    });
  });
});
