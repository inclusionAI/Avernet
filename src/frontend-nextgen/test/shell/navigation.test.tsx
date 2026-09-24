import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems, navigationItems } from '@/shell/navigation';
import { describe, expect, it } from '@jest/globals';
import { Compass, Sparkles } from 'lucide-react';

// getMergedNavigationItems 分组模型（section: collab|bot|legacy）断言。
// split-admin-space-ticket-pages：【管理后台】基线项退役（管理域拆分至 /space-admin、/ticket-center
// 独立路由，入口不再占导航位），legacy 过渡组仅剩 任务列表/协作权限；adminEntry 形态开关随项退役
// （getShellVisibility 剩 spaceSwitcher/notificationBell 两字段）。
// 注意：extendCapabilities 合并后无法恢复，故越靠后的用例携带越多的 capability override，
// 默认形态用例一律置前，internal 注入 override 用例按依赖顺序排列。

describe('getMergedNavigationItems（Open Core 默认 capabilities）', () => {
  it('双固定分组 + legacy 过渡组：flat 序 = collab → bot → legacy，组内基线项原序', () => {
    const merged = getMergedNavigationItems();
    expect(merged.filter((item) => item.section === 'collab').map((item) => item.id)).toEqual([
      'workspace',
      'collaboration-square',
    ]);
    expect(merged.filter((item) => item.section === 'bot').map((item) => item.id)).toEqual(['bot-workshop']);
    expect(merged.filter((item) => item.section === 'legacy').map((item) => item.id)).toEqual([
      'my-task',
      'collaboration-privacy',
    ]);
  });

  it('同路由就地改名项归组正确且不打 deprecated（Bot管理 / 发现）', () => {
    const merged = getMergedNavigationItems();
    const bot = merged.find((item) => item.id === 'bot-workshop');
    expect(bot?.label).toBe('Bot管理');
    expect(bot?.section).toBe('bot');
    expect(bot?.deprecated).toBeUndefined();

    const found = merged.find((item) => item.id === 'collaboration-square');
    expect(found?.label).toBe('发现');
    expect(found?.section).toBe('collab');
    expect(found?.deprecated).toBeUndefined();
  });

  it('在新 IA 中无继任位的项沉淀 legacy 过渡组并带 deprecated 标记（管理后台项已退役）', () => {
    for (const id of ['my-task', 'collaboration-privacy']) {
      const item = getMergedNavigationItems().find((entry) => entry.id === id);
      expect(item?.section).toBe('legacy');
      expect(item?.deprecated).toBe(true);
    }
    expect(getMergedNavigationItems().some((item) => item.id === 'admin')).toBe(false);
  });

  it('基线 navigationItems 数组字面量本身不被改动（分组与过滤只发生在合并点）', () => {
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
    expect(navigationItems.some((item) => item.id === 'admin')).toBe(false);
  });
});

describe('getMergedNavigationItems（internal overlay 注入语义）', () => {
  it('内部项按自身 section 追加到同名分组末尾，deprecated 标记透传', () => {
    extendCapabilities({
      getInternalNavigationItems: () => ({
        status: 'available',
        value: [
          {
            id: 'capability-workshop',
            label: '能力管理',
            path: '/capability-workshop',
            icon: Sparkles,
            section: 'bot',
            description: '管理 Skill 与 MCP',
          },
          {
            id: 'market',
            label: '能力市场',
            path: '/market',
            icon: Compass,
            section: 'bot',
            description: '发现和添加通用能力',
          },
          {
            id: 'legacy-portal',
            label: '旧门户',
            path: '/legacy-portal',
            icon: Compass,
            section: 'legacy',
            deprecated: true,
            description: 'deprecated 透传用例',
          },
        ],
      }),
      getShellVisibility: () => ({
        status: 'available',
        value: { spaceSwitcher: true, notificationBell: true },
      }),
    });

    const merged = getMergedNavigationItems();
    expect(merged.map((item) => item.id)).toEqual([
      'workspace',
      'collaboration-square',
      'bot-workshop',
      'capability-workshop',
      'market',
      'my-task',
      'collaboration-privacy',
      'legacy-portal',
    ]);
    expect(merged.find((item) => item.id === 'capability-workshop')?.label).toBe('能力管理');
    expect(merged.find((item) => item.id === 'legacy-portal')?.deprecated).toBe(true);
  });
});
