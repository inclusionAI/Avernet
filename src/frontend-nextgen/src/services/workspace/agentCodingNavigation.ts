export interface AgentCodingChatTarget {
  botId: string;
  spaceId?: string;
  spaceName?: string;
}

export function buildAgentCodingChatPath(target: AgentCodingChatTarget): string {
  const params = new URLSearchParams({ botId: target.botId });
  if (target.spaceId) params.set('space_id', target.spaceId);
  if (target.spaceName) params.set('space_name', target.spaceName);
  return `/coding/coding-chat?${params.toString()}`;
}
