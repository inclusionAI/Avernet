/**
 * UserCollabTab - 用户协作 Tab 内容
 *
 * 三种状态：
 * 1. 未加入：图标 + 提示文案 + 加入按钮（与 BotControlTab 布局一致）
 * 2. 已加入 + Bot 视角：提示文案 + 去发言按钮（切换到用户视角）
 * 3. 已加入 + 用户视角：用户头像 + 身份信息 + 退出按钮 + Sender 输入框
 */

import BotAvatar from '@/components/BotAvatar';
import type { DriverBot } from '@/stores/botNetworkStore';
import { chatBridge } from '@/utils/chatBridge';
import { cn } from '@/utils/utils';
import type { MentionCategory, MentionItem, SenderRef } from '@aix-chat/ui';
import { Sender } from '@aix-chat/ui';
import { LogOut, UserPlus } from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { GroupInfo, ParticipantMode } from '../../types';

interface ModeConfigItem {
  label: string;
  description: string;
  icon: any;
  color: string;
  disabled?: boolean;
  badge?: string;
}

interface UserCollabTabProps {
  isCurrentUserInGroup: boolean;
  userDisplayName: string;
  userAvatarUrl: string | undefined;
  currentHumanMode: ParticipantMode | null;
  modeConfig: Record<string, ModeConfigItem>;
  driverBot: DriverBot | null;
  bots: GroupInfo['participants'];
  provider: any;
  isRequesting: boolean;
  mentionCategories: MentionCategory[];
  userId: string | undefined;
  /** 是否为会话级协作（而非群级） */
  isSessionLevel?: boolean;
  /** 是否为用户视角（human driver bot） */
  isHumanDriverBot?: boolean;
  /** 是否禁用加入协作按钮 */
  isJoinCollaborationDisabled?: boolean;
  /** 加入协作按钮文案后缀 */
  joinButtonSuffix?: string;
  /** 切换到用户视角回调 */
  onSwitchToHuman?: () => void;
  onJoinCollaboration: () => void;
  onLeaveCollaboration: () => void;
  onSendMessage: (msg: string, mentions?: string[], senderId?: string) => void;
  onAbort: () => void;
}

