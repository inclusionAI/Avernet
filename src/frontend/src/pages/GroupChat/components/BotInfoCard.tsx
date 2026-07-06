/**
 * BotInfoCard - Bot 信息卡片
 *
 * 展示当前 Bot 的头像、名称、许可证、协作状态
 * 右侧有设置按钮，点击展开好友设置 Popover
 */

import { useExt } from '@/capabilities';
import BotAvatar from '@/components/BotAvatar';
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
import type { Bot } from '@/services/backend-api/BotController';
import { AppExt } from '@/shell';
import { cn } from '@/utils/utils';
import { Check, ChevronDown, Copy, Settings2 } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

interface BotInfoCardProps {
  /** Bot 完整信息（用于 LicenceInfo） */
  bot?: Bot;
  /** Bot UUID */
  botUuid?: string;
  /** Bot 名称 */
  botName: string;
  /** Bot 头像 URL */
  avatarUrl?: string;
  /** 是否在线 */
  isOnline: boolean;
  /** 可见性状态 */
  visibility?: 'public' | 'protected' | 'private' | 'offline';
  /** Actor 在线状态（online/hidden），与 visibility 正交 */
  actorStatus?: 'online' | 'hidden';
  /** 是否允许被添加为好友 */
  allowAddFriend?: boolean;
  /** 添加好友是否需要确认 */
  requireConfirmation?: boolean;
  /** 更新允许被添加为好友 */
  onAllowAddFriendChange?: (value: boolean) => void;
  /** 更新是否需要确认 */
  onRequireConfirmationChange?: (value: boolean) => void;
  /** 更新协作状态（新增） */
  onStatusChange?: (status: 'online' | 'hidden') => void;
  /** 类型标识：bot | human（新增） */
  type?: 'bot' | 'human';
}

