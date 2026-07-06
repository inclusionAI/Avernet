/**
 * SettingTabsButton - 移动端设置按钮（带 Tab 下拉菜单 + 抽屉）
 *
 * 功能：
 * 1. 点击设置按钮出现下拉菜单展示多个 Tab
 * 2. 点击 Tab 打开右侧抽屉展示对应内容
 * 3. 抽屉宽度 = 屏幕宽度，最小 375px
 *
 * 使用场景：
 * - 个人助手页 (Assistant)
 * - 智能专家页 (PrivateChat)
 *
 * 样式规范详见 docs/移动端体验规范.md
 */
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { cn } from '@/utils/utils';
import { Settings } from 'lucide-react';
import React, { useState } from 'react';

export interface SettingTab {
  id: string;
  label: string;
  icon: React.ElementType;
}

interface SettingTabsButtonProps {
  /** Tab 配置列表 */
  tabs: SettingTab[];
  /** 渲染抽屉内容的回调 */
  renderDrawerContent: (activeTabId: string) => React.ReactNode;
  /** 自定义类名 */
  className?: string;
}

/**
 * 设置按钮组件
 * 点击后显示下拉菜单，选择 Tab 后打开对应抽屉
 */
export function SettingTabsButton({
  tabs,
  renderDrawerContent,
  className,
}: SettingTabsButtonProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeTabId, setActiveTabId] = useState<string>('');

  // 处理 Tab 选择
  const handleTabSelect = (tabId: string) => {
    setActiveTabId(tabId);
    setDropdownOpen(false);
    setDrawerOpen(true);
  };

  // 获取当前选中的 Tab
  const activeTab = tabs.find((t) => t.id === activeTabId);

  return (
    <>
      {/* 设置按钮 + 下拉菜单容器 */}
      <div className={cn('relative', className)}>
        {/* 设置按钮 */}
        <button
          type="button"
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="flex items-center justify-center w-9 h-9 rounded-lg text-slate-600 active:bg-slate-100 transition-colors"
          aria-label="设置"
          data-aspm-click="ca114853.da193932"
          data-aspm-desc="SettingTabsButton-设置按钮"
          data-aspm-param={``}
          data-aspm-expo
        >
          <Settings size={18} />
        </button>

        {/* 下拉菜单 */}
        {dropdownOpen && (
          <>
            {/* 遮罩层 - 点击关闭下拉菜单 */}
            <div
              className="fixed inset-0 z-40"
              onClick={() => setDropdownOpen(false)}
              data-aspm-click="ca114853.da193933"
              data-aspm-desc="SettingTabsButton-遮罩层"
              data-aspm-param={``}
              data-aspm-expo
            />

            {/* 下拉菜单内容 */}
            <div className="absolute right-0 top-full mt-1.5 w-44 bg-white rounded-xl shadow-lg border border-slate-100 py-1.5 z-50">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => handleTabSelect(tab.id)}
                    className={cn(
                      'w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors',
                      'hover:bg-slate-50 active:bg-slate-100',
                    )}
                  >
                    <Icon size={18} className="text-slate-500" />
                    <span className="text-slate-700 font-medium">
                      {tab.label}
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* 抽屉 - 从右侧滑出 */}
      <Drawer open={drawerOpen} onOpenChange={setDrawerOpen}>
        <DrawerContent
          position="right"
          width="100%"
          minWidth="375px"
          height="100%"
          title={activeTab?.label || '设置'}
          overlay
          className="p-0 flex flex-col"
        >
          {/* Tab 导航栏 - 横向滚动 */}
          <div className="flex border-b border-slate-100 overflow-x-auto scrollbar-hide">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = tab.id === activeTabId;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTabId(tab.id)}
                  className={cn(
                    'flex items-center gap-1.5 px-4 py-3 text-sm whitespace-nowrap transition-colors flex-shrink-0',
                    isActive
                      ? 'text-lavender-600 border-b-2 border-lavender-600'
                      : 'text-slate-500 hover:text-slate-700',
                  )}
                >
                  <Icon size={16} />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>

          {/* 抽屉内容区 - 可滚动 */}
          <div className="flex-1 overflow-y-auto p-4">
            {activeTabId && renderDrawerContent(activeTabId)}
          </div>
        </DrawerContent>
      </Drawer>
    </>
  );
}

export default SettingTabsButton;
