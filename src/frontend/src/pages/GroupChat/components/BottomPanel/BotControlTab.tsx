/**
 * BotControlTab - Bot 控制 Tab 内容
 *
 * 展示当前模式图标 + 标题 + 描述，右侧 BotModePopover
 */

import { cn } from '@/utils/utils';
import React from 'react';
import BotModePopover, { type BotModePopoverProps } from './BotModePopover';

interface BotControlTabProps extends BotModePopoverProps {
  isCurrentUserInGroup: boolean;
}

/** 各模式的左侧展示配置 */
const MODE_DISPLAY: Record<
  string,
  { title: string; description: string; iconColor: string; iconBg: string }
> = {
  auto: {
    title: 'Bot 自主发言中',
    description: 'Bot 将根据群内语境自主决策是否回复',
    iconColor: 'text-blue-500',
    iconBg: 'bg-blue-50',
  },
  muted: {
    title: 'Bot 已禁言',
    description: 'Bot 将不会主动回复任何消息',
    iconColor: 'text-slate-400',
    iconBg: 'bg-slate-100',
  },
  assistant: {
    title: '辅助模式',
    description: 'Bot 回复需经用户审核后发送',
    iconColor: 'text-amber-500',
    iconBg: 'bg-amber-50',
  },
};

const DEFAULT_DISPLAY = MODE_DISPLAY.auto;

const BotControlTab: React.FC<BotControlTabProps> = ({
  currentBotMode,
  currentModeConfig,
  isUpdatingMode,
  onModeChange,
  modeConfig,
}) => {
  const display = MODE_DISPLAY[currentBotMode] || DEFAULT_DISPLAY;
  const Icon = currentModeConfig.icon;

  return (
    <div className="flex items-center justify-between min-h-[64px]">
      <div className="flex items-center gap-2.5">
        <div
          className={cn(
            'w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0',
            display.iconBg,
          )}
        >
          <Icon className={cn('w-4 h-4', display.iconColor)} />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-700 leading-tight">
            {display.title}
          </p>
          <p className="text-xs text-slate-400 leading-tight mt-0.5">
            {display.description}
          </p>
        </div>
      </div>
      <BotModePopover
        currentBotMode={currentBotMode}
        currentModeConfig={currentModeConfig}
        isUpdatingMode={isUpdatingMode}
        onModeChange={onModeChange}
        modeConfig={modeConfig}
      />
    </div>
  );
};

export default BotControlTab;
