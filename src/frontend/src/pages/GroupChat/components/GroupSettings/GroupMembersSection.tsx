/**
 * GroupMembersSection - 群管理 Drawer 的「协作群成员」区域
 *
 * - 展示当前群所有成员，区分群主 Bot / 成员 Bot
 * - 群主视角下：成员行右侧 X 删除按钮、底部「+ 添加成员」入口
 * - 群主自身：X 始终置灰 + tooltip 提示
 * - 删除走 shadcn AlertDialog 二次确认 → useGroupMembers.removeGroupMember(DELETE)
 * - 添加点击触发 onClickAddMember（由父组件控制二级面板）
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
import { useGroupMembers } from '@/pages/GroupChat/hooks/useGroupMembers';
import { cn } from '@/utils/utils';
import { EyeOff, Plus, X } from 'lucide-react';
import React, { useState } from 'react';
import type { GroupInfo, GroupMember } from '../../types';

interface GroupMembersSectionProps {
  group: GroupInfo;
  /** 群主 Bot UUID（用于区分群主 / 成员、控制不可删除） */
  ownerBotUuid?: string;
  /** 当前用户是否为群主（控制 X 与 + 显示） */
  isOwner: boolean;
  /** 点击 + 添加成员 */
  onClickAddMember: () => void;
}

const isUndeletableMember = (
  m: GroupMember,
  group: GroupInfo,
  ownerBotUuid?: string,
): boolean => {
  if (ownerBotUuid && (m.botUuid === ownerBotUuid || m.id === ownerBotUuid))
    return true;
  if (
    group.groupStrategy === 'manager_worker' &&
    group.masterBot &&
    (m.botUuid === group.masterBot || m.id === group.masterBot)
  )
    return true;
  return false;
};

const isManagerMember = (m: GroupMember, group: GroupInfo): boolean => {
  return (
    group.groupStrategy === 'manager_worker' &&
    !!group.masterBot &&
    (m.botUuid === group.masterBot || m.id === group.masterBot)
  );
};

const GroupMembersSection: React.FC<GroupMembersSectionProps> = ({
  group,
  ownerBotUuid,
  isOwner,
  onClickAddMember,
}) => {
  const { removeGroupMember, isRemoving } = useGroupMembers();
  const [pendingRemove, setPendingRemove] = useState<GroupMember | null>(null);

  const participants = group.participants || [];
  const isStateMachine = group.groupStrategy === 'state_machine';
  const canManageMembers = isOwner && !isStateMachine;

  const handleConfirmRemove = async () => {
    if (!pendingRemove) return;
    const botUuid = pendingRemove.botUuid || pendingRemove.id;
    const ok = await removeGroupMember(group.id, botUuid, pendingRemove.name);
    if (ok) setPendingRemove(null);
  };

  return (
    <div className="rounded-xl border border-slate-200/60 bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-800">协作群成员</h3>
        <span className="text-xs text-slate-400">
          共 {participants.length} 个成员
        </span>
      </div>

      <div className="space-y-1.5">
        {participants.map((member) => {
          const undeletable = isUndeletableMember(member, group, ownerBotUuid);
          const isManager = isManagerMember(member, group);
          const isAbsent = member.mode === 'absent';
          const canDelete = isOwner && !undeletable;
          return (
            <div
              key={member.id}
              className={cn(
                'flex items-center gap-2.5 px-2.5 py-2 rounded-lg',
                isAbsent ? 'bg-slate-50/30 opacity-50' : 'bg-slate-50/60',
              )}
            >
              <BotAvatar
                type="assistant"
                size="sm"
                name={member.name}
                botId={member.botUuid?.split(':')[0]}
                avatarUrl={member.avatar}
              />
              <div className="flex-1 min-w-0 flex items-center gap-1.5">
                <span className="text-sm font-medium text-slate-800 truncate">
                  {member.name}
                </span>
                <span
                  className={cn(
                    'inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium flex-shrink-0',
                    member.actorKind === 'human'
                      ? 'bg-amber-50 text-amber-600'
                      : 'bg-blue-50 text-blue-500',
                  )}
                >
                  {member.actorKind === 'human' ? '用户' : 'Bot'}
                </span>
                <span
                  className={cn(
                    'inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium flex-shrink-0',
                    isManager
                      ? 'bg-violet-50 text-violet-600'
                      : undeletable
                      ? 'bg-lavender-50 text-lavender-600'
                      : 'bg-slate-50 text-slate-400',
                  )}
                >
                  {isManager ? '主节点' : undeletable ? '群主 Bot' : '成员 Bot'}
                </span>
                {isAbsent && (
                  <span className="inline-flex items-center gap-0.5 px-1 py-0 text-[10px] font-medium rounded bg-slate-100 text-slate-400 flex-shrink-0">
                    <EyeOff className="w-2.5 h-2.5" />
                    旁观
                  </span>
                )}
              </div>

              {canManageMembers &&
                (undeletable ? (
                  <TooltipProvider delayDuration={100}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          disabled
                          className="p-1 rounded-md text-slate-300 cursor-not-allowed flex-shrink-0"
                          aria-label={
                            isManager ? '主节点不可删除' : '群主 Bot 不可删除'
                          }
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        {isManager ? '主节点不可删除' : '群主 Bot 不可删除'}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <button
                    type="button"
                    onClick={() => setPendingRemove(member)}
                    disabled={!canDelete || isRemoving}
                    className="p-1 rounded-md text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                    title="移出协作群"
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
            <AlertDialogTitle>移出协作群</AlertDialogTitle>
            <AlertDialogDescription>
              确定要将「{pendingRemove?.name}」移出协作群吗？此操作不可恢复。
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

export default GroupMembersSection;
