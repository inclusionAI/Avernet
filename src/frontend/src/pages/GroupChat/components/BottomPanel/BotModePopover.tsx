/**
 * BotModePopover - Bot 发言模式切换 Popover
 *
 * 提取自 GroupChatPage 中重复出现的 Bot 模式切换逻辑
 * 仅渲染 PopoverTrigger 按钮 + PopoverContent，不包含外层布局
 */

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/utils/utils';
import { ChevronDown, Loader2 } from 'lucide-react';
import React from 'react';
import type { ParticipantMode } from '../../types';

interface ModeConfigItem {
  label: string;
  description: string;
  icon: any;
  color: string;
  disabled?: boolean;
  badge?: string;
}

export interface BotModePopoverProps {
  currentBotMode: ParticipantMode;
  currentModeConfig: ModeConfigItem;
  isUpdatingMode: boolean;
  onModeChange: (mode: ParticipantMode) => void;
  modeConfig: Record<string, ModeConfigItem>;
}

const BotModePopover: React.FC<BotModePopoverProps> = ({
  currentBotMode,
  currentModeConfig,
  isUpdatingMode,
  onModeChange,
  modeConfig,
}) => {
  const TriggerIcon = currentModeConfig.icon;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={isUpdatingMode}
          className="flex items-center gap-3 px-4 py-2.5 bg-white border border-slate-200 rounded-xl hover:bg-slate-50 hover:border-slate-300 transition-colors"
        >
          {isUpdatingMode ? (
            <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
          ) : (
            <TriggerIcon className="w-5 h-5 text-blue-500" />
          )}
          <div className="text-left">
            <p className="text-xs text-slate-400 leading-none">Bot发言模式</p>
            <div className="flex items-center gap-1 mt-0.5">
              <span className="text-sm font-medium text-slate-800">
                {currentModeConfig.label}
              </span>
              <ChevronDown className="w-4 h-4 text-slate-400" />
            </div>
          </div>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[320px] p-0">
        <div className="p-3 border-b border-slate-100">
          <h4 className="text-sm font-medium text-slate-800">
            选择 Bot 发言模式
          </h4>
          <p className="text-xs text-slate-400 mt-0.5">
            不同模式会影响 Bot 在群内的发言方式
          </p>
        </div>
        <div className="p-2 space-y-1">
          {(['auto', 'assistant', 'muted'] as const).map((mode) => {
            const config = modeConfig[mode];
            const Icon = config.icon;
            const isDisabled = config.disabled;
            const isActive = currentBotMode === mode;
            return (
              <button
                key={mode}
                type="button"
                onClick={() =>
                  !isDisabled && onModeChange(mode as ParticipantMode)
                }
                disabled={isUpdatingMode || isDisabled}
                className={cn(
                  'w-full flex items-start gap-3 px-3 py-3 rounded-lg text-left transition-colors',
                  isDisabled
                    ? 'opacity-60 cursor-not-allowed'
                    : isActive
                    ? 'bg-blue-50'
                    : 'hover:bg-slate-50',
                )}
                data-aspm-click="ca114903.da194167"
                data-aspm-desc="GroupChat-切换Bot发言模式"
                data-aspm-param={``}
                data-aspm-expo
              >
                <div
                  className={cn(
                    'w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0',
                    isActive ? 'bg-blue-100' : 'bg-slate-100',
                    isDisabled && 'bg-slate-100',
                  )}
                >
                  <Icon
                    className={cn(
                      'w-5 h-5',
                      isActive ? 'text-blue-500' : 'text-slate-500',
                      isDisabled && 'text-slate-400',
                    )}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'font-medium text-sm',
                        isActive ? 'text-slate-900' : 'text-slate-700',
                        isDisabled && 'text-slate-500',
                      )}
                    >
                      {config.label}
                    </span>
                    {isActive && (
                      <span className="px-1.5 py-0 text-[10px] font-medium bg-blue-100 text-blue-600 rounded">
                        当前模式
                      </span>
                    )}
                    {config.badge && (
                      <span className="px-1.5 py-0 text-[10px] font-medium bg-slate-200 text-slate-500 rounded">
                        {config.badge}
                      </span>
                    )}
                  </div>
                  <p
                    className={cn(
                      'text-xs mt-1 leading-relaxed',
                      isActive ? 'text-slate-600' : 'text-slate-500',
                      isDisabled && 'text-slate-400',
                    )}
                  >
                    {config.description}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default BotModePopover;
