/** TaskPanel 协作群展示名富化：用群详情把 group_id 归一为成员名列表，避免直接展示内部 ID。 */
import type { TaskArtifactView, TaskNodeView, TaskView } from './types';

export interface GroupMemberInfo {
  names: string[];
  nameByActorId: Record<string, string>;
}

export type GroupMemberInfoByGroupId = Record<string, GroupMemberInfo>;

function isGroupNode(node: TaskNodeView | undefined): boolean {
  return (
    node?.runMode === 'coop_group' ||
    node?.runMode === 'bbs' ||
    node?.drillable === true ||
    node?.sessionId?.startsWith('bcs_grp_') === true
  );
}

export function collectTaskGroupIds(task: TaskView): string[] {
  const ids = new Set<string>();
  for (const node of task.nodes) {
    if (!isGroupNode(node)) continue;
    const id = node.groupId ?? node.assignee;
    if (id?.trim()) ids.add(id.trim());
  }
  return [...ids];
}

function joinNames(names: string[]): string | null {
  const uniqueNames = [...new Set(names.map((name) => name.trim()).filter(Boolean))];
  return uniqueNames.length ? uniqueNames.join('、') : null;
}

function memberLabel(groupId: string | null | undefined, groups: GroupMemberInfoByGroupId): string | null {
  const id = groupId?.trim();
  return id && groups[id] ? joinNames(groups[id].names) : null;
}

function actorMemberName(owner: string, groups: GroupMemberInfoByGroupId): string | null {
  for (const info of Object.values(groups)) {
    if (info.nameByActorId[owner]) return info.nameByActorId[owner];
  }
  return null;
}

function resolveArtifactOwnerLabel(
  artifact: TaskArtifactView,
  node: TaskNodeView | undefined,
  task: TaskView,
  groups: GroupMemberInfoByGroupId,
): string | null {
  const owner = artifact.owner?.trim();
  if (!owner) return null;
  const directGroup = groups[owner];
  if (directGroup) return joinNames(directGroup.names);
  const actorName = actorMemberName(owner, groups);
  if (actorName) return actorName;
  if (owner === task.ownerBotId && task.ownerBotName.trim()) return task.ownerBotName;
  if (!node) {
    return owner === task.sourceGroupId && task.mainSessionName?.trim() ? task.mainSessionName : owner;
  }
  if (isGroupNode(node)) {
    const groupLabel = memberLabel(node.groupId ?? node.assignee, groups);
    if (groupLabel) return groupLabel;
  }
  if (owner === node.assignee && node.assigneeName?.trim()) return node.assigneeName;
  if (owner === node.groupId && node.groupName?.trim() && node.groupName !== 'BCS协作群') {
    return node.groupName;
  }
  return owner;
}

/** 为步骤执行体与产物产出者补充可读 label；原始 ID 字段保持不变，供导航与下载链路使用。 */
export function enrichTaskGroupDisplay(task: TaskView, groups: GroupMemberInfoByGroupId): TaskView {
  if (!Object.keys(groups).length) return task;
  const mapNode = (node: TaskNodeView): TaskNodeView => {
    if (!isGroupNode(node)) return node;
    const label = memberLabel(node.groupId ?? node.assignee, groups);
    return label ? { ...node, executorLabel: label } : node;
  };
  const mapArtifacts = (artifacts: TaskArtifactView[], node?: TaskNodeView): TaskArtifactView[] =>
    artifacts.map((artifact) => {
      const label = resolveArtifactOwnerLabel(artifact, node, task, groups);
      return label === artifact.owner ? artifact : { ...artifact, ownerLabel: label };
    });

  return {
    ...task,
    nodes: task.nodes.map((node) => ({ ...mapNode(node), artifacts: mapArtifacts(node.artifacts, node) })),
    artifacts: mapArtifacts(task.artifacts),
  };
}
