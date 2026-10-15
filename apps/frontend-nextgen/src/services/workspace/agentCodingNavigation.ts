export interface AgentCodingChatTarget {
  botId: string;
  spaceId?: string;
  spaceName?: string;
}

export function buildAgentCodingChatPath(target: AgentCodingChatTarget): string {
  const botId = target.botId.split(':')[0];
  const params = new URLSearchParams({ botId });
  return `/coding/coding-chat?${params.toString()}`;
}
