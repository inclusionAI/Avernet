/**
 * BotTab - Bot Tab 组件
 *
 * 显示在顶部导航栏中的 Bot Tab，支持：
 * - 点击主体切换选中的 Bot
 * - 点击下拉按钮切换参与模式（可见性）
 */

import BotAvatar from '@/components/BotAvatar';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { BotNetworkInfo, BotVisibility } from '@/stores/botNetworkStore';
import { cn } from '@/utils/utils';
import {
  ChevronDown,
  EyeOff,
  Globe,
  Loader2,
  Lock,
  RefreshCw,
} from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { isHorizontalTextOverflowing } from '../utils/overflowTooltip';

interface BotTabProps {
  bot: BotNetworkInfo;
  isSelected: boolean;
  isToggling: boolean;
  onSetVisibility: (botUuid: string, visibility: BotVisibility) => void;
  /** 点击 Tab 主体切换 Bot */
  onBotSwitch?: (botUuid: string) => void;
  /** 点击入网开关 */
  onOnboard?: (botUuid: string, onboard: boolean) => Promise<void>;
  /** 是否正在切换入网状态 */
  isOnboarding?: boolean;
  /** 点击刷新按钮（同步名称） */
  onRefreshName?: (botUuid: string) => Promise<void>;
  /** 是否正在刷新名称 */
  isRefreshingName?: boolean;
  /** 用户头像 URL（当 bot 为 human 类型时） */
  userAvatarUrl?: string;
  /** 点击初始化 Human Actor */
  onInitHuman?: (botUuid: string) => Promise<void>;
  /** 是否正在初始化 Human */
  isInitingHuman?: boolean;
}

const COMMUNICATION_WARNING_TEXT =
  'Bot 与协作网络连接异常，请稍后重试或检查 Bot 状态';

/**
 * 根据可见性获取颜色、图标和样式
 */
export const getVisibilityStyle = (visibility: BotVisibility) => {
  switch (visibility) {
    case 'public':
      return {
        color: 'bg-green-500',
        textColor: 'text-green-500',
        icon: Globe,
        label: '公开',
        description: '可以被发现，加好友不需要确认',
      };
    case 'protected':
      return {
        color: 'bg-blue-500',
        textColor: 'text-blue-500',
        icon: Lock,
        label: '受保护',
        description: '可以被发现，加好友要确认',
      };
    case 'private':
      return {
        color: 'bg-slate-400',
        textColor: 'text-slate-400',
        icon: EyeOff,
        label: '隐身',
        description: '仅注册，不可被发现，不可加好友',
      };
    case 'offline':
      return {
        color: 'bg-red-400',
        textColor: 'text-gray-400',
        icon: EyeOff,
        label: '未入网',
        description: '历史 Bot 数据，尚未注册到协作网络',
      };
    default:
      return {
        color: 'bg-slate-400',
        textColor: 'text-slate-400',
        icon: EyeOff,
        label: '隐身',
        description: '仅注册，不可被发现，不可加好友',
      };
  }
};

