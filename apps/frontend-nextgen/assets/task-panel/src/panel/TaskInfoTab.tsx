// @asset-migrated: teamclaw 自研资产
/** 任务信息 Tab（_5）：目标 / 描述 / 验收标准 / 元信息 2 列 grid。 */
import React from 'react';
import { Empty, StatusTag } from './theme';
import { C } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskView } from './types';

const LabelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: C.textSecondary, marginBottom: 4 };
const ValueStyle: React.CSSProperties = { fontSize: 11, color: C.textPrimary, lineHeight: 1.5 };

export function formatRuntimeDuration(start?: string | null, end?: string | null, now = Date.now()): string {
  if (!start) return '—';
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : now;
  if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs < startMs) return '—';

  const totalSeconds = Math.max(1, Math.round((endMs - startMs) / 1000));
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return seconds ? `${minutes}分${seconds}秒` : `${minutes}分钟`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}小时${remainingMinutes}分钟` : `${hours}小时`;
}

const MetaItem: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <div style={{ marginBottom: 20 }}>
    <div style={LabelStyle}>{label}</div>
    <div style={ValueStyle}>
      {value === null || value === '' ? <span style={{ color: C.textMuted }}>—</span> : value}
    </div>
  </div>
);

export const TaskInfoTab: React.FC<{ task: TaskView }> = ({ task }) => {
  const taskMeta: { label: string; value?: React.ReactNode }[] = [
    { label: '任务类型', value: task.taskTypeLabel },
    { label: '来源', value: task.sourceLabel },
    { label: 'Owner Bot', value: task.ownerBotName },
    { label: '创建时间', value: task.createdAt },
    { label: '结束时间', value: task.finishedAt },
    { label: '运行时长', value: formatRuntimeDuration(task.createdAt, task.finishedAt) },
  ];
  if (task.taskType === 'workflow' && task.template) {
    taskMeta.push({ label: '关联模板', value: task.template });
  }
  if (task.parentTaskId) taskMeta.push({ label: '父任务', value: task.parentTaskId });

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <TruncatedText
          value={task.name}
          maxLength={20}
          style={{ fontSize: 14, fontWeight: 600, color: C.textPrimary }}
        />
        <StatusTag status={task.status} />
      </div>

      {task.goal && (
        <div style={{ marginBottom: 16 }}>
          <div style={LabelStyle}>任务目标</div>
          <div style={{ ...ValueStyle, lineHeight: 1.6 }}>{task.goal}</div>
        </div>
      )}

      {task.description && (
        <div style={{ marginBottom: 16 }}>
          <div style={LabelStyle}>任务描述</div>
          <TruncatedText value={task.description} maxLength={50} as="div" style={{ ...ValueStyle, lineHeight: 1.6 }} />
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <div style={{ ...LabelStyle, marginBottom: 8 }}>验收标准</div>
        {task.acceptances.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {task.acceptances.map((a, i) => (
              <li key={i} style={{ ...ValueStyle, marginBottom: 4, lineHeight: 1.6, listStyle: 'disc' }}>
                {a}
              </li>
            ))}
          </ul>
        ) : (
          <Empty description="暂无" minHeight={80} />
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 32px', marginBottom: 4 }}>
        {taskMeta.map((item) => (
          <MetaItem key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
    </div>
  );
};
