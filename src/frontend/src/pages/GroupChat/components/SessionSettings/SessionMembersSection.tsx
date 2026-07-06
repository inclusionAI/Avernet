/**
 * SessionMembersSection - 会话设置 Drawer 的「会话成员」区域
 *
 * - 展示当前会话中所有成员（含 absent 旁观成员，灰色展示）
 * - 成员名/头像由 group.participants 补全
 * - 行内 X 删除（实际是 PATCH mode=absent，保留行）
 * - 群主（group.coordinatorBot）不可删除
 * - 仅群主视角下展示「+ 添加成员」入口与行内 X 删除按钮
 * - 底部「+ 添加成员」按钮触发 onClickAddMember
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
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useSessionMembers } from '@/pages/GroupChat/hooks/useSessionMembers';
import { cn } from '@/utils/utils';
import { EyeOff, Mic, MicOff, Plus, User, X } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import type { GroupInfo, GroupSession, ParticipantMode } from '../../types';

interface SessionMembersSectionProps {
  session: GroupSession;
  group?: GroupInfo;
  /** 当前用户是否为协作群群主（控制 X 与 + 显示） */
  isOwner: boolean;
  onClickAddMember: () => void;
}

interface DisplayMember {
  actorId: string;
  actorKind: 'bot' | 'human';
  name: string;
  avatar?: string;
  botUuid?: string;
  mode?: ParticipantMode;
  role?: string;
  isOwner: boolean;
  isMaster: boolean;
  isWorker: boolean;
}

const MODE_BADGE: Record<
  ParticipantMode,
  { label: string; icon: any; className: string }
> = {
  auto: { label: '自动', icon: Mic, className: 'bg-blue-50 text-blue-600' },
  muted: {
    label: '禁言',
    icon: MicOff,
    className: 'bg-slate-100 text-slate-500',
  },
  present: {
    label: '参与',
    icon: User,
    className: 'bg-green-50 text-green-600',
  },
  absent: {
    label: '旁观',
    icon: EyeOff,
    className: 'bg-slate-100 text-slate-400',
  },
};

