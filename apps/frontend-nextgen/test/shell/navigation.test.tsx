import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems, navigationItems } from '@/shell/navigation';
import { describe, expect, it } from '@jest/globals';
import { Compass, Sparkles } from 'lucide-react';

// getMergedNavigationItems 只承载产品侧栏的 collab / bot 两个分组。
// 无导航位页面（如任务列表）保留路由，但不进入 navigationItems。
// 注意：extendCapabilities 合并后无法恢复，故越靠后的用例携带越多的 capability override，
// 默认形态用例一律置前，internal 注入 override 用例按依赖顺序排列。

describe('getMergedNavigationItems（Open Core 默认 capabilities）', () => {
  // workspace-conversation-navigation-refactor：原「对话协作」项拆分为
  // 对话（/workspace/chat）与 协作群（/workspace/collaboration）两个一级入口。
  it('exposes separate conversation and collaboration navigation items', () => {
    expect(navigationItems.filter((item) => item.section === 'collab')).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'conversation', label: '对话', path: '/workspace/chat' }),
        expect.objectContaining({ id: 'collaboration', label: '协作群', path: '/workspace/collaboration' }),
      ]),
    );
  });

  it('双固定分组：flat 序 = collab → bot，组内基线项原序', () => {
    const merged = getMergedNavigationItems();
    expect(merged.filter((item) => item.section === 'collab').map((item) => item.id)).toEqual([
      'conversation',
      'collaboration',
      'collaboration-square',
      'lab',
    ]);
    expect(merged.filter((item) => item.section === 'bot').map((item) => item.id)).toEqual(['bot-workshop']);
    expect(merged.some((item) => item.id === 'my-task')).toBe(false);
  });

  it('同路由就地改名项归组正确（Bot管理 / 发现）', () => {
    const merged = getMergedNavigationItems();
    const bot = merged.find((item) => item.id === 'bot-workshop');
    expect(bot?.label).toBe('Bot管理');
    expect(bot?.section).toBe('bot');

    const found = merged.find((item) => item.id === 'collaboration-square');
    expect(found?.label).toBe('发现');
    expect(found?.section).toBe('collab');
  });

  it('无产品导航位的存量入口均不出现在侧栏', () => {
    expect(getMergedNavigationItems().some((item) => item.id === 'my-task')).toBe(false);
    expect(getMergedNavigationItems().some((item) => item.id === 'admin')).toBe(false);
    expect(getMergedNavigationItems().some((item) => item.id === 'collaboration-privacy')).toBe(false);
  });

  it('基线 navigationItems 数组字面量本身不被改动（分组与过滤只发生在合并点）', () => {
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
    expect(navigationItems.some((item) => item.id === 'admin')).toBe(false);
  });
});

describe('getMergedNavigationItems（internal overlay 注入语义）', () => {
  it('内部项按自身 section 追加到同名产品分组末尾', () => {
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
        ],
      }),
      getShellVisibility: () => ({
        status: 'available',
        value: { spaceSwitcher: true, notificationBell: true },
      }),
    });

    const merged = getMergedNavigationItems();
    expect(merged.map((item) => item.id)).toEqual([
      'conversation',
      'collaboration',
      'collaboration-square',
      'lab',
      'bot-workshop',
      'capability-workshop',
      'market',
    ]);
    expect(merged.find((item) => item.id === 'capability-workshop')?.label).toBe('能力管理');
  });
});