const UserCollabTab: React.FC<UserCollabTabProps> = ({
  isCurrentUserInGroup,
  userDisplayName,
  userAvatarUrl,
  currentHumanMode,
  modeConfig,
  driverBot,
  bots,
  provider,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  isRequesting,
  mentionCategories,
  userId,
  isSessionLevel,
  isHumanDriverBot,
  isJoinCollaborationDisabled,
  joinButtonSuffix,
  onSwitchToHuman,
  onJoinCollaboration,
  onLeaveCollaboration,
  onSendMessage,
  onAbort,
}) => {
  const senderRef = useRef<SenderRef>(null);
  const [isSenderFocused, setIsSenderFocused] = useState(false);
  const joinButtonText = `${isSessionLevel ? '加入当前会话' : '加入协作群'}${
    joinButtonSuffix || ''
  }`;

  // 将 senderRef 注册到 chatBridge，供副屏操作使用
  const setSenderRef = (ref: SenderRef | null) => {
    senderRef.current = ref;
    if (ref) {
      chatBridge.setInputRef(ref);
    }
  };

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      chatBridge.setInputRef(null);
    };
  }, []);

  // Sender 聚焦/失焦追踪（用于失焦时收起 mention 面板）
  const senderContainerRef = useRef<HTMLDivElement>(null);
  const handleSenderFocusIn = useCallback(() => setIsSenderFocused(true), []);
  const handleSenderFocusOut = useCallback((e: React.FocusEvent) => {
    // 如果焦点仍在 Sender 容器内（如点击 mention 列表项），不收起
    if (senderContainerRef.current?.contains(e.relatedTarget as Node | null)) {
      return;
    }
    setIsSenderFocused(false);
  }, []);

  // 切换到用户视角时自动聚焦输入框
  // 使用 hasAutoFocusedRef 而非跳变检测，以处理组件因渲染路径变化被卸载重建的场景
  const hasAutoFocusedRef = useRef(false);
  useEffect(() => {
    if (
      isHumanDriverBot &&
      isCurrentUserInGroup &&
      !hasAutoFocusedRef.current
    ) {
      hasAutoFocusedRef.current = true;
      requestAnimationFrame(() => {
        senderRef.current?.focus();
      });
    }
    if (!isHumanDriverBot) {
      hasAutoFocusedRef.current = false;
    }
  }, [isHumanDriverBot, isCurrentUserInGroup]);

  // Sender 提交逻辑
  const handleSenderSubmit = (
    msg: string,
    ctx: { mentions: MentionItem[] },
  ) => {
    const mentionIds = ctx.mentions.map((m) => m.id);
    let mentions: string[] | undefined;
    if (mentionIds.length > 0) {
      if (mentionIds.includes('ALL')) {
        mentions = bots.map((bot) => bot.botUuid || bot.id).filter(Boolean);
      } else {
        mentions = mentionIds;
      }
    }
    onSendMessage(
      msg,
      mentions && mentions.length > 0 ? mentions : undefined,
      userId ? `human_${userId}` : undefined,
    );
  };

  if (!isCurrentUserInGroup) {
    // 未加入：图标 + 提示文案 + 加入按钮
    return (
      <div className="flex items-center justify-between min-h-[64px]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-slate-100">
            <UserPlus className="w-4 h-4 text-slate-400" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-700 leading-tight">
              {isSessionLevel ? '未加入当前会话' : '未加入协作'}
            </p>
            <p className="text-xs text-slate-400 leading-tight mt-0.5">
              {isSessionLevel
                ? '以用户身份加入当前会话后可直接发言'
                : '以用户身份加入后可直接发送消息'}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onJoinCollaboration}
          disabled={isJoinCollaborationDisabled}
          className={cn(
            'px-3 py-1.5 text-sm font-medium text-white bg-lavender-500 rounded-lg hover:bg-lavender-600 transition-colors flex-shrink-0 whitespace-nowrap',
            isJoinCollaborationDisabled &&
              'cursor-not-allowed bg-slate-200 text-slate-400 hover:bg-slate-200',
          )}
        >
          {joinButtonText}
        </button>
      </div>
    );
  }

  // 已加入
  // Bot 视角下已加入：提示用户切换到用户视角发言
  if (!isHumanDriverBot) {
    return (
      <div className="flex items-center justify-between min-h-[64px]">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-blue-50">
            <UserPlus className="w-4 h-4 text-blue-500" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-700 leading-tight">
              {userDisplayName}
            </p>
            <p className="text-xs text-slate-400 leading-tight mt-0.5">
              当前为 Bot 视角，用户已加入{isSessionLevel ? '当前会话' : '协作'}
              后请点击右侧&ldquo;去发言&rdquo;，切换到用户视角继续发言。
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onSwitchToHuman}
          className="px-3 py-1.5 text-sm font-medium text-white bg-lavender-500 rounded-lg hover:bg-lavender-600 transition-colors flex-shrink-0"
        >
          去发言
        </button>
      </div>
    );
  }

  // 用户视角下已加入：用户头像 + 身份信息 + 退出按钮 + Sender
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BotAvatar
            type="user"
            size="sm"
            name={userDisplayName}
            avatarUrl={userAvatarUrl}
          />
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-800">
              {userDisplayName}
            </span>
            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-green-100 text-green-600 rounded">
              用户身份发言中
            </span>
            {currentHumanMode && (
              <span
                className={cn(
                  'px-1.5 py-0.5 text-[10px] font-medium rounded',
                  currentHumanMode === 'present'
                    ? 'bg-green-50 text-green-600'
                    : 'bg-slate-100 text-slate-400',
                )}
              >
                {modeConfig[currentHumanMode]?.label || currentHumanMode}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onLeaveCollaboration}
          className="flex items-center gap-1 px-2 py-1 text-sm text-red-600 border border-red-200 rounded-md hover:bg-red-50 transition-colors"
        >
          <LogOut className="w-3 h-3" />
          {isSessionLevel ? '退出当前会话' : '退出协作'}
        </button>
      </div>
      <div
        ref={senderContainerRef}
        onFocus={handleSenderFocusIn}
        onBlur={handleSenderFocusOut}
      >
        <Sender
          ref={setSenderRef}
          placeholder={
            driverBot?.visibility === 'offline'
              ? '该Bot处于离线，请先加入协作网络'
              : '输入消息...（将以您的用户身份发送到群内）'
          }
          loading={false}
          disabled={!provider || driverBot?.visibility === 'offline'}
          mention={
            isSenderFocused ? { categories: mentionCategories } : undefined
          }
          onSubmit={handleSenderSubmit}
          onCancel={onAbort}
        />
      </div>
    </div>
  );
};

export default UserCollabTab;
