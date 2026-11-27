import type { BotTeamDetail, BotTeamNode } from '@/domain/todos/models';

export type BotTeamEditorMode = 'view' | 'edit' | 'create';

export interface BotTeamEditorState {
  mode: BotTeamEditorMode;
  botTeamId?: string;
  name: string;
  description: string;
  nodes: BotTeamNode[];
  createdBy?: string;
  updatedBy?: string;
  createdAt?: string;
  updatedAt?: string;
}

function nextNodeCode(nodes: BotTeamNode[]): string {
  const used = new Set(nodes.map((node) => node.code));
  let index = nodes.length + 1;
  while (used.has(`node_${index}`)) index += 1;
  return `node_${index}`;
}

export function emptyBotTeamNode(nodes: BotTeamNode[], botId = ''): BotTeamNode {
  const code = nextNodeCode(nodes);
  const displayIndex = nodes.length + 1;
  return {
    code,
    name: `节点 ${displayIndex}`,
    botId,
    approvalRequired: false,
    completionGate: { requiredArtifacts: [] },
  };
}

function latestRevision(detail: BotTeamDetail) {
  return (
    detail.revisions.find((revision) => revision.botTeamRevisionId === detail.botTeam.publishedRevisionId) ??
    [...detail.revisions].sort((left, right) => right.revisionNo - left.revisionNo)[0]
  );
}

export function botTeamEditorFromDetail(
  detail: BotTeamDetail,
  mode: 'view' | 'edit',
): BotTeamEditorState {
  const revision = latestRevision(detail);
  return {
    mode,
    botTeamId: detail.botTeam.botTeamId,
    name: revision?.name ?? detail.botTeam.name,
    description: revision?.description ?? detail.botTeam.description,
    nodes: revision?.nodes ?? [],
    createdBy: detail.botTeam.createdBy,
    updatedBy: detail.botTeam.updatedBy,
    createdAt: detail.botTeam.createdAt,
    updatedAt: detail.botTeam.updatedAt,
  };
}

export function formatBotTeamDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
