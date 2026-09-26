// @asset-migrated: teamclaw 自研资产
/** 任务副屏摘要头：统一主任务与子任务的标题、状态、进度和上下文信息。 */
import React from 'react';
import { StatusTag } from './theme';
import { C, TASK_STATUS_TONES } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskView } from './types';

const MetaPill: React.FC<{ label: string; value: string; tone?: string }> = ({ label, value, tone }) => (
  <span
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      padding: '4px 8px',
      border: `1px solid ${C.border}`,
      borderRadius: 6,
      background: C.surface,
      color: C.textSecondary,
      fontSize: 10,
    }}
  >
    <span>{label}</span>
    <strong style={{ color: tone ?? C.textPrimary, fontWeight: 650 }}>{value}</strong>
  </span>
);

export const TaskPanelHeader: React.FC<{ task: TaskView; compact?: boolean }> = ({ task, compact = false }) => {
  const tone = TASK_STATUS_TONES[task.status] ?? TASK_STATUS_TONES.DRAFTING;
  const percent = Math.min(Math.max(task.progress?.percent ?? 0, 0), 100);
  const meta = [task.taskTypeLabel, task.sourceLabel, task.ownerBotName].filter(Boolean);

  return (
    <div
      style={{
        padding: compact ? '12px 14px' : '16px 18px',
        borderBottom: `1px solid ${C.border}`,
        background: `linear-gradient(135deg, ${C.surface} 0%, ${C.surfaceRaised} 100%)`,
        flexShrink: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: tone.color,
                boxShadow: `0 0 0 4px ${tone.bg}`,
                flexShrink: 0,
              }}
            />
            <TruncatedText
              value={task.name || '未命名任务'}
              maxLength={20}
              as="h1"
              style={{
                margin: 0,
                color: C.textPrimary,
                fontSize: compact ? 14 : 16,
                fontWeight: 650,
                lineHeight: 1.35,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            />
          </div>
          {meta.length > 0 && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                marginTop: 7,
                color: C.textSecondary,
                fontSize: 11,
              }}
            >
              {meta.map((item, index) => (
                <React.Fragment key={`${item}-${index}`}>
                  {index > 0 && <span style={{ color: C.textMuted }}>·</span>}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item}</span>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
        <StatusTag status={task.status} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14 }}>
        <div style={{ flex: 1, height: 7, borderRadius: 99, background: C.surfaceAlt, overflow: 'hidden' }}>
          <div
            style={{
              width: `${percent}%`,
              height: '100%',
              borderRadius: 99,
              background: `linear-gradient(90deg, ${tone.color} 0%, ${tone.color}CC 100%)`,
              transition: 'width 300ms ease-out',
            }}
          />
        </div>
        <span style={{ color: C.textSecondary, fontSize: 11, whiteSpace: 'nowrap' }}>
          <strong style={{ color: C.textPrimary, fontWeight: 650 }}>{Math.round(percent)}%</strong>
          <span style={{ color: C.textMuted }}> · </span>
          {task.progress?.done ?? 0}/{task.progress?.total ?? 0} 已完成
        </span>
      </div>

      {!compact && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
          <MetaPill label="执行轮次" value={String(task.loopRound ?? 0)} />
          <MetaPill label="运行中" value={String(task.progress?.running ?? 0)} tone={C.warning} />
          <MetaPill
            label="失败"
            value={String(task.progress?.failed ?? 0)}
            tone={task.progress?.failed ? C.danger : undefined}
          />
          {task.needsAttention && <MetaPill label="需要关注" value="请查看" tone={C.warning} />}
        </div>
      )}
    </div>
  );
};