const BotInfoCard: React.FC<BotInfoCardProps> = ({
  // bot, // TODO: 许可证功能恢复后启用
  bot,
  botUuid,
  botName,
  avatarUrl,
  isOnline,
  visibility = 'private',
  actorStatus,
  allowAddFriend = true,
  requireConfirmation = true,
  onAllowAddFriendChange,
  onRequireConfirmationChange,
  onStatusChange,
  type = 'bot',
}) => {
  // Bot 画像公开（内部专属，代码不可见）：组件经 AppExt.slots.botProfilePublic 注入，开源默认 null（不渲染）。
  const { botProfilePublic: BotProfilePublicSlot } = useExt(AppExt).slots;
  const isHuman = type === 'human';
  // 桌面 Bot 不允许设置「添加好友无需确认」（强制需要确认）
  const isDesktopBot = bot?.bot_type === 'desktop';
  const [open, setOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [isUuidCopied, setIsUuidCopied] = useState(false);
  const copyResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 是否为离线/未入网状态（visibility=offline 表示未注册到 BCN）
  const isOffline = visibility === 'offline';

  // 当前协作状态：优先使用 actorStatus（后端返回的 online/hidden），
  // 回退到 visibility + isOnline 推导（兼容旧数据）
  const currentStatus: 'online' | 'hidden' =
    actorStatus ?? (isOnline && !isOffline ? 'online' : 'hidden');
  const statusText = currentStatus === 'online' ? '已开启协作' : '已暂停协作';

  // 状态配置
  const statusConfig = {
    online: {
      color: 'bg-green-500',
      textColor: 'text-green-600',
      bgColor: 'bg-green-50',
      borderColor: 'border-green-200',
      title: '已开启',
      description: ['正常参与各项协作群与任务', '实时接收协作邀请与通知'],
    },
    hidden: {
      color: 'bg-slate-400',
      textColor: 'text-slate-500',
      bgColor: 'bg-slate-50',
      borderColor: 'border-slate-200',
      title: '已暂停',
      description: [
        '暂不参与新的协作群与任务',
        '静默接收消息，不再触发提醒',
        '已有好友关系不受任何影响',
      ],
    },
  };

  const currentConfig = statusConfig[currentStatus];

  const handleStatusChange = (newStatus: 'online' | 'hidden') => {
    if (newStatus !== currentStatus) {
      onStatusChange?.(newStatus);
    }
    setStatusOpen(false);
  };

  const copyTextToClipboard = async (
    text: string,
    container: HTMLElement | null,
  ) => {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    const copyContainer = container ?? document.body;
    copyContainer.appendChild(textarea);
    textarea.select();

    const copied = document.execCommand('copy');
    copyContainer.removeChild(textarea);

    if (!copied) {
      throw new Error('Copy failed');
    }
  };

  const handleCopyBotUuid = async (
    event: React.MouseEvent<HTMLButtonElement>,
  ) => {
    event.preventDefault();
    event.stopPropagation();

    if (!botUuid) return;

    const copyContainer = event.currentTarget.closest('[data-bot-settings-id]');

    try {
      await copyTextToClipboard(botUuid, copyContainer as HTMLElement | null);
      setIsUuidCopied(true);
      toast.success('uuid 已复制');

      if (copyResetTimerRef.current) {
        clearTimeout(copyResetTimerRef.current);
      }
      copyResetTimerRef.current = setTimeout(() => {
        setIsUuidCopied(false);
      }, 1500);
    } catch {
      toast.error('复制 uuid 失败');
    }
  };

  useEffect(() => {
    return () => {
      if (copyResetTimerRef.current) {
        clearTimeout(copyResetTimerRef.current);
      }
    };
  }, []);

  return (
    <div className="flex items-center justify-between px-4 py-3 bg-white border-b border-slate-100">
      {/* 左侧：头像 + 名称 + 状态行 */}
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <div className="relative">
          <BotAvatar
            type="assistant"
            size="lg"
            name={botName}
            botId={botUuid?.split(':')[0]}
            avatarUrl={avatarUrl}
            className="w-10 h-10"
          />
        </div>
        <div className="flex flex-col flex-1 min-w-0">
          {/* 第一行：名称 (左侧) + 类型标签 (右侧) */}
          <div className="flex items-center gap-0">
            <span className="text-sm font-medium text-slate-800 truncate min-w-0 flex-1">
              {botName}
            </span>
            <span
              className={cn(
                'px-1 py-0.5 text-[10px] font-medium rounded flex-shrink-0 mr-1',
                isHuman
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-lavender-100 text-lavender-700',
              )}
            >
              {isHuman ? '用户' : 'BOT'}
            </span>
          </div>
          {/* 第二行：状态（仅 Bot 显示） + 许可证 + 设置（仅 Bot 显示许可证和设置） */}
          <div className="flex items-center gap-2 mt-0.5">
            {/* 状态切换 Popover - 仅 Bot 视角显示，用户视角隐藏 */}
            {!isHuman && (
              <Popover open={statusOpen} onOpenChange={setStatusOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={cn(
                      'flex items-center gap-1 px-1.5 py-0.5 text-xs rounded border transition-colors',
                      currentStatus === 'online'
                        ? 'bg-green-50 border-green-200 text-green-600 hover:bg-green-100'
                        : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100',
                    )}
                    title="点击切换协作状态"
                  >
                    <ChevronDown className="w-3 h-3" />
                    {statusText}
                  </button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-64 p-0">
                  <div className="px-4 py-3">
                    {/* 当前协作状态 */}
                    <div className="flex items-center gap-2 mb-3">
                      <span
                        className={cn(
                          'w-2.5 h-2.5 rounded-full',
                          currentConfig.color,
                        )}
                      />
                      <span className="text-sm font-medium text-slate-800">
                        协作状态：{currentConfig.title}
                      </span>
                    </div>
                    {/* 状态说明 */}
                    <ul className="space-y-2 mb-4">
                      {currentConfig.description.map((desc, index) => (
                        <li
                          key={index}
                          className="flex items-start gap-2 text-xs text-slate-600"
                        >
                          <span className="mt-1.5 w-1 h-1 rounded-full bg-slate-400 flex-shrink-0" />
                          {desc}
                        </li>
                      ))}
                    </ul>
                    {/* 切换按钮 */}
                    <div className="">
                      <button
                        type="button"
                        onClick={() =>
                          handleStatusChange(
                            currentStatus === 'online' ? 'hidden' : 'online',
                          )
                        }
                        className={cn(
                          'w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors',
                          currentStatus === 'online'
                            ? 'bg-slate-50 hover:bg-slate-100 text-slate-600'
                            : 'bg-green-50 hover:bg-green-100 text-green-600',
                        )}
                        data-aspm-click="ca114903.da194150"
                        data-aspm-desc="GroupChat-切换协作状态"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        {currentStatus === 'online' ? '暂停协作' : '开启协作'}
                      </button>
                    </div>
                  </div>
                </PopoverContent>
              </Popover>
            )}
            {/* 许可证信息 - 仅 Bot 显示 */}
            {/* NOTE: 先改成 botuuid，但是因为现在没搞好许可证所以显示失败 */}
            {/* {!isHuman && bot && (
              <LicenceInfo bot={{ ...bot, bot_id: botUuid || bot.bot_id }} />
            )} */}
            {/* 设置按钮 + Popover - 仅 Bot 显示 */}
            {!isHuman && (
              <Popover open={open} onOpenChange={setOpen}>
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <PopoverTrigger asChild>
                        <button
                          type="button"
                          className="flex items-center justify-center w-5 h-5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors"
                        >
                          <Settings2 className="w-3.5 h-3.5" />
                        </button>
                      </PopoverTrigger>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>Bot设置</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <PopoverContent
                  align="start"
                  className="w-80 p-0 overflow-hidden"
                >
                  {/* Popover 标题 */}
                  <div
                    className="px-4 py-3 border-b border-slate-100"
                    data-bot-settings-id
                  >
                    <div className="min-w-0">
                      <h3 className="text-sm font-medium text-slate-800">
                        Bot设置
                      </h3>
                      <div className="mt-1 flex min-w-0 items-center gap-1">
                        <span
                          className="truncate font-mono text-[11px] leading-4 text-slate-400"
                          title={botUuid || undefined}
                        >
                          {botUuid || '-'}
                        </span>
                        <TooltipProvider delayDuration={200}>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                type="button"
                                onClick={handleCopyBotUuid}
                                onPointerDown={(event) => {
                                  event.preventDefault();
                                  event.stopPropagation();
                                }}
                                disabled={!botUuid}
                                className={cn(
                                  'flex h-4 w-4 flex-shrink-0 items-center justify-center rounded text-slate-400 transition-colors',
                                  botUuid
                                    ? 'hover:bg-slate-100 hover:text-slate-600'
                                    : 'cursor-not-allowed opacity-50',
                                )}
                                aria-label="复制 Bot UUID"
                              >
                                {isUuidCopied ? (
                                  <Check className="h-3 w-3 text-green-500" />
                                ) : (
                                  <Copy className="h-3 w-3" />
                                )}
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>
                              <p>{isUuidCopied ? '已复制' : '复制 uuid'}</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    </div>
                  </div>

                  {/* 允许被添加为好友 - 开关 */}
                  <div className="px-4 py-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-slate-700">
                        允许被添加为好友
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          onAllowAddFriendChange?.(!allowAddFriend)
                        }
                        className={cn(
                          'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                          allowAddFriend ? 'bg-lavender-500' : 'bg-slate-200',
                        )}
                        data-aspm-click="ca114903.da194151"
                        data-aspm-desc="GroupChat-切换允许添加好友"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        <span
                          className={cn(
                            'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform',
                            allowAddFriend
                              ? 'translate-x-5'
                              : 'translate-x-0.5',
                          )}
                        />
                      </button>
                    </div>
                  </div>

                  {/* 好友确认选项 - 仅在允许被添加为好友时显示 */}
                  {allowAddFriend && (
                    <div className="px-4 py-3 space-y-2 pt-0">
                      {/* 需要确认 */}
                      <button
                        type="button"
                        onClick={() => onRequireConfirmationChange?.(true)}
                        className={cn(
                          'w-full flex items-start gap-3 p-3 rounded-lg border text-left transition-all',
                          requireConfirmation
                            ? 'border-lavender-200 bg-lavender-50'
                            : 'border-slate-200 hover:border-slate-300',
                        )}
                        data-aspm-click="ca114903.da194152"
                        data-aspm-desc="GroupChat-选择需要确认"
                        data-aspm-param={``}
                        data-aspm-expo
                      >
                        <div
                          className={cn(
                            'w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 mt-0.5',
                            requireConfirmation
                              ? 'border-lavender-500'
                              : 'border-slate-300',
                          )}
                        >
                          {requireConfirmation && (
                            <div className="w-2 h-2 rounded-full bg-lavender-500" />
                          )}
                        </div>
                        <div className="flex-1">
                          <div
                            className={cn(
                              'text-sm font-medium',
                              requireConfirmation
                                ? 'text-lavender-700'
                                : 'text-slate-700',
                            )}
                          >
                            添加为好友需要确认
                          </div>
                          <div className="text-xs text-slate-500 mt-0.5">
                            需经你手动审核同意才可成为好友
                          </div>
                        </div>
                      </button>

                      {/* 无需确认（桌面 Bot 不允许） */}
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={() => {
                                if (isDesktopBot) return;
                                onRequireConfirmationChange?.(false);
                              }}
                              disabled={isDesktopBot}
                              aria-disabled={isDesktopBot}
                              className={cn(
                                'w-full flex items-start gap-3 p-3 rounded-lg border text-left transition-all',
                                !requireConfirmation && !isDesktopBot
                                  ? 'border-lavender-200 bg-lavender-50'
                                  : 'border-slate-200',
                                isDesktopBot
                                  ? 'opacity-50 cursor-not-allowed'
                                  : 'hover:border-slate-300',
                              )}
                              data-aspm-click="ca114903.da194153"
                              data-aspm-desc="GroupChat-选择无需确认"
                              data-aspm-param={``}
                              data-aspm-expo
                            >
                              <div
                                className={cn(
                                  'w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 mt-0.5',
                                  !requireConfirmation && !isDesktopBot
                                    ? 'border-lavender-500'
                                    : 'border-slate-300',
                                )}
                              >
                                {!requireConfirmation && !isDesktopBot && (
                                  <div className="w-2 h-2 rounded-full bg-lavender-500" />
                                )}
                              </div>
                              <div className="flex-1">
                                <div
                                  className={cn(
                                    'text-sm font-medium',
                                    !requireConfirmation && !isDesktopBot
                                      ? 'text-lavender-700'
                                      : 'text-slate-700',
                                  )}
                                >
                                  添加为好友无需确认
                                </div>
                                <div className="text-xs text-slate-500 mt-0.5">
                                  其他成员可直接添加你为好友
                                </div>
                              </div>
                            </button>
                          </TooltipTrigger>
                          {isDesktopBot && (
                            <TooltipContent
                              side="top"
                              className="text-xs max-w-[220px]"
                            >
                              桌面 Bot 不支持「无需确认」，新好友申请需手动审核
                            </TooltipContent>
                          )}
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  )}

                  {/* 底部提示 */}
                  {allowAddFriend && (
                    <div className="px-4 py-2.5 bg-slate-50 border-t border-slate-100">
                      <div className="flex items-start gap-2">
                        <div className="w-4 h-4 rounded-full bg-slate-200 flex items-center justify-center flex-shrink-0 mt-0.5">
                          <span className="text-[10px] text-slate-500">i</span>
                        </div>
                        <p className="text-xs text-slate-500">
                          此设置仅影响新好友申请，不影响已有好友关系
                        </p>
                      </div>
                    </div>
                  )}

                  {/* Bot画像公开（内部专属，代码不可见）：开源裁掉，内部通过 slot 注入 */}
                  {BotProfilePublicSlot && (
                    <BotProfilePublicSlot botUuid={botUuid} />
                  )}
                </PopoverContent>
              </Popover>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default BotInfoCard;
