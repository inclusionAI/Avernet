import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems, navigationItems } from '@/shell/navigation';
import { describe, expect, it } from '@jest/globals';
import { Compass, Sparkles } from 'lucide-react';

// getMergedNavigationItems 合并与形态入口过滤（adminEntry）断言。
// 注意：extendCapabilities 合并后无法恢复，故 internal 形态用例置于文件末尾，不影响前述默认用例。

describe('getMergedNavigationItems（Open Core 默认 capabilities）', () => {
  it('adminEntry=true：保留【管理后台】项于 manage 分区末位，基线项原序保留', () => {
    const merged = getMergedNavigationItems();
    expect(merged.find((item) => item.id === 'admin')).toBeDefined();
    // Open Core 默认无内部导航项注入，合并结果 = 基线 navigationItems 原序。
    expect(merged.map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
  });

  it('基线 navigationItems 数组字面量本身不被改动（过滤只发生在合并点）', () => {
    expect(navigationItems.some((item) => item.id === 'admin')).toBe(true);
  });
});

describe('getMergedNavigationItems（internal overlay 语义，capability override）', () => {
  it('adminEntry=true + 内部导航项注入：【管理后台】保留于末位，内部项插在其前', () => {
    extendCapabilities({
      getInternalNavigationItems: () => ({
        status: 'available',
        value: [
          {
            id: 'capability-workshop',
            label: '能力工坊',
            path: '/capability-workshop',
            icon: Sparkles,
            area: 'manage',
            description: '管理 Skill 与 MCP',
          },
          {
            id: 'market',
            label: '能力市场',
            path: '/market',
            icon: Compass,
            area: 'manage',
            description: '发现和添加通用能力',
          },
        ],
      }),
      getShellVisibility: () => ({
        status: 'available',
        value: { adminEntry: true, spaceSwitcher: true, notificationBell: true },
      }),
    });

    expect(getMergedNavigationItems().map((item) => item.id)).toEqual([
      'workspace',
      'my-task',
      'collaboration-square',
      'collaboration-privacy',
      'bot-workshop',
      'capability-workshop',
      'market',
      'admin',
    ]);
  });
});