const SessionMembersSection: React.FC<SessionMembersSectionProps> = ({
  session,
  group,
  isOwner,
  onClickAddMember,
}) => {
  const { removeSessionMember, isRemoving } = useSessionMembers();
  const [pendingRemove, setPendingRemove] = useState<DisplayMember | null>(
    null,
  );

  const ownerBotUuid = group?.coordinatorBot;
  const isManagerWorker = group?.groupStrategy === 'manager_worker';
  const isStateMachine = group?.groupStrategy === 'state_machine';
  const masterBotUuid = group?.masterBot;
  const canManageMembers = isOwner && !isStateMachine;

  // 映射会话成员，从群成员补全 name/avatar
  const displayMembers: DisplayMember[] = useMemo(() => {
    const groupParticipants = group?.participants || [];
    return session.members.map<DisplayMember>((sm) => {
      const matched = groupParticipants.find(
        (p) => p.botUuid === sm.actorId || p.id === sm.actorId,
      );
      const isOwner = ownerBotUuid
        ? matched?.botUuid === ownerBotUuid || matched?.id === ownerBotUuid
        : sm.role === 'driver';
      // 主从模式：主节点 = group.masterBot 或 role='manager';其余 Bot 视为从节点
      const actorKind = (sm.actorKind || 'bot') as 'bot' | 'human';
      const isMaster =
        isManagerWorker &&
        actorKind === 'bot' &&
        (sm.role
          ? sm.role === 'manager'
          : matched?.botUuid === masterBotUuid ||
            matched?.id === masterBotUuid ||
            matched?.role === 'manager');
      const isWorker = isManagerWorker && actorKind === 'bot' && !isMaster;
      return {
        actorId: sm.actorId,
        actorKind,
        name: sm.name || matched?.name || sm.actorId,
        avatar: matched?.avatar,
        botUuid: matched?.botUuid,
        mode: sm.mode,
        role: sm.role,
        isOwner,
        isMaster,
        isWorker,
      };
    });
  }, [
    session.members,
    group?.participants,
    ownerBotUuid,
    isManagerWorker,
    masterBotUuid,
  ]);

  const handleConfirmRemove = async () => {
    if (!pendingRemove) return;
    const ok = await removeSessionMember(
      session.sessionId,
      pendingRemove.actorId,
    );
    if (ok) setPendingRemove(null);
  };

  return (
    <div className="rounded-xl border border-slate-200/60 bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-800">会话成员</h3>
        <span className="text-xs text-slate-400">
          共 {displayMembers.length} 位
        </span>
      </div>

      <div className="space-y-1.5">
        {displayMembers.map((m) => {
          const modeConfig = m.mode ? MODE_BADGE[m.mode] : undefined;
          const ModeIcon = modeConfig?.icon;
          return (
            <div
              key={m.actorId}
              className={cn(
                'flex items-center gap-2.5 px-2.5 py-2 rounded-lg',
                m.mode === 'absent'
                  ? 'bg-slate-50/30 opacity-50'
                  : 'bg-slate-50/60',
              )}
            >
              <BotAvatar
                type="assistant"
                size="sm"
                name={m.name}
                botId={m.botUuid?.split(':')[0]}
                avatarUrl={m.avatar}
              />
              <div className="flex-1 min-w-0 flex items-center gap-1.5 flex-wrap">
                <span className="text-sm font-medium text-slate-800 truncate max-w-[120px]">
                  {m.name}
                </span>
                <span
                  className={cn(
                    'inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium flex-shrink-0',
                    m.actorKind === 'bot'
                      ? 'bg-blue-50 text-blue-500'
                      : 'bg-amber-50 text-amber-600',
                  )}
                >
                  {m.actorKind === 'bot' ? 'Bot' : '用户'}
                </span>
                {m.isMaster && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-violet-50 text-violet-600 flex-shrink-0">
                    主节点
                  </span>
                )}
                {m.isWorker && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-slate-100 text-slate-500 flex-shrink-0">
                    从节点
                  </span>
                )}
                {!isManagerWorker && m.isOwner && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-lavender-50 text-lavender-600 flex-shrink-0">
                    群主
                  </span>
                )}
                {modeConfig && ModeIcon && (
                  <span
                    className={cn(
                      'inline-flex items-center gap-0.5 px-1 py-0 text-[10px] font-medium rounded',
                      modeConfig.className,
                    )}
                  >
                    <ModeIcon className="w-2.5 h-2.5" />
                    {modeConfig.label}
                  </span>
                )}
              </div>

              {canManageMembers &&
                (m.isOwner || m.isMaster ? (
                  <TooltipProvider delayDuration={100}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          disabled
                          className="p-1 rounded-md text-slate-300 cursor-not-allowed flex-shrink-0"
                          aria-label={
                            m.isMaster ? '主节点不可移除' : '群主不可移除'
                          }
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        {m.isMaster ? '主节点不可移除' : '群主不可移除'}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <button
                    type="button"
                    onClick={() => setPendingRemove(m)}
                    disabled={isRemoving}
                    className="p-1 rounded-md text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                    title="移出会话"
                  >
                    <X className="w-4 h-4" />
                  </button>
                ))}
            </div>
          );
        })}

        {canManageMembers && (
          <button
            type="button"
            onClick={onClickAddMember}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-lavender-300 text-lavender-600 hover:bg-lavender-50/60 transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
            添加成员
          </button>
        )}
      </div>

      <AlertDialog
        open={!!pendingRemove}
        onOpenChange={(open) => {
          if (!open) setPendingRemove(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>移出会话</AlertDialogTitle>
            <AlertDialogDescription>
              确定要将「{pendingRemove?.name}
              」移出当前会话吗？此操作不影响 ta 在协作群内的身份。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmRemove}
              className="bg-red-500 hover:bg-red-600"
            >
              确认移除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default SessionMembersSection;
