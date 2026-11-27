import { Button, Card, Empty } from '@/components/ui';
import type { CreateTodoProductInteractionInput, TodoProductInteraction, TodoStatus } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import { ChevronDown, ChevronRight, User } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { ConversationReply } from './product-interaction/ConversationReply';
import { InteractionMessage } from './product-interaction/InteractionMessage';
import {
  buildInteractionConversations,
  formatInteractionTime,
  interactionMessageCount,
} from './product-interaction/interactionConversation';

interface TodoProductInteractionPanelProps {
  todoId: string;
  interactions: TodoProductInteraction[];
  canSend: boolean;
  blockingInteractionId?: string;
  todoStatus: TodoStatus;
  loading: boolean;
  error: string | null;
  humanName: string;
  botName: string;
  onCancel: (interactionId: string) => Promise<boolean> | boolean;
  onRetry: () => Promise<void> | void;
  onSubmit: (input: CreateTodoProductInteractionInput) => Promise<boolean> | boolean;
}

export function TodoProductInteractionPanel({
  todoId,
  interactions,
  canSend,
  blockingInteractionId,
  todoStatus,
  loading,
  error,
  humanName,
  botName,
  onCancel,
  onRetry,
  onSubmit,
}: TodoProductInteractionPanelProps) {
  const [replyMessage, setReplyMessage] = useState('');
  const [collapsedByConversation, setCollapsedByConversation] = useState<Record<string, boolean>>({});
  const conversations = useMemo(() => buildInteractionConversations(interactions), [interactions]);
  const latestConversation = conversations.at(-1);
  const latestInteraction = latestConversation?.interactions.at(-1);
  const sendDisabled = loading || !canSend || !canInteractWithTodo(todoStatus);

  useEffect(() => {
    setReplyMessage('');
    setCollapsedByConversation({});
  }, [todoId]);

  const submitReply = async () => {
    const text = replyMessage.trim();
    if (!text || !latestInteraction || sendDisabled) return;
    const accepted = await onSubmit({ message: text, replyToInteractionId: latestInteraction.interactionId });
    if (accepted) setReplyMessage('');
  };

  return (
    <section role="region" aria-label="Product 待办互动" className="flex flex-col gap-3">
      {conversations.length ? (
        <ol className="m-0 list-none space-y-3 p-0">
          {conversations.map((conversation, conversationIndex) => {
            const firstInteraction = conversation.interactions[0];
            const isLatest = conversationIndex === conversations.length - 1;
            const collapsed = collapsedByConversation[conversation.conversationId] ?? !isLatest;
            return (
              <li key={conversation.conversationId}>
                <Card className="overflow-hidden rounded-xl border-border bg-background shadow-none">
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-auto w-full justify-start gap-2 rounded-none px-3 py-2.5 text-left hover:bg-muted/30"
                    aria-expanded={!collapsed}
                    onClick={() =>
                      setCollapsedByConversation((current) => ({
                        ...current,
                        [conversation.conversationId]: !collapsed,
                      }))
                    }
                  >
                    {collapsed ? (
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    ) : (
                      <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    {collapsed ? (
                      <>
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-foreground">
                          <User className="size-3.5" aria-hidden />
                        </span>
                        <span className="shrink-0 text-sm font-medium text-foreground">{firstInteraction.actorName || humanName}</span>
                        <span className="truncate text-sm text-muted-foreground">{firstInteraction.requestMessage || '互动'}</span>
                      </>
                    ) : (
                      <span className="truncate text-xs text-muted-foreground">{formatInteractionTime(firstInteraction.createdAt)} 开始</span>
                    )}
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">{interactionMessageCount(conversation)} 条</span>
                  </Button>

                  {!collapsed ? (
                    <div className="px-3 pb-3">
                      <div className="space-y-4">
                        {conversation.interactions.map((interaction) => (
                          <InteractionMessage
                            key={interaction.interactionId}
                            interaction={interaction}
                            blocking={interaction.kind === 'human_message' && interaction.interactionId === blockingInteractionId}
                            loading={loading}
                            humanName={humanName}
                            botName={botName}
                            onCancel={onCancel}
                          />
                        ))}
                      </div>
                      {isLatest && latestInteraction ? (
                        <ConversationReply
                          value={replyMessage}
                          disabled={sendDisabled}
                          loading={loading}
                          onChange={setReplyMessage}
                          onSend={() => void submitReply()}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </Card>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="rounded-xl border border-dashed border-border py-10">
          <Empty title="暂无互动" description="在下方评论后开始第一块互动。" compact />
        </div>
      )}

      {error ? (
        <p className="m-0 mt-4 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-2 h-auto p-0 font-semibold text-destructive underline hover:bg-transparent hover:text-destructive"
            onClick={() => void onRetry()}
          >
            重试
          </Button>
        </p>
      ) : null}
    </section>
  );
}

export { TodoProductInteractionComposer } from './TodoProductInteractionComposer';
