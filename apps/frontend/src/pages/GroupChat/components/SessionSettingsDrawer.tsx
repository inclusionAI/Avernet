/**
 * SessionSettingsDrawer - 会话设置 Drawer
 *
 * - 一级面板：会话信息（只读）+ 会话成员（仅群主可增删，非群主只读）
 * - 二级面板：添加会话成员（好友 / 可协作 Bot Tab，滑入覆盖）
 *
 * 群主视角判定：driverBot.bot_uuid === group.coordinatorBot
 * 兜底：若 coordinatorBot 缺失，回退到 participants 中 role='driver' 的成员
 *
 * 会话成员增删通过 useSessionMembers：
 * - 添加 = PATCH mode='present'（后端 upsert）
 * - 移除 = PATCH mode='absent'（保留行，UI 不渲染）
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { useGroupChatStore } from '@/stores/groupChatStore';
import { useGroupSessionStore } from '@/stores/groupSessionStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { cn } from '@/utils/utils';
import { Link, Trash2, X } from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import type { GroupSession } from '../types';
import InviteLinkDialog from './InviteLinkDialog';
import AddSessionMembersPanel from './SessionSettings/AddSessionMembersPanel';
import SessionInfoCard from './SessionSettings/SessionInfoCard';
import SessionMembersSection from './SessionSettings/SessionMembersSection';

interface SessionSettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  /** 当前会话（外部传入兜底，组件内部仍从 store 读最新值） */
  session: GroupSession;
  /** 删除会话回调（仅 creator 可见） */
  onDelete?: (sessionId: string) => Promise<boolean>;
}

const SessionSettingsDrawer: React.FC<SessionSettingsDrawerProps> = ({
  open,
  onClose,
  session,
  onDelete,
}) => {
  // 从 store 读最新的 currentSession（增删后实时刷新）
  const liveSession = useGroupSessionStore((state) =>
    state.currentSession?.sessionId === session.sessionId
      ? state.currentSession
      : session,
  );
  const effectiveSession = liveSession || session;

  // 关联群信息（从 groupId 查 groups 列表）
  const group = useGroupChatStore((state) => {
    if (state.currentGroup?.id === effectiveSession.groupId) {
      return state.currentGroup;
    }
    return state.groups.find((g) => g.id === effectiveSession.groupId);
  });

  const driverBot = useBotNetworkStore((state) => state.driverBot);

  // 群主判定（与 GroupSettingsDrawer 一致）：coordinatorBot 缺失时回退到 role='driver'
  const fallbackOwner = group?.participants?.find(
    (p) => p.role === 'driver',
  )?.botUuid;
  const ownerBotUuid = group?.coordinatorBot || fallbackOwner;
  const isOwner = !!(
    driverBot?.bot_uuid &&
    ownerBotUuid &&
    driverBot.bot_uuid === ownerBotUuid
  );

  const isCreator = !!(
    driverBot?.bot_uuid &&
    effectiveSession.createdBy &&
    driverBot.bot_uuid === effectiveSession.createdBy
  );

  const isStateMachine = group?.groupStrategy === 'state_machine';

  useEffect(() => {
    if (open) {
      console.log('[SessionSettings] isCreator check', {
        driverBot: driverBot?.bot_uuid,
        createdBy: effectiveSession.createdBy,
        isCreator,
        hasOnDelete: !!onDelete,
      });
    }
  }, [
    open,
    driverBot?.bot_uuid,
    effectiveSession.createdBy,
    isCreator,
    onDelete,
  ]);

  const [showAddPanel, setShowAddPanel] = useState(false);
  const [showInviteDialog, setShowInviteDialog] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleGenerateSessionInvite = useCallback(
    async (ttlSeconds: number): Promise<string | null> => {
      try {
        const res = await BcnController.createSessionInviteLink({
          session_id: effectiveSession.sessionId,
          ttl_seconds: ttlSeconds,
        });
        if (res.error) {
          toast.error(res.error);
          return null;
        }
        return `${window.location.origin}/bcn/chat/invite/sessions/${res.invite_token}`;
      } catch (err: any) {
        toast.error(extractErrorMessage(err, '生成邀请链接失败'));
        return null;
      }
    },
    [effectiveSession.sessionId],
  );

  // 关闭 Drawer 时重置二级面板
  useEffect(() => {
    if (!open) {
      setShowAddPanel(false);
      setShowDeleteConfirm(false);
    }
  }, [open]);

  // 非群主时强制收起二级面板（防止状态切换导致的越权访问）
  useEffect(() => {
    if (!isOwner && showAddPanel) {
      setShowAddPanel(false);
    }
  }, [isOwner, showAddPanel]);

  const handleDelete = async () => {
    if (!onDelete) return;
    setIsDeleting(true);
    const success = await onDelete(effectiveSession.sessionId);
    setIsDeleting(false);
    if (success) {
      setShowDeleteConfirm(false);
      onClose();
    }
  };

  if (!open) return null;

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
                    会话管理
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    查看会话信息与成员
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onClose}
                  className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                  aria-label="关闭"
                >
                  <X className="w-5 h-5 text-slate-400" />
                </button>
              </div>

              {/* 内容 */}
              <div className="flex-1 overflow-auto p-6 space-y-4">
                <SessionInfoCard session={effectiveSession} group={group} />
                <SessionMembersSection
                  session={effectiveSession}
                  group={group}
                  isOwner={isOwner}
                  onClickAddMember={() => setShowAddPanel(true)}
                />

                {/* 分享会话（自定义协作群不支持） */}
                {!isStateMachine && (
                  <button
                    type="button"
                    onClick={() => setShowInviteDialog(true)}
                    className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-lavender-600 border border-lavender-200 rounded-lg hover:bg-lavender-50 transition-colors"
                  >
                    <Link className="w-4 h-4 flex-shrink-0" />
                    <div className="flex flex-col items-start">
                      <span>分享会话</span>
                      <span className="text-[11px] text-slate-400">
                        人类（Human）角色可以通过链接加入会话
                      </span>
                    </div>
                  </button>
                )}

                {/* 删除会话（仅 creator） */}
                {isCreator && onDelete && (
                  <button
                    type="button"
                    onClick={() => setShowDeleteConfirm(true)}
                    className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-red-500 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
                  >
                    <Trash2 className="w-4 h-4 flex-shrink-0" />
                    <div className="flex flex-col items-start">
                      <span>删除会话</span>
                      <span className="text-[11px] text-red-400">
                        此操作不可恢复，请谨慎操作
                      </span>
                    </div>
                  </button>
                )}
              </div>
            </>
          ) : (
            /* 二级面板：添加会话成员 */
            <AddSessionMembersPanel
              session={effectiveSession}
              onBack={() => setShowAddPanel(false)}
            />
          )}
        </div>
      </div>

      {/* 邀请链接弹窗 */}
      <InviteLinkDialog
        open={showInviteDialog}
        onClose={() => setShowInviteDialog(false)}
        label={effectiveSession.sessionTitle || effectiveSession.sessionId}
        onGenerate={handleGenerateSessionInvite}
      />

      {/* 删除会话确认弹窗 */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowDeleteConfirm(false)}
          />
          <div className="relative bg-white rounded-lg shadow-xl p-6 mx-4 max-w-sm">
            <h3 className="text-lg font-semibold text-slate-800 mb-2">
              删除会话
            </h3>
            <p className="text-sm text-slate-600 mb-4">
              确定要删除会话「
              {effectiveSession.sessionTitle || effectiveSession.sessionId}
              」吗？此操作不可恢复。
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800"
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
              >
                {isDeleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default SessionSettingsDrawer;
