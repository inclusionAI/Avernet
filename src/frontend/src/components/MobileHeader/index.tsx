/**
 * MobileHeader - 移动端统一顶部导航栏
 *
 * 布局规则：
 * 1. 左侧：菜单按钮（打开左侧导航抽屉）
 * 2. 中间：页面标题/切换器（可自定义内容）
 * 3. 右侧：操作按钮区（可自定义，默认显示设置）
 *
 * 页面中间内容规则（由页面组件传入 centerContent）：
 * - Assistant 页：当前 Bot 名称，点击展开 Bot/群聊切换
 * - Market 页：skill/mcp 切换 + 全部/我的切换
 * - 其他页：页面标题
 *
 * 样式规范详见 docs/移动端体验规范.md §2
 */
import { cn } from '@/utils/utils';
import { ChevronDown, Menu, Settings } from 'lucide-react';
import React from 'react';

interface MobileHeaderProps {
  /** 左侧菜单按钮点击 */
  onMenuClick: () => void;

  /** 中间内容 - 页面自定义（如 Bot 切换器、状态切换等） */
  centerContent?: React.ReactNode;

  /** 右侧内容 - 页面自定义操作按钮 */
  rightContent?: React.ReactNode;

  /** 是否显示默认设置按钮（当 rightContent 为空时） */
  showSettings?: boolean;

  /** 默认设置按钮点击 */
  onSettingsClick?: () => void;

  className?: string;
}

/**
 * 标题切换器模板组件
 * 用于页面中间显示可切换的内容
 */
export interface TitleSwitcherProps {
  title: string;
  subtitle?: string;
  onClick?: () => void;
  showDropdown?: boolean;
  active?: boolean;
}

export function TitleSwitcher({
  title,
  subtitle,
  onClick,
  showDropdown = true,
  active = true,
}: TitleSwitcherProps) {
  // 移动端标题最多展示6个字，超出显示省略号
  const displayTitle = title.length > 6 ? title.slice(0, 6) + '…' : title;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      data-aspm-click="ca114849.da194007"
      data-aspm-desc="TitleSwitcher-标题切换按钮"
      data-aspm-param={``}
      data-aspm-expo
      className={cn(
        'flex items-center justify-center flex-1 px-2 min-w-0',
        onClick && 'active:bg-slate-100 rounded-lg transition-colors',
      )}
    >
      <div className="flex flex-col items-center">
        <span
          className={cn(
            'text-sm font-medium truncate max-w-[200px]',
            active ? 'text-slate-800' : 'text-slate-500',
          )}
        >
          {displayTitle}
        </span>
        {subtitle && (
          <span className="text-[10px] text-slate-400 truncate max-w-[200px]">
            {subtitle}
          </span>
        )}
      </div>
      {showDropdown && onClick && (
        <ChevronDown size={14} className="text-slate-400 ml-1" />
      )}
    </button>
  );
}

export function MobileHeader({
  onMenuClick,
  centerContent,
  rightContent,
  showSettings = true,
  onSettingsClick,
  className,
}: MobileHeaderProps) {
  return (
    <header
      className={cn(
        'flex items-center justify-between px-3 h-12 bg-white border-b border-slate-100 flex-shrink-0',
        className,
      )}
    >
      {/* 左侧菜单 Icon - 大尺寸触摸区域 */}
      <button
        type="button"
        onClick={onMenuClick}
        data-aspm-click="ca114847.da193994"
        data-aspm-desc="MobileHeader-菜单按钮"
        data-aspm-param={``}
        data-aspm-expo
        className="flex items-center justify-center w-10 h-10 rounded-lg text-slate-700 active:bg-slate-100 shrink-0 transition-colors"
        aria-label="菜单"
      >
        <Menu size={22} />
      </button>

      {/* 中间内容区 - 页面自定义 */}
      <div className="flex-1 flex justify-center min-w-0 px-2">
        {centerContent}
      </div>

      {/* 右侧内容区 - 页面自定义或默认设置 */}
      <div className="shrink-0 flex items-center">
        {rightContent ||
          (showSettings ? (
            <button
              type="button"
              onClick={onSettingsClick}
              data-aspm-click="ca114847.da193995"
              data-aspm-desc="MobileHeader-设置按钮"
              data-aspm-param={``}
              data-aspm-expo
              className="flex items-center justify-center w-10 h-10 rounded-lg text-slate-700 active:bg-slate-100 transition-colors"
              aria-label="设置"
            >
              <Settings size={20} />
            </button>
          ) : (
            <div className="w-10" /> // 占位保持对称
          ))}
      </div>
    </header>
  );
}

export default MobileHeader;
