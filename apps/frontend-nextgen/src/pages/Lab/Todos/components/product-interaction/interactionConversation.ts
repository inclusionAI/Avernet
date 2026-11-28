import type { TodoProductInteraction, TodoProductInteractionDeliveryState } from '@/domain/todos/models';

export const INTERACTION_STATE_LABELS: Record<TodoProductInteractionDeliveryState, string> = {
  sending: '发送中',
  delivery_unknown: '结果确认中',
  succeeded: '已返回',
  failed: '发送失败',
  cancel_requested: '停止请求已提交',
  cancelled: '已确认停止',
};

export const INTERACTION_STATE_CLASSES: Record<TodoProductInteractionDeliveryState, string> = {
  sending: 'border-info/30 bg-info/5 text-info',
  delivery_unknown: 'border-warning/40 bg-warning/10 text-warning',
  succeeded: 'border-success/30 bg-success/10 text-success',
  failed: 'border-destructive/30 bg-destructive/5 text-destructive',
  cancel_requested: 'border-warning/40 bg-warning/10 text-warning',
  cancelled: 'border-border bg-muted/50 text-muted-foreground',
};

export interface InteractionConversation {
  conversationId: string;
  interactions: TodoProductInteraction[];
}

export function formatInteractionTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function buildInteractionConversations(interactions: TodoProductInteraction[]): InteractionConversation[] {
  const interactionsById = new Map(interactions.map((interaction) => [interaction.interactionId, interaction]));
  const conversationIdByInteractionId = new Map<string, string>();

  const resolveConversationId = (interaction: TodoProductInteraction): string => {
    const cached = conversationIdByInteractionId.get(interaction.interactionId);
    if (cached) return cached;

    const path: string[] = [];
    const visited = new Set<string>();
    let current = interaction;
    while (current.targetInteractionId) {
      if (visited.has(current.interactionId)) break;
      visited.add(current.interactionId);
      path.push(current.interactionId);
      const target = interactionsById.get(current.targetInteractionId);
      if (!target) break;
      current = target;
    }

    const conversationId = current.interactionId;
    conversationIdByInteractionId.set(conversationId, conversationId);
    path.forEach((interactionId) => conversationIdByInteractionId.set(interactionId, conversationId));
    return conversationId;
  };

  const conversationsById = new Map<string, InteractionConversation>();
  interactions.forEach((interaction) => {
    const conversationId = resolveConversationId(interaction);
    const conversation = conversationsById.get(conversationId);
    if (conversation) conversation.interactions.push(interaction);
    else conversationsById.set(conversationId, { conversationId, interactions: [interaction] });
  });
  return [...conversationsById.values()];
}

export function interactionMessageCount(conversation: InteractionConversation): number {
  return conversation.interactions.reduce((count, interaction) => count + 1 + (interaction.response ? 1 : 0), 0);
}
