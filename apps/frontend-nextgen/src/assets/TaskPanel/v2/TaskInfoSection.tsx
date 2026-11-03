// @asset-migrated: teamclaw 自研资产
/**
 * TaskInfoSection —— L1 默认折叠的「任务信息」区(Ruling 5:存量用户价值不删)。
 * 内容复用 TaskInfoTab 渲染块(目标/描述/验收/元信息)。
 */
import React from 'react';

import { TaskInfoTab } from '../TaskInfoTab';
import { C } from '../tokens';
import type { TaskView } from '../types';

export const TaskInfoSection: React.FC<{ task: TaskView }> = ({ task }) => (
  <details
    data-testid="task-info-section"
    style={{ borderTop: `1px solid ${C.border}`, padding: '0 16px' }}
  >
    <summary style={{ padding: '12px 0', cursor: 'pointer', fontSize: 14, fontWeight: 600, color: C.textPrimary }}>
      任务信息
    </summary>
    <TaskInfoTab task={task} />
  </details>
);

export default TaskInfoSection;