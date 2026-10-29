// @asset-migrated: teamclaw 自研资产
/** TaskPanel 任务级产物列表：统一聚合节点产物与任务级产物，供任务总览及外部宿主复用。 */
import React, { useMemo } from 'react';

import { collectTaskGroupIds, enrichTaskGroupDisplay } from '../groupDisplay';
import { useGroupMemberNames } from '../hooks/useGroupMemberNames';
import type { TaskArtifactView, TaskView } from '../types';
import { ArtifactsSection } from './ArtifactsSection';

export interface TaskArtifactsListProps {
  task: TaskView;
  apiBaseUrl: string;
  title?: string;
  previewPortalContainer?: HTMLElement | null;
  /** TaskPanelV2 已统一富化时传 false，避免同屏重复拉取群详情。 */
  enrichGroupDisplay?: boolean;
}

export function collectTaskArtifacts(task: TaskView): TaskArtifactView[] {
  const merged = new Map<string, TaskArtifactView>();
  for (const node of task.nodes) {
    for (const artifact of node.artifacts) merged.set(artifact.id, artifact);
  }
  for (const artifact of task.artifacts) {
    if (!merged.has(artifact.id)) merged.set(artifact.id, artifact);
  }
  return [...merged.values()];
}

export const TaskArtifactsList: React.FC<TaskArtifactsListProps> = ({
  task,
  apiBaseUrl,
  title,
  previewPortalContainer,
  enrichGroupDisplay = true,
}) => {
  const groupIds = useMemo(() => collectTaskGroupIds(task), [task]);
  const groupMemberNames = useGroupMemberNames(enrichGroupDisplay ? groupIds : [], '');
  const displayTask = useMemo(() => enrichTaskGroupDisplay(task, groupMemberNames), [task, groupMemberNames]);
  const artifacts = useMemo(() => collectTaskArtifacts(displayTask), [displayTask]);

  return (
    <ArtifactsSection
      artifacts={artifacts}
      apiBaseUrl={apiBaseUrl}
      context={{ taskId: task.id, ownerBotId: task.ownerBotId, ownerUserId: task.ownerUserId }}
      title={title}
      previewPortalContainer={previewPortalContainer}
    />
  );
};

export default TaskArtifactsList;
