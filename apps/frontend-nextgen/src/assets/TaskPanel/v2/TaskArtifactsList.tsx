// @asset-migrated: teamclaw 自研资产
/** TaskPanel 任务级产物列表：统一聚合节点产物与任务级产物，供任务总览及外部宿主复用。 */
import React, { useMemo } from 'react';

import { collectTaskGroupIds, enrichTaskGroupDisplay } from '../groupDisplay';
import { compareArtifactsByCreationOrder } from '../artifactDescriptor';
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
  const stageByNodeId = new Map(task.nodes.map((node, index) => [node.id, index + 1]));
  const withStage = (artifact: TaskArtifactView, nodeId: string | null): TaskArtifactView => {
    const stage = nodeId === null ? null : stageByNodeId.get(nodeId) ?? null;
    return {
      ...artifact,
      nodeId,
      stage,
      isFinalArtifact: artifact.isFinalArtifact === true,
    };
  };

  for (const node of task.nodes) {
    for (const artifact of node.artifacts) merged.set(artifact.id, withStage(artifact, node.id));
  }
  for (const artifact of task.artifacts) {
    const nodeId = artifact.nodeId ?? task.nodes[0]?.id ?? null;
    if (!merged.has(artifact.id)) merged.set(artifact.id, withStage(artifact, nodeId));
  }
  return [...merged.values()].sort((a, b) => {
    const stageA = a.stage ?? Number.MAX_SAFE_INTEGER;
    const stageB = b.stage ?? Number.MAX_SAFE_INTEGER;
    if (stageA !== stageB) return stageA - stageB;
    return compareArtifactsByCreationOrder(a, b);
  });
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