const BotTab: React.FC<BotTabProps> = ({
  bot,
  isSelected,
  isToggling,
  onSetVisibility,
  onBotSwitch,
  onOnboard,
  isOnboarding,
  onRefreshName,
  isRefreshingName,
  userAvatarUrl,
  onInitHuman,
  isInitingHuman,
}) => {
  const visibilityStyle = getVisibilityStyle(bot.visibility);
  const isOffline = bot.visibility === 'offline';
  const isHidden = bot.status === 'hidden';
  const isUserTab =
    bot.actor_kind === 'human' || bot.bot_uuid.startsWith('human_');
  const isBotUnreachable =
    !isUserTab &&
    !isOffline &&
    !isHidden &&
    bot.dynamic_status?.status === 'offline';
  const isOnline = bot.status === 'online';
  const [showConfirm, setShowConfirm] = useState(false);
  const [pendingVisibility, setPendingVisibility] =
    useState<BotVisibility | null>(null);
  const botNameRef = useRef<HTMLSpanElement>(null);
  const [isNameTooltipOpen, setIsNameTooltipOpen] = useState(false);

  const handleNameTooltipOpenChange = useCallback((open: boolean) => {
    if (!open) {
      setIsNameTooltipOpen(false);
      return;
    }
    setIsNameTooltipOpen(isHorizontalTextOverflowing(botNameRef.current));
  }, []);

  useEffect(() => {
    setIsNameTooltipOpen(false);
  }, [bot.bot_name]);

  // 处理可见性切换
  const handleVisibilityClick = (newVisibility: BotVisibility) => {
    // 从公开/受保护切换到隐身时需要二次确认
    if (newVisibility === 'private' && bot.visibility !== 'private') {
      setPendingVisibility(newVisibility);
      setShowConfirm(true);
    } else {
      onSetVisibility(bot.bot_uuid, newVisibility);
    }
  };

  // 处理入网开关
  const handleOnboard = async (onboard: boolean) => {
    if (onOnboard) {
      await onOnboard(bot.bot_uuid, onboard);
    }
  };

  // 确认切换到隐身模式
  const handleConfirm = () => {
    if (pendingVisibility) {
      onSetVisibility(bot.bot_uuid, pendingVisibility);
    }
    setShowConfirm(false);
    setPendingVisibility(null);
  };

  const statusDot = (
    <div
      className={cn(
        'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full',
        isOffline
          ? 'bg-red-400'
          : isHidden
          ? 'bg-slate-400'
          : isBotUnreachable
          ? 'bg-amber-400'
          : isOnline
          ? 'bg-green-500'
          : 'bg-slate-400',
      )}
      aria-label={isBotUnreachable ? COMMUNICATION_WARNING_TEXT : undefined}
    />
  );

  return (
    <div
      className={cn(
        'relative flex items-center gap-2 px-3 h-[52px] rounded-t-lg transition-colors flex-shrink-0',
        isSelected
          ? 'bg-white text-slate-800 border border-b-0 border-slate-200 shadow-sm'
          : 'text-slate-500 hover:bg-slate-200/60 hover:text-slate-700',
      )}
    >
      {isSelected && (
        <div className="absolute top-0 inset-x-0 h-[3px] bg-lavender-600 rounded-tl-lg rounded-tr-lg z-10" />
      )}
      {/* 主体区域：点击切换 Bot */}
      <button
        type="button"
        onClick={() => onBotSwitch?.(bot.bot_uuid)}
        className="flex items-center gap-2 flex-1 min-w-0"
        data-aspm-click="ca114903.da194161"
        data-aspm-desc="GroupChat-切换Bot"
        data-aspm-param={``}
        data-aspm-expo
      >
        {/* 头像 + 可见性状态呼吸灯 */}
        <div className={cn('relative', !isSelected && 'opacity-70')}>
          <BotAvatar
            type={bot.actor_kind === 'human' ? 'user' : 'expert'}
            size="sm"
            name={bot.bot_name}
            botId={bot.bot_uuid.split(':')[0]}
            avatarUrl={
              bot.actor_kind === 'human' ? userAvatarUrl : bot.avatar_url
            }
          />
          {/* 在线状态呼吸灯在头像右下角（用户头像不显示状态图标） */}
          {!isUserTab && (
            <div className="relative">
              {isBotUnreachable ? (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>{statusDot}</TooltipTrigger>
                    <TooltipContent side="top" className="text-xs">
                      {COMMUNICATION_WARNING_TEXT}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : (
                statusDot
              )}
              {/* 在线状态显示闪烁效果 */}
              {!isOffline && !isBotUnreachable && isOnline && (
                <div
                  className={cn(
                    'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full animate-ping',
                    'bg-green-500 opacity-75',
                  )}
                />
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <TooltipProvider delayDuration={200}>
            <Tooltip
              open={isNameTooltipOpen}
              onOpenChange={handleNameTooltipOpenChange}
            >
              <TooltipTrigger asChild>
                <span
                  ref={botNameRef}
                  className="text-sm font-medium truncate max-w-[80px]"
                >
                  {bot.bot_name}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[240px] text-xs">
                {bot.bot_name}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <span
            className={cn(
              'px-1 py-0 text-[10px] font-medium rounded flex-shrink-0',
              bot.actor_kind === 'human'
                ? 'bg-blue-100 text-blue-700'
                : 'bg-lavender-100 text-lavender-700',
            )}
          >
            {bot.actor_kind === 'human' ? '用户' : 'Bot'}
          </span>
        </div>
        {/* 名称不一致时显示刷新按钮 */}
        {bot.needsRefresh && onRefreshName && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRefreshName(bot.bot_uuid);
                  }}
                  disabled={isRefreshingName}
                  className={cn(
                    'flex-shrink-0 p-1 rounded hover:bg-amber-100 transition-colors',
                    isRefreshingName && 'opacity-50 cursor-not-allowed',
                  )}
                  data-aspm-click="ca114903.da194162"
                  data-aspm-desc="GroupChat-同步Bot名称"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  <RefreshCw
                    className={cn(
                      'w-3.5 h-3.5 text-amber-500',
                      isRefreshingName && 'animate-spin',
                    )}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                检测到名称不一致，建议同步
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}

        {/* Human Actor 需要初始化时显示初始化按钮 */}
        {bot.needsHumanInit && onInitHuman && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onInitHuman(bot.bot_uuid);
                  }}
                  disabled={isInitingHuman}
                  className={cn(
                    'flex-shrink-0 p-1 rounded hover:bg-amber-100 transition-colors',
                    isInitingHuman && 'opacity-50 cursor-not-allowed',
                  )}
                  data-aspm-click="ca114903.da194163"
                  data-aspm-desc="GroupChat-初始化HumanActor"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  <RefreshCw
                    className={cn(
                      'w-3.5 h-3.5 text-amber-500',
                      isInitingHuman && 'animate-spin',
                    )}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs max-w-[200px]">
                加入后，可控制自己的Bot在BCN网络的行为，并以独立用户身份加入协作群
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </button>

      {/* 许可证信息 - 切换按钮左边 */}
      {/* <LicenceInfo bot={{
        ...bot,
        bot_id: bot.bot_uuid,
      }} className="flex-shrink-0" /> */}

      {/* 可见性状态 Tag（仅 offline 且非 human 时显示，点击切换） */}
      {bot.visibility === 'offline' && bot.actor_kind !== 'human' && (
        <Popover>
          <PopoverTrigger asChild>
            <button
              type="button"
              disabled={isToggling}
              className={cn(
                'flex items-center gap-1 px-2 py-0.5 rounded text-xs transition-colors',
                'hover:bg-white/50',
                visibilityStyle.textColor,
                'bg-white/30',
              )}
            >
              {isToggling ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <ChevronDown className="w-3 h-3" />
              )}
              <span>{visibilityStyle.label}</span>
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            sideOffset={4}
            className="w-[240px] p-2"
          >
            <div className="space-y-1">
              {/* offline 状态显示入网开关 */}
              {bot.visibility === 'offline' ? (
                <>
                  <div className="text-xs text-slate-500 px-2 py-1">
                    入网设置
                  </div>
                  <div className="px-2 py-3">
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <div className="text-sm font-medium text-slate-700">
                          加入协作网络
                        </div>
                        <p className="text-xs text-slate-400 mt-0.5">
                          注册到 BCN 后可参与群聊协作
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleOnboard(true)}
                        disabled={isOnboarding}
                        className={cn(
                          'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                          'bg-slate-200 hover:bg-lavender-200',
                          isOnboarding && 'opacity-50 cursor-not-allowed',
                        )}
                        data-aspm-click="ca114903.da194164"
                        data-aspm-desc="GroupChat-加入协作网络开关"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        <span
                          className={cn(
                            'inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform',
                            'translate-x-1',
                          )}
                        />
                      </button>
                    </div>
                  </div>
                </>
              ) : (
                false && (
                  <>
                    <div className="text-xs text-slate-500 px-2 py-1">
                      切换参与模式
                    </div>
                    {(
                      ['public', 'protected', 'private'] as BotVisibility[]
                    ).map((v) => {
                      const style = getVisibilityStyle(v);
                      const Icon = style.icon;
                      return (
                        <button
                          key={v}
                          type="button"
                          onClick={() => handleVisibilityClick(v)}
                          disabled={isToggling}
                          className={cn(
                            'w-full flex items-start gap-2 px-2 py-2 rounded-md text-sm transition-colors',
                            bot.visibility === v
                              ? 'bg-lavender-100 text-lavender-700'
                              : 'hover:bg-slate-100 text-slate-600',
                          )}
                          data-aspm-click="ca114903.da194165"
                          data-aspm-desc="GroupChat-切换可见性模式"
                          data-aspm-param={``}
                          data-aspm-expo
                        >
                          <div
                            className={cn(
                              'w-3 h-3 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
                              style.color,
                            )}
                          >
                            <Icon className="w-2 h-2 text-white" />
                          </div>
                          <div className="flex-1 min-w-0 text-left">
                            <div className="flex items-center gap-1">
                              <span className="font-medium">{style.label}</span>
                            </div>
                            <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">
                              {style.description}
                            </p>
                          </div>
                        </button>
                      );
                    })}
                  </>
                )
              )}
            </div>
          </PopoverContent>
        </Popover>
      )}

      {/* 隐身模式确认弹窗 */}
      <AlertDialog open={showConfirm} onOpenChange={setShowConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>切换到隐身模式</AlertDialogTitle>
            <AlertDialogDescription>
              Bot 切为隐身后无法参与协作，确认继续吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirm}
              data-aspm-click="ca114903.da194166"
              data-aspm-desc="GroupChat-确认切换隐身"
              data-aspm-param={``}
              data-aspm-expo
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default BotTab;
