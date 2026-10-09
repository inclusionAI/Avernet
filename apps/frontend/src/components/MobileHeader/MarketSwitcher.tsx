/**
 * MarketSwitcher - 移动端能力市场 Header 中间切换器
 *
 * 功能：
 * 1. 切换 Skill / MCP
 * 2. 切换 全部 / 我的（仅 Skill 市场显示）
 *
 * 样式规范详见 docs/移动端体验规范.md
 */
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { cn } from '@/utils/utils';
import { history } from '@umijs/max';
import { Check, Globe, User, Zap } from 'lucide-react';
import React, { useState } from 'react';
import { TitleSwitcher } from './index';

type MarketType = 'skill' | 'mcp';
type FilterType = 'all' | 'my';

interface MarketSwitcherProps {
  className?: string;
  /** 当前筛选类型 - 仅 Skill 市场使用 */
  currentFilter?: FilterType;
  /** 筛选切换回调 */
  onFilterChange?: (filter: FilterType) => void;
}

// 切换选项配置
const MARKET_OPTIONS: {
  value: MarketType;
  label: string;
  icon: React.ElementType;
}[] = [
  { value: 'skill', label: 'Skill', icon: Zap },
  { value: 'mcp', label: 'MCP', icon: Globe },
];

const FILTER_OPTIONS: {
  value: FilterType;
  label: string;
  icon: React.ElementType;
}[] = [
  { value: 'all', label: '全部', icon: Globe },
  { value: 'my', label: '我的', icon: User },
];

/**
 * 能力市场切换器
 * 用于 MobileHeader 中间显示市场类型和筛选条件
 */
