import { compareArtifactsByCreationOrder } from '../panel/artifactDescriptor';
import type { TaskArtifactView, TaskView } from '../panel/types';
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
