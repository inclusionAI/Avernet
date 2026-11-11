import { describe, expect, it } from '@jest/globals';
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { NavigationItem } from '@/shell/navigation';
import { getMergedNavigationItems, navigationItems } from '@/shell/navigation';
import { Users } from 'lucide-react';

/**
 * 产品一级导航基线的形态开关（collab-form）：
 * Open Core 默认 null = 沿用 `src/shell/navigation.tsx` 的 Open Core 基线数组（Open/internal 零变化）；
 * collab 装配覆盖为「协作群 + 发现 + 外部 Bot」三项。internal 注入项仍经
 * `getInternalNavigationItems` 按 section 追加，与本基线正交。
 */
// 注意：extendCapabilities 合并后无法恢复，故默认形态用例一律置前，override 用例置于文件末尾
// （navigation.test.tsx 同约定）。
describe('getPrimaryNavigationItems capability', () => {
  it('Open Core 默认返回 null（沿用 shell/navigation 基线数组）', () => {
    expect(defaultCapabilities.getPrimaryNavigationItems()).toEqual({ status: 'available', value: null });
  });

  it('合并结果默认仍等于模块基线数组（Open 行为零变化）', () => {
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
  });

  it('装配覆盖后基线被整体替换（internal 注入项仍按 section 追加）', () => {
    const collabItems: NavigationItem[] = [
      {
        id: 'collaboration',
        label: '协作群',
        path: '/workspace/collaboration',
        icon: Users,
        section: 'collab',
        description: '与用户和 Bot 即时协作',
      },
    ];
    extendCapabilities({ getPrimaryNavigationItems: () => ({ status: 'available', value: collabItems }) });
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(['collaboration']);
  });

  // —— 基线语义收口（CF-2）：显式 [] 与 null 的区分。——
  it('显式空数组 [] 是合法「空基线」覆盖：基线清空，internal 注入项仍追加', () => {
    const internalItem: NavigationItem = {
      id: 'market',
      label: '能力市场',
      path: '/market',
      icon: Users,
      section: 'bot',
      description: '内部专属市场入口',
    };
    extendCapabilities({
      getPrimaryNavigationItems: () => ({ status: 'available', value: [] }),
      getInternalNavigationItems: () => ({ status: 'available', value: [internalItem] }),
    });
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(['market']);
  });

  it('status 非 available（unsupported）与 null 同义：回退模块基线数组', () => {
    extendCapabilities({
      getPrimaryNavigationItems: () => ({ status: 'unsupported', value: null }),
      getInternalNavigationItems: () => ({ status: 'available', value: [] }),
    });
    expect(getMergedNavigationItems().map((item) => item.id)).toEqual(navigationItems.map((item) => item.id));
  });
});
