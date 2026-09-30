/**
 * GroupInfoCard - 群管理 Drawer 顶部「协作群信息」只读卡片
 *
 * 展示群名、协作目标、群类型、创建时间，纯只读，无任何编辑入口
 */

import { Calendar, Users } from 'lucide-react';
import React from 'react';
import { GROUP_STRATEGY_DOT, GROUP_STRATEGY_LABEL } from '../../constants';
import type { GroupInfo } from '../../types';

interface GroupInfoCardProps {
  group: GroupInfo;
}

const formatCreatedAt = (ts: number): string => {
  if (!ts) return '-';
  const d = new Date(ts);
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
};

const GroupInfoCard: React.FC<GroupInfoCardProps> = ({ group }) => {
  const goal = group.extra?.context || group.extra?.goal;
  const strategyLabel = group.groupStrategy
    ? GROUP_STRATEGY_LABEL[group.groupStrategy] || group.groupStrategy
    : undefined;
  const strategyDot = group.groupStrategy
    ? GROUP_STRATEGY_DOT[group.groupStrategy] || 'bg-slate-300'
    : 'bg-slate-300';
  const createdAtText = formatCreatedAt(group.createdAt);

  return (
    <div className="rounded-xl border border-slate-200/60 bg-gradient-to-br from-violet-50/40 via-white to-white p-4">
      {/* 卡片标题 */}
      <div className="flex items-center gap-2 mb-3">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-violet-100 text-violet-500">
          <Users className="h-3.5 w-3.5" />
        </span>
        <h3 className="text-xs font-medium text-slate-500 tracking-wide">
          协作群信息
        </h3>
      </div>

      {/* 主信息：群名 + 目标 */}
      <div className="space-y-1.5">
        <div className="text-base font-semibold text-slate-900 break-words leading-snug">
          {group.topic || '未命名协作群'}
        </div>
        {goal && (
          <p className="text-sm text-slate-600 leading-relaxed break-words line-clamp-3">
            {goal}
          </p>
        )}
      </div>

      {/* 元信息：类型 + 创建时间 */}
      {(strategyLabel || group.createdAt) && (
        <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          {strategyLabel && (
            <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
              <span className={`h-1.5 w-1.5 rounded-full ${strategyDot}`} />
              {strategyLabel}
            </span>
          )}
          {group.createdAt > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
              <Calendar className="h-3 w-3" />
              {createdAtText}
            </span>
          )}
        </div>
      )}
    </div>
  );
};

export default GroupInfoCard;
