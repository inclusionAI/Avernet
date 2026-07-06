/**
 * GroupSettingsDrawer - 群管理 Drawer
 *
 * 升级版（spec: group-chat-settings-drawer-upgrade）：
 * - 顶部：协作群信息（只读卡片）
 * - 中部：协作群成员（群主可增/删，非群主只读）
 * - 底部：群主可见的「删除协作群」危险区域
 * - 二级面板：「添加成员」滑入覆盖
 *
 * 群主视角判定：driverBot.bot_uuid === group.coordinatorBot
 * 兜底：若 coordinatorBot 缺失，回退到 participants 中 role='driver' 的成员
 *
 * 本期成员增删仅更新本地 store，刷新页面会回到后端真实成员列表。
 */

import { useExt } from '@/capabilities';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import * as BcnController from '@/services/backend-api/BcnController';
import { AppExt } from '@/shell';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { useGroupChatStore } from '@/stores/groupChatStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { cn } from '@/utils/utils';
import { Link, Trash2, X } from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  CUSTOM_COLLAB_HUMAN_INVITE_DISABLED_TIP,
  isHumanInviteDisabledForGroupStrategy,
} from '../constants';
import type { GroupInfo } from '../types';
import AddMembersPanel from './GroupSettings/AddMembersPanel';
import GroupInfoCard from './GroupSettings/GroupInfoCard';
import GroupMembersSection from './GroupSettings/GroupMembersSection';
import InviteLinkDialog from './InviteLinkDialog';

interface GroupSettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  group: GroupInfo;
  /** 保留 props 兼容现有调用，但信息卡只读后不再使用 */
  onUpdate?: (updates: Partial<GroupInfo>) => Promise<boolean>;
  onDelete?: (groupId: string) => Promise<boolean>;
}