export function MarketSwitcher({
  currentFilter = 'all',
  onFilterChange,
}: MarketSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'market' | 'filter'>('market');

  // 从 URL 获取当前状态
  const path = typeof window !== 'undefined' ? window.location.pathname : '';
  const currentMarket: MarketType = path.includes('/market/mcp')
    ? 'mcp'
    : 'skill';
  const isSkillMarket = currentMarket === 'skill';

  // 当前显示的标题
  const currentLabel =
    MARKET_OPTIONS.find((o) => o.value === currentMarket)?.label || 'Skill';
  // 当前筛选标签
  const currentFilterLabel =
    FILTER_OPTIONS.find((o) => o.value === currentFilter)?.label || '全部';

  const handleSwitchMarket = (market: MarketType) => {
    if (market === currentMarket) {
      // 如果市场类型相同，切换到筛选 Tab
      if (isSkillMarket) {
        setActiveTab('filter');
      } else {
        setOpen(false);
      }
      return;
    }
    history.push(`/market/${market}`);
    setOpen(false);
  };

  const handleSwitchFilter = (filter: FilterType) => {
    onFilterChange?.(filter);
    setOpen(false);
  };

  return (
    <>
      {/* Header 中间标题 */}
      <TitleSwitcher
        title={currentLabel}
        subtitle={isSkillMarket ? currentFilterLabel : undefined}
        onClick={() => {
          setActiveTab('market');
          setOpen(true);
        }}
        showDropdown
        data-aspm-click="ca114850.da193949"
        data-aspm-desc="MarketSwitcher-标题切换按钮"
        data-aspm-param={``}
        data-aspm-expo
      />

      {/* 切换抽屉 */}
      <Drawer open={open} onOpenChange={setOpen}>
        <DrawerContent
          position="bottom"
          width="100%"
          height="auto"
          title="切换市场"
          overlay
          className="p-0 rounded-t-2xl"
        >
          {/* 选项卡 - Skill 市场显示两个 Tab，MCP 只显示市场类型 */}
          <div className="flex border-b border-slate-100">
            <button
              type="button"
              onClick={() => setActiveTab('market')}
              data-aspm-click="ca114850.da193950"
              data-aspm-desc="MarketSwitcher-市场类型Tab"
              data-aspm-param={``}
              data-aspm-expo
              className={cn(
                'flex-1 py-3 text-sm font-medium text-center transition-colors',
                activeTab === 'market'
                  ? 'text-lavender-600 border-b-2 border-lavender-600'
                  : 'text-slate-500 hover:text-slate-700',
              )}
            >
              市场类型
            </button>
            {/* 仅 Skill 市场显示筛选 Tab */}
            {isSkillMarket && (
              <button
                type="button"
                onClick={() => setActiveTab('filter')}
                data-aspm-click="ca114850.da193951"
                data-aspm-desc="MarketSwitcher-筛选Tab"
                data-aspm-param={``}
                data-aspm-expo
                className={cn(
                  'flex-1 py-3 text-sm font-medium text-center transition-colors',
                  activeTab === 'filter'
                    ? 'text-lavender-600 border-b-2 border-lavender-600'
                    : 'text-slate-500 hover:text-slate-700',
                )}
              >
                筛选
              </button>
            )}
          </div>

          {/* 内容区 */}
          <div className="flex flex-col py-2 max-h-[50vh] overflow-y-auto">
            {/* 市场类型选项 */}
            {activeTab === 'market' && (
              <>
                {MARKET_OPTIONS.map((option) => {
                  const isActive = option.value === currentMarket;
                  const Icon = option.icon;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => handleSwitchMarket(option.value)}
                      data-aspm-click="ca114850.da193952"
                      data-aspm-desc="MarketSwitcher-市场类型选项"
                      data-aspm-param={``}
                      data-aspm-expo
                      className={cn(
                        'flex items-center gap-3 px-4 py-3.5 text-left transition-colors w-full active:bg-slate-100',
                        isActive
                          ? 'bg-lavender-50 text-lavender-600'
                          : 'text-slate-700 hover:bg-slate-50',
                      )}
                    >
                      <div className="w-10 h-10 rounded-xl bg-lavender-50 flex items-center justify-center flex-shrink-0">
                        <Icon size={20} className="text-lavender-500" />
                      </div>
                      <span className="text-sm font-medium flex-1 text-slate-800">
                        {option.label}
                      </span>
                      {isActive && (
                        <Check
                          size={18}
                          className="text-lavender-600 flex-shrink-0"
                        />
                      )}
                    </button>
                  );
                })}
              </>
            )}

            {/* 筛选选项 - 仅 Skill 市场 */}
            {activeTab === 'filter' && isSkillMarket && (
              <>
                {FILTER_OPTIONS.map((option) => {
                  const isActive = option.value === currentFilter;
                  const Icon = option.icon;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => handleSwitchFilter(option.value)}
                      data-aspm-click="ca114850.da193953"
                      data-aspm-desc="MarketSwitcher-筛选选项"
                      data-aspm-param={``}
                      data-aspm-expo
                      className={cn(
                        'flex items-center gap-3 px-4 py-3.5 text-left transition-colors w-full active:bg-slate-100',
                        isActive
                          ? 'bg-lavender-50 text-lavender-600'
                          : 'text-slate-700 hover:bg-slate-50',
                      )}
                    >
                      <div className="w-10 h-10 rounded-xl bg-lavender-50 flex items-center justify-center flex-shrink-0">
                        <Icon size={20} className="text-lavender-500" />
                      </div>
                      <span className="text-sm font-medium flex-1 text-slate-800">
                        {option.label}
                      </span>
                      {isActive && (
                        <Check
                          size={18}
                          className="text-lavender-600 flex-shrink-0"
                        />
                      )}
                    </button>
                  );
                })}
              </>
            )}
          </div>
        </DrawerContent>
      </Drawer>

      {/* 分类树按钮放在右侧 - 由页面传入 rightContent */}
    </>
  );
}

/**
 * 分类树抽屉按钮
 * 用于 Market 页面的 Header 右侧
 */
export function MarketCategoryButton({
  onClick,
  categoryName,
}: {
  onClick: () => void;
  categoryName?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-aspm-click="ca114851.da193963"
      data-aspm-desc="MarketCategoryButton-分类按钮"
      data-aspm-param={``}
      data-aspm-expo
      className="flex items-center gap-1 px-3 py-1.5 text-xs text-slate-600 bg-slate-100 hover:bg-slate-200 active:bg-slate-300 rounded-lg transition-colors max-w-[100px]"
      title={categoryName || '选择分类'}
    >
      <span className="truncate font-medium">{categoryName || '分类'}</span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>
  );
}
