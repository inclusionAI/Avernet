/**
 * BottomPanel - 群聊底部面板
 *
 * 使用双 Tab 布局（Bot控制 / 用户协作）
 * Bot控制：提示文案 + 模式切换
 * 用户协作：未加入→提示+加入按钮；已加入→Sender输入框
 */

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { DriverBot } from '@/stores/botNetworkStore';
import { cn } from '@/utils/utils';
import type { MentionCategory } from '@aix-chat/ui';
import { ConnectionBanner } from '@aix-chat/ui';
import { Loader2, Network } from 'lucide-react';
import React, { useCallback, useState } from 'react';
import type { GroupInfo, ParticipantMode } from '../../types';
import BotControlTab from './BotControlTab';
import CollabAlertDialogs from './CollabAlertDialogs';
import UserCollabTab from './UserCollabTab';

interface ModeConfigItem {
  label: string;
  description: string;
  icon: any;
  color: string;
  disabled?: boolean;
  badge?: string;
}

interface BottomPanelProps {
  group: GroupInfo;
  isMobile?: boolean;
  isCurrentUserInGroup: boolean;
  isHumanDriverBot: boolean;
  currentBotMode: ParticipantMode;
  currentModeConfig: ModeConfigItem;
  currentHumanMode: ParticipantMode | null;
  modeConfig: Record<string, ModeConfigItem>;
  isUpdatingMode: boolean;
  userDisplayName: string;
  userAvatarUrl: string | undefined;
  driverBot: DriverBot | null;
  bots: GroupInfo['participants'];
  provider: any;
  isRequesting: boolean;
  mentionCategories: MentionCategory[];
  userId: string | undefined;
  wsStatus?: string;
  retryCount?: number;
  onReconnect?: () => void;
  needsBcnInit?: boolean;
  isJoiningBcn?: boolean;
  /** 是否为任务协作群（主从模式） */
  isManagerWorker?: boolean;
  /** 当前活跃会话 ID */
  activeSessionId?: string | null;
  onModeChange: (mode: ParticipantMode) => void;
  onJoinCollaboration: () => Promise<void>;
  onLeaveCollaboration: () => Promise<void>;
  onSendMessage: (msg: string, mentions?: string[], senderId?: string) => void;
  onAbort: () => void;
  onJoinBcn?: () => void;
  /** 切换到用户视角回调 */
  onSwitchToHuman?: () => void;
}