const GroupSettingsDrawer: React.FC<GroupSettingsDrawerProps> = ({
  open,
  onClose,
  group,
  onDelete,
}) => {
  const driverBot = useBotNetworkStore((state) => state.driverBot);
  // Bot 画像公开（内部专属，代码不可见）：组件经 AppExt.slots.groupVisibility 注入，开源默认 null（不渲染）。
  const { groupVisibility: GroupVisibilitySlot } = useExt(AppExt).slots;
  // 从 store 读最新数据（确保 add/remove/visibility 更新后实时刷新）
  const liveGroup = useGroupChatStore((state) =>
    state.currentGroup?.id === group.id
      ? state.currentGroup
      : state.groups.find((g) => g.id === group.id) || group,
  );
  const effectiveGroup = liveGroup || group;

  const [showAddPanel, setShowAddPanel] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [showInviteDialog, setShowInviteDialog] = useState(false);
  const isHumanInviteDisabled = isHumanInviteDisabledForGroupStrategy(
    effectiveGroup.groupStrategy,
  );

  const handleGenerateGroupInvite = useCallback(
    async (ttlSeconds: number): Promise<string | null> => {
      try {
        const res = await BcnController.createGroupInviteLink({
          group_id: effectiveGroup.id,
          ttl_seconds: ttlSeconds,
        });
        if (res.error) {
          toast.error(res.error);
          return null;
        }
        return `${window.location.origin}/bcn/chat/invite/groups/${res.invite_token}`;
      } catch (err: any) {
        toast.error(extractErrorMessage(err, '生成邀请链接失败'));
        return null;
      }
    },
    [effectiveGroup.id],
  );

  // 群主判定
  const fallbackOwner = effectiveGroup.participants?.find(
    (p) => p.role === 'driver',
  )?.botUuid;
  const ownerBotUuid = effectiveGroup.coordinatorBot || fallbackOwner;
  const isOwner = !!(
    driverBot?.bot_uuid &&
    ownerBotUuid &&
    driverBot.bot_uuid === ownerBotUuid
  );

  useEffect(() => {
    if (open) {
      console.log('[GroupSettings] isOwner check', {
        driverBot: driverBot?.bot_uuid,
        coordinatorBot: effectiveGroup.coordinatorBot,
        fallbackOwner,
        isOwner,
      });
    }
  }, [
    open,
    driverBot?.bot_uuid,
    effectiveGroup.coordinatorBot,
    fallbackOwner,
    isOwner,
  ]);

  // 关闭 Drawer 时重置二级面板状态
  useEffect(() => {
    if (!open) {
      setShowAddPanel(false);
      setShowDeleteConfirm(false);
    }
  }, [open]);

  const handleDelete = async () => {
    if (!onDelete) return;
    setIsDeleting(true);
    const success = await onDelete(effectiveGroup.id);
    setIsDeleting(false);
    if (success) {
      setShowDeleteConfirm(false);
      onClose();
    }
  };

  if (!open) return null;

  const inviteButton = (
    <button
      type="button"
      onClick={() => {
        if (isHumanInviteDisabled) return;
        setShowInviteDialog(true);
      }}
      disabled={isHumanInviteDisabled}
      className={cn(
        'w-full flex items-center gap-2 px-4 py-2.5 text-sm border rounded-lg transition-colors',
        isHumanInviteDisabled
          ? 'text-slate-400 border-slate-200 bg-slate-50 cursor-not-allowed pointer-events-none'
          : 'text-lavender-600 border-lavender-200 hover:bg-lavender-50',
      )}
    >
      <Link className="w-4 h-4 flex-shrink-0" />
      <div className="flex flex-col items-start">
        <span>分享群组</span>
        <span className="text-[11px] text-slate-400">
          人类（Human）角色可以通过链接加入群组
        </span>
      </div>
    </button>
  );

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] p-4">
        {/* 遮罩 */}
        <div
          className="absolute inset-0 bg-black/30 backdrop-blur-sm"
          onClick={onClose}
        />

        {/* 弹窗容器 */}
        <div className="relative w-full max-w-2xl bg-white rounded-xl shadow-xl flex flex-col max-h-[80vh] min-h-[50vh] overflow-hidden">
          {/* 一级面板 */}
          {!showAddPanel ? (
            <>
              {/* 头部 */}
              <div className="flex items-start justify-between px-6 py-4 border-b border-slate-100 flex-shrink-0">
                <div>
                  <h2 className="text-base font-semibold text-slate-800">
                    群管理
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    查看协作群基础信息与群成员
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onClose}
                  className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                >
                  <X className="w-5 h-5 text-slate-400" />
                </button>
              </div>

              {/* 内容 */}
              <div className="flex-1 overflow-auto p-6 space-y-4">
                <GroupInfoCard group={effectiveGroup} />

                <GroupMembersSection
                  group={effectiveGroup}
                  ownerBotUuid={ownerBotUuid}
                  isOwner={isOwner}
                  onClickAddMember={() => setShowAddPanel(true)}
                />

                {/* Bot 画像公开（内部专属，代码不可见）：开源裁掉，内部通过 slot 透出 */}
                {GroupVisibilitySlot && (
                  <GroupVisibilitySlot
                    group={effectiveGroup}
                    isOwner={isOwner}
                  />
                )}

                {/* 分享群组 */}
                {isHumanInviteDisabled ? (
                  <TooltipProvider delayDuration={100}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="block w-full cursor-not-allowed">
                          {inviteButton}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        {CUSTOM_COLLAB_HUMAN_INVITE_DISABLED_TIP}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  inviteButton
                )}

                {/* 删除协作群（仅群主） */}
                {isOwner && onDelete && (
                  <button
                    type="button"
                    onClick={() => setShowDeleteConfirm(true)}
                    className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-red-500 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
                  >
                    <Trash2 className="w-4 h-4 flex-shrink-0" />
                    <div className="flex flex-col items-start">
                      <span>删除协作群</span>
                      <span className="text-[11px] text-red-400">
                        此操作不可恢复，请谨慎操作
                      </span>
                    </div>
                  </button>
                )}
              </div>
            </>
          ) : (
            /* 二级面板：添加成员 */
            <AddMembersPanel
              group={effectiveGroup}
              onBack={() => setShowAddPanel(false)}
            />
          )}
        </div>
      </div>

      {/* 删除协作群确认弹窗 */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowDeleteConfirm(false)}
            data-aspm-click="ca114903.da194207"
            data-aspm-desc="GroupChat-关闭删除确认遮罩"
            data-aspm-param={``}
            data-aspm-expo
          />
          <div className="relative bg-white rounded-lg shadow-xl p-6 mx-4 max-w-sm">
            <h3 className="text-lg font-semibold text-slate-800 mb-2">
              删除协作群
            </h3>
            <p className="text-sm text-slate-600 mb-4">
              确定要删除协作群「{effectiveGroup.topic}」吗？此操作不可恢复。
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800"
                data-aspm-click="ca114903.da194208"
                data-aspm-desc="GroupChat-取消删除群组"
                data-aspm-param={``}
                data-aspm-expo
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={isDeleting}
                className={cn(
                  'px-4 py-2 text-sm text-white rounded-lg',
                  isDeleting
                    ? 'bg-red-300 cursor-not-allowed'
                    : 'bg-red-500 hover:bg-red-600',
                )}
                data-aspm-click="ca114903.da194201"
                data-aspm-desc="GroupChat-确认删除群组"
                data-aspm-param={``}
                data-aspm-expo
              >
                {isDeleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 邀请链接弹窗 */}
      <InviteLinkDialog
        open={showInviteDialog}
        onClose={() => setShowInviteDialog(false)}
        label={effectiveGroup.topic}
        onGenerate={handleGenerateGroupInvite}
      />
    </>
  );
};

export default GroupSettingsDrawer;
