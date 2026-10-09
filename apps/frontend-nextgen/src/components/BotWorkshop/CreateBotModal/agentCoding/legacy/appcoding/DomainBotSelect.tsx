import { Button } from '@/components/ui/Button';
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * DomainBotSelect - 域架构 Bot 自定义选择组件
 *
 * 使用自定义下拉框实现，避免 Radix UI Select 锁定页面滚动
 * 支持输入文字筛选下拉选项
 * 显示格式：title 是 bot_name，描述是 arch_domain
 */

import type { Bot } from '@/services/botWorkshop/agentCodingLegacyService';
import { cn } from '@/utils/cn';
import { ChevronDown, Loader2, X } from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface DomainBotSelectProps {
  /** 当前选中的 Bot ID */
  value: string;
  /** Bot 列表 */
  options: Bot[];
  /** 是否加载中 */
  loading?: boolean;
  /** 禁用状态 */
  disabled?: boolean;
  /** 值变化回调 */
  onChange: (botId: string) => void;
  /** 占位符 */
  placeholder?: string;
  /** 输入框样式类 */
  className?: string;
}

export function DomainBotSelect({
  value,
  options,
  loading = false,
  disabled = false,
  onChange,
  placeholder = '选择负责此应用的架构 Bot',
  className,
}: DomainBotSelectProps) {
  // 下拉框显示状态
  const [isOpen, setIsOpen] = useState(false);
  // 搜索关键词
  const [searchText, setSearchText] = useState('');
  // 输入框 ref
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 获取当前选中的 Bot
  const selectedBot = options.find((bot) => bot.bot_id === value);

  // 获取 Bot 的架构域名（从 ext.arch_domain 获取）
  const getArchDomain = (bot: Bot): string => {
    return (bot as any).ext?.arch_domain || '';
  };

  // 获取 Bot 的拥有者展示名（优先 owner_name，回退 owner_id）
  const getOwnerName = (bot: Bot): string => {
    return bot.owner_name || bot.owner_id || '';
  };

  // 根据搜索关键词筛选选项
  const filteredOptions = useMemo(() => {
    if (!searchText.trim()) return options;
    const lower = searchText.toLowerCase();
    return options.filter((bot) => {
      const name = (bot.bot_name || '').toLowerCase();
      const domain = getArchDomain(bot).toLowerCase();
      const owner = getOwnerName(bot).toLowerCase();
      return name.includes(lower) || domain.includes(lower) || owner.includes(lower);
    });
  }, [options, searchText]);

  // 点击外部关闭下拉框
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // 打开下拉框时重置搜索并聚焦输入框
  useEffect(() => {
    if (isOpen) {
      setSearchText('');
      // 延迟聚焦，确保 DOM 已更新
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    }
  }, [isOpen]);

  // 处理选择
  const handleSelect = useCallback(
    (bot: Bot) => {
      onChange(bot.bot_id);
      setIsOpen(false);
      setSearchText('');
      // 应用 Bot 埋点 A3：选择 DomainBot
      // 详见 docs/架构与规范/埋点/应用Bot埋点方案.md
    },
    [onChange],
  );

  // 处理清空选择
  const handleClear = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onChange('');
      setIsOpen(false);
      setSearchText('');
    },
    [onChange],
  );

  // 输入框获得焦点时打开下拉框
  const handleFocus = useCallback(() => {
    if (!disabled && !loading) {
      setIsOpen(true);
    }
  }, [disabled, loading]);

  // 输入框内容变化
  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setSearchText(e.target.value);
      if (!isOpen) {
        setIsOpen(true);
      }
    },
    [isOpen],
  );

  // 键盘事件：Esc 关闭下拉框
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
      inputRef.current?.blur();
    }
  }, []);

  // 点击已选中区域时，打开下拉并进入搜索模式
  const handleSelectedClick = useCallback(() => {
    if (!disabled && !loading) {
      setIsOpen(true);
    }
  }, [disabled, loading]);

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      {/* 选中且下拉关闭时：显示 name + domain 的叠加层（与原样式一致） */}
      {!isOpen && selectedBot && (
        <div
          onClick={handleSelectedClick}
          className={cn(
            'w-full px-3 py-1.5 text-[13px] border border-slate-200 rounded-lg bg-white',
            'focus:outline-none focus:ring-1 focus:ring-slate-300 focus:border-transparent',
            'text-left flex items-center justify-between gap-2 cursor-pointer',
            'hover:border-slate-300',
            disabled && 'bg-slate-50 cursor-not-allowed',
          )}
        >
          <span className="truncate flex flex-col">
            <span className="text-slate-700">{selectedBot.bot_name}</span>
            {(getArchDomain(selectedBot) || getOwnerName(selectedBot)) && (
              <span className="text-[11px] text-slate-400 flex items-center gap-1">
                {getArchDomain(selectedBot) && <span>{getArchDomain(selectedBot)}</span>}
                {getArchDomain(selectedBot) && getOwnerName(selectedBot) && <span className="text-slate-300">·</span>}
                {getOwnerName(selectedBot) && <span>所属者：{getOwnerName(selectedBot)}</span>}
              </span>
            )}
          </span>
          {value && !disabled && !loading ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleClear}
              className="h-auto min-h-0 w-auto cursor-pointer rounded border-0 bg-transparent p-0.5 text-slate-400 font-normal shadow-none hover:bg-slate-100 hover:text-slate-600"
              aria-label="清空选择"
            >
              <X size={12} />
            </Button>
          ) : (
            <ChevronDown size={14} className="text-slate-400 flex-shrink-0" />
          )}
        </div>
      )}

      {/* 输入框：下拉打开时 或 未选中时 显示 */}
      {(isOpen || !selectedBot) && (
        <div
          className={cn(
            'w-full flex items-center border border-slate-200 rounded-lg bg-white',
            'focus-within:ring-1 focus-within:ring-slate-300 focus-within:border-transparent',
            disabled && 'bg-slate-50',
          )}
        >
          {loading ? (
            <div className="flex-1 px-3 py-1.5 text-[13px] flex items-center gap-1.5 text-slate-400">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>加载中...</span>
            </div>
          ) : (
            <input
              ref={inputRef}
              type="text"
              value={isOpen ? searchText : ''}
              onChange={handleInputChange}
              onFocus={handleFocus}
              onKeyDown={handleKeyDown}
              disabled={disabled}
              placeholder={placeholder}
              className={cn(
                'flex-1 min-w-0 px-3 py-1.5 text-[13px] bg-transparent',
                'focus:outline-none',
                'placeholder:text-slate-300',
                'disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed',
              )}
            />
          )}
          <ChevronDown
            size={14}
            className={cn('text-slate-400 flex-shrink-0 mr-2 transition-transform', isOpen && 'rotate-180')}
          />
        </div>
      )}

      {/* 下拉选项列表 */}
      {isOpen && !loading && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
          {filteredOptions.length === 0 ? (
            <div className="px-3 py-2 text-xs text-slate-400 text-center">
              {searchText.trim() ? '无匹配结果' : '暂无域架构 Bot'}
            </div>
          ) : (
            filteredOptions.map((bot) => {
              const archDomain = getArchDomain(bot);
              return (
                <Button
                  key={bot.bot_id}
                  type="button"
                  variant="ghost"
                  onClick={() => handleSelect(bot)}
                  disabled={disabled}
                  className={cn(
                    'h-auto min-h-0 w-full justify-start rounded-none border-0 bg-transparent px-3 py-2 text-left font-normal shadow-none cursor-pointer hover:bg-slate-50 transition-colors',
                    'border-b border-slate-100 last:border-b-0',
                    value === bot.bot_id && 'bg-lavender-50',
                  )}
                >
                  <div className="flex flex-col">
                    <span
                      className={cn(
                        'text-[13px]',
                        value === bot.bot_id ? 'font-medium text-lavender-700' : 'font-medium text-slate-700',
                      )}
                    >
                      {bot.bot_name}
                    </span>
                    {(archDomain || getOwnerName(bot)) && (
                      <span className="text-[11px] text-slate-400 line-clamp-1 flex items-center gap-1">
                        {archDomain && <span>{archDomain}</span>}
                        {archDomain && getOwnerName(bot) && <span className="text-slate-300">·</span>}
                        {getOwnerName(bot) && <span>所属者：{getOwnerName(bot)}</span>}
                      </span>
                    )}
                  </div>
                </Button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

export default DomainBotSelect;
