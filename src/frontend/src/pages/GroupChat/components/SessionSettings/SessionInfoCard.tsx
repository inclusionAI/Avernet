/**
 * SessionInfoCard - 会话设置 Drawer 顶部「会话信息」只读卡片
 *
 * 展示：标题 / 所属群 / 协作目标 / 成员数 / 创建时间 / 更新时间 /
 *       群策略 + 群主标识 / 会话状态徽标
 * 纯只读，无任何编辑入口
 */

import { Calendar, MessageSquare, RefreshCw } from 'lucide-react';
import React from 'react';
import type { GroupInfo, GroupSession } from '../../types';
import { GROUP_STRATEGY_DOT, GROUP_STRATEGY_LABEL } from './constants';

interface SessionInfoCardProps {
  session: GroupSession;
  group?: GroupInfo;
}

const formatTime = (ts?: number): string => {
  if (!ts) return '-';
  const d = new Date(ts);
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
};

const SessionInfoCard: React.FC<SessionInfoCardProps> = ({
  session,
  group,
}) => {
  const goal = group?.extra?.context || group?.extra?.goal;
  const strategy = group?.groupStrategy;
  const strategyLabel = strategy
    ? GROUP_STRATEGY_LABEL[strategy] || strategy
    : undefined;
  const strategyDot = strategy
    ? GROUP_STRATEGY_DOT[strategy] || 'bg-slate-300'
    : 'bg-slate-300';

  const ownerName = React.useMemo(() => {
    if (!group?.coordinatorBot || !group.participants) return undefined;
    const owner = group.participants.find(
      (p) =>
        p.botUuid === group.coordinatorBot || p.id === group.coordinatorBot,
    );
    return owner?.name;
  }, [group?.coordinatorBot, group?.participants]);

  return (
    <div className="rounded-xl border border-slate-200/60 bg-gradient-to-br from-lavender-50/40 via-white to-white p-4">
      {/* 卡片头 */}
      <div className="flex items-center gap-2 mb-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-lavender-100 text-lavender-500">
          <MessageSquare className="h-3.5 w-3.5" />
        </span>
        <h3 className="text-xs font-medium text-slate-500 tracking-wide">
          会话信息
        </h3>
      </div>

      {/* 标题 + 协作目标 */}
      <div className="space-y-1.5">
        <div className="text-base font-semibold text-slate-900 break-words leading-snug">
          {session.sessionTitle || '新会话'}
        </div>
        {goal && (
          <p className="text-sm text-slate-600 leading-relaxed break-words line-clamp-3">
            {goal}
          </p>
        )}
      </div>

      {/* 元信息分隔区 */}
      <div className="mt-3 pt-3 border-t border-slate-100 space-y-1.5">
        {/* 所属群 */}
        {group?.topic && (
          <div className="text-xs text-slate-500 flex items-start gap-1.5">
            <span className="text-slate-400 flex-shrink-0">所属群：</span>
            <span className="text-slate-600 truncate">{group.topic}</span>
          </div>
        )}

        {/* 群策略 + 群主 */}
        {(strategyLabel || ownerName) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {strategyLabel && (
              <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
                <span className={`h-1.5 w-1.5 rounded-full ${strategyDot}`} />
                {strategyLabel}
              </span>
            )}
            {ownerName && (
              <span className="text-xs text-slate-400">群主：{ownerName}</span>
            )}
          </div>
        )}

        {/* 成员数 */}
        <div className="text-xs text-slate-500">
          <span className="text-slate-400">成员数：</span>
          <span className="text-slate-600">{session.members.length} 位</span>
        </div>

        {/* 时间 */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {session.createdAt > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
              <Calendar className="h-3 w-3" />
              创建：{formatTime(session.createdAt)}
            </span>
          )}
          {session.updatedAt && session.updatedAt !== session.createdAt && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
              <RefreshCw className="h-3 w-3" />
              更新：{formatTime(session.updatedAt)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

export default SessionInfoCard;