const BottomPanel: React.FC<BottomPanelProps> = ({
  group,
  isMobile,
  isCurrentUserInGroup,
  isHumanDriverBot,
  currentBotMode,
  currentModeConfig,
  currentHumanMode,
  modeConfig,
  isUpdatingMode,
  userDisplayName,
  userAvatarUrl,
  driverBot,
  bots,
  provider,
  isRequesting,
  mentionCategories,
  userId,
  wsStatus,
  retryCount,
  onReconnect,
  needsBcnInit,
  isJoiningBcn,
  isManagerWorker,
  activeSessionId,
  onModeChange,
  onJoinCollaboration,
  onLeaveCollaboration,
  onSendMessage,
  onAbort,
  onJoinBcn,
  onSwitchToHuman,
}) => {
  const [showJoinDialog, setShowJoinDialog] = useState(false);
  const [showLeaveDialog, setShowLeaveDialog] = useState(false);
  const isStateMachine = group.groupStrategy === 'state_machine';
  const shouldShowUserCollabOnly =
    isHumanDriverBot || isManagerWorker || isStateMachine;

  const handleJoin = useCallback(async () => {
    await onJoinCollaboration();
    setShowJoinDialog(false);
    // 加入成功后自动切换到用户视角，无需再点击"去发言"
    onSwitchToHuman?.();
  }, [onJoinCollaboration, onSwitchToHuman]);

  const handleLeave = useCallback(async () => {
    await onLeaveCollaboration();
    setShowLeaveDialog(false);
  }, [onLeaveCollaboration]);

  return (
    <div
      className={cn(
        'border-t border-slate-100 bg-white',
        isMobile ? 'p-4' : 'px-5 py-3',
      )}
    >
      {/* 连接状态 - 始终渲染以维持 hasEverConnected 状态，connected 时组件内部返回 null */}
      <ConnectionBanner
        status={wsStatus || 'connected'}
        retryCount={retryCount}
        onRetry={onReconnect}
      />

      {needsBcnInit ? (
        /* BCN 未初始化蒙层 */
        <div className="flex items-center justify-between p-3">
          <div className="flex items-center gap-3">
            <Network className="w-5 h-5 text-slate-400" />
            <div>
              <span className="text-sm font-medium text-slate-800 block">
                用户尚未加入BCN协作网络
              </span>
              <p className="text-xs text-slate-500 mt-0.5">
                加入后，可控制自己的Bot在BCN网络的行为，并以独立用户身份加入协作群
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onJoinBcn}
            disabled={isJoiningBcn}
            className={cn(
              'px-4 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors flex-shrink-0',
              isJoiningBcn && 'opacity-50 cursor-not-allowed',
            )}
            data-aspm-click="ca114903.da194168"
            data-aspm-desc="GroupChat-加入BCN"
            data-aspm-param={``}
            data-aspm-expo
          >
            {isJoiningBcn ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              '加入BCN'
            )}
          </button>
        </div>
      ) : shouldShowUserCollabOnly ? (
        /* 用户视角、任务协作群或自定义协作群：仅显示用户协作，无 Tab 切换 */
        <UserCollabTab
          isCurrentUserInGroup={isCurrentUserInGroup}
          userDisplayName={userDisplayName}
          userAvatarUrl={userAvatarUrl}
          currentHumanMode={currentHumanMode}
          modeConfig={modeConfig}
          driverBot={driverBot}
          bots={bots}
          provider={provider}
          isRequesting={isRequesting}
          mentionCategories={mentionCategories}
          userId={userId}
          isSessionLevel={!!activeSessionId}
          isHumanDriverBot={isHumanDriverBot}
          isJoinCollaborationDisabled={isStateMachine}
          joinButtonSuffix={isStateMachine ? '（开发中）' : undefined}
          onSwitchToHuman={onSwitchToHuman}
          onJoinCollaboration={() => setShowJoinDialog(true)}
          onLeaveCollaboration={() => setShowLeaveDialog(true)}
          onSendMessage={onSendMessage}
          onAbort={onAbort}
        />
      ) : (
        /* 自由聊天群：双 Tab 布局 */
        <Tabs defaultValue={isCurrentUserInGroup ? 'collab' : 'bot'}>
          <TabsList className="mb-1.5 gap-0.5 bg-transparent p-0 justify-start">
            <TabsTrigger value="bot" className="px-2.5 py-1 text-sm rounded-md">
              Bot控制
            </TabsTrigger>
            <TabsTrigger
              value="collab"
              className="px-2.5 py-1 text-sm rounded-md"
            >
              用户协作
            </TabsTrigger>
          </TabsList>
          <TabsContent value="bot">
            <BotControlTab
              isCurrentUserInGroup={isCurrentUserInGroup}
              currentBotMode={currentBotMode}
              currentModeConfig={currentModeConfig}
              isUpdatingMode={isUpdatingMode}
              onModeChange={onModeChange}
              modeConfig={modeConfig}
            />
          </TabsContent>
          <TabsContent value="collab">
            <UserCollabTab
              isCurrentUserInGroup={isCurrentUserInGroup}
              userDisplayName={userDisplayName}
              userAvatarUrl={userAvatarUrl}
              currentHumanMode={currentHumanMode}
              modeConfig={modeConfig}
              driverBot={driverBot}
              bots={bots}
              provider={provider}
              isRequesting={isRequesting}
              mentionCategories={mentionCategories}
              userId={userId}
              isSessionLevel={!!activeSessionId}
              isHumanDriverBot={isHumanDriverBot}
              onSwitchToHuman={onSwitchToHuman}
              onJoinCollaboration={() => setShowJoinDialog(true)}
              onLeaveCollaboration={() => setShowLeaveDialog(true)}
              onSendMessage={onSendMessage}
              onAbort={onAbort}
            />
          </TabsContent>
        </Tabs>
      )}

      {/* 加入/退出协作弹窗 */}
      <CollabAlertDialogs
        showJoinDialog={showJoinDialog}
        setShowJoinDialog={setShowJoinDialog}
        showLeaveDialog={showLeaveDialog}
        setShowLeaveDialog={setShowLeaveDialog}
        onJoin={handleJoin}
        onLeave={handleLeave}
        isManagerWorker={isManagerWorker}
      />
    </div>
  );
};

export default BottomPanel;
