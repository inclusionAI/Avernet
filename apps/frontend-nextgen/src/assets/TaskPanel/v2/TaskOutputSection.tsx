// @asset-migrated: teamclaw 自研资产
/**
 * TaskOutputSection —— 「任务产出」区:承接原产物 Tab 的根节点 output 渲染
 * (维度卡片优先,单块 Markdown 回退;长文折叠由 MarkdownCell 承担)。
 * 产物卡列表在 ArtifactsSection,V2 两者并列。
 */
import React from 'react';

import { MarkdownCell } from '../MarkdownCell';
import { C } from '../tokens';
import type { TaskOutputDimension, TaskView } from '../types';

const DimensionCard: React.FC<{ dimension: TaskOutputDimension }> = ({ dimension }) => (
  <div
    style={{
      background: C.surface,
      border: `1px solid ${C.border}`,
      borderRadius: 8,
      padding: 14,
    }}
  >
    <MarkdownCell content={dimension.content} />
  </div>
);

export const TaskOutputSection: React.FC<{ task: TaskView }> = ({ task }) => {
  const dimensions = task.rootOutputDimensions ?? [];
  const hasOutput = !!task.rootOutputRender && task.rootOutputRender.trim().length > 0;
  if (!dimensions.length && !hasOutput) return null;

  return (
    <details data-testid="task-output-section" style={{ borderTop: `1px solid ${C.border}`, padding: '0 16px' }}>
      <summary style={{ padding: '12px 0', cursor: 'pointer', fontSize: 14, fontWeight: 600, color: C.textPrimary }}>
        任务产出
      </summary>
      {dimensions.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 16 }}>
          {dimensions.map((d) => (
            <DimensionCard key={d.key} dimension={d} />
          ))}
        </div>
      ) : (
        <div
          style={{
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: 14,
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 650, color: C.textPrimary, marginBottom: 4 }}>产出内容</div>
          <MarkdownCell content={task.rootOutputRender} />
        </div>
      )}
    </details>
  );
};

export default TaskOutputSection;