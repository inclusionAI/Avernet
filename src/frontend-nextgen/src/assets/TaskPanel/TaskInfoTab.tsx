// @asset-migrated: teamclaw 自研资产
/** 任务信息 Tab（_5）：目标 / 描述 / 验收标准 / 元信息 2 列 grid。 */
import React from 'react';
import { Empty, StatusTag } from './theme';
import { C } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskView } from './types';

const LabelStyle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: C.textSecondary, marginBottom: 4 };
const ValueStyle: React.CSSProperties = { fontSize: 14, color: C.textPrimary, lineHeight: 1.6 };

const MetaItem: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <div style={{ marginBottom: 20 }}>
    <div style={LabelStyle}>{label}</div>
    <div style={ValueStyle}>
      {value === null || value === '' ? <span style={{ color: C.textMuted }}>—</span> : value}
    </div>
  </div>
);

export const TaskInfoTab: React.FC<{ task: TaskView }> = ({ task }) => {
  const meta: { label: string; value?: React.ReactNode }[] = [{ label: '任务类型', value: task.taskTypeLabel }];
  if (task.taskType === 'workflow' && task.template) {
    meta.push({ label: '关联模板', value: task.template });
  }
  meta.push({ label: '来源', value: task.sourceLabel });
  meta.push({ label: 'Owner Bot', value: task.ownerBotName });
  meta.push({ label: '创建时间', value: task.createdAt });
  if (task.finishedAt) meta.push({ label: '完成时间', value: task.finishedAt });
  if (task.mainSessionName) meta.push({ label: '发起会话', value: task.mainSessionName });
  if (task.parentTaskId) meta.push({ label: '父任务', value: task.parentTaskId });

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <TruncatedText
          value={task.name}
          maxLength={20}
          style={{ fontSize: 16, fontWeight: 600, color: C.textPrimary }}
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

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 32px' }}>
        {meta
          .filter((m) => m.value && m.value !== '—')
          .map((m, i) => (
            <MetaItem key={i} label={m.label} value={m.value} />
          ))}
      </div>
    </div>
  );
};
