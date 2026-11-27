import { Badge, Button, Card, Empty, Textarea } from '@/components/ui';
import type {
  CreateTodoProductInteractionInput,
  TodoProductInteraction,
  TodoProductInteractionDeliveryState,
  TodoStatus,
} from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import {
  Bot,
  ChevronDown,
  ChevronRight,
  CircleStop,
  LoaderCircle,
  Send,
  User,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const STATE_LABELS: Record<TodoProductInteractionDeliveryState, string> = {
  sending: '发送中',
  delivery_unknown: '结果确认中',
  succeeded: '已返回',
  failed: '发送失败',
  cancel_requested: '停止请求已提交',
  cancelled: '已确认停止',
};

const STATE_CLASSES: Record<TodoProductInteractionDeliveryState, string> = {
  sending: 'border-info/30 bg-info/5 text-info',
  delivery_unknown: 'border-warning/40 bg-warning/10 text-warning',
  succeeded: 'border-success/30 bg-success/10 text-success',
  failed: 'border-destructive/30 bg-destructive/5 text-destructive',
  cancel_requested: 'border-warning/40 bg-warning/10 text-warning',
  cancelled: 'border-border bg-muted/50 text-muted-foreground',
};

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

interface InteractionConversation {
  conversationId: string;
  interactions: TodoProductInteraction[];
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function buildConversations(interactions: TodoProductInteraction[]): InteractionConversation[] {
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
    if (conversation) {
      conversation.interactions.push(interaction);
      return;
    }
    conversationsById.set(conversationId, { conversationId, interactions: [interaction] });
  });

  return [...conversationsById.values()];
}

function interactionMessageCount(conversation: InteractionConversation): number {
  return conversation.interactions.reduce((count, interaction) => count + 1 + (interaction.response ? 1 : 0), 0);
}

interface InteractionMessageProps {
  interaction: TodoProductInteraction;
  blocking: boolean;
  loading: boolean;
  humanName: string;
  botName: string;
  onCancel: (interactionId: string) => Promise<boolean> | boolean;
}

function InteractionMessage({
  interaction,
  blocking,
  loading,
  humanName,
  botName,
  onCancel,
}: InteractionMessageProps) {
  const isStopRequest = interaction.kind === 'stop_request';

  return (
    <div className="space-y-3">
      <div className="group flex items-start gap-2">
        <span
          className={cn(
            'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full',
            isStopRequest ? 'bg-warning/10 text-warning' : 'bg-muted text-foreground',
          )}
        >
          {isStopRequest ? <CircleStop className="size-3.5" aria-hidden /> : <User className="size-3.5" aria-hidden />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-foreground">{interaction.actorName || humanName}</span>
            {isStopRequest ? (
              <Badge className="border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
                停止请求
              </Badge>
            ) : null}
            <Badge className={cn('border px-1.5 py-0.5 text-[10px]', STATE_CLASSES[interaction.deliveryState])}>
              {STATE_LABELS[interaction.deliveryState]}
            </Badge>
            <time className="text-xs text-muted-foreground/70">{formatTime(interaction.createdAt)}</time>
          </div>
          <p className="m-0 mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/85">
            {interaction.requestMessage || (isStopRequest ? '请求停止当前处理。' : '')}
          </p>
          {interaction.failureCode ? (
            <p className="m-0 mt-1 text-xs text-destructive">失败码：{interaction.failureCode}</p>
          ) : null}
        </div>
      </div>

      {interaction.response ? (
        <div className="flex items-start gap-2">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Bot className="size-3.5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-medium text-foreground">{botName}</span>
              <Badge className="border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                助手
              </Badge>
              <Badge className="border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {interaction.response.type}
              </Badge>
            </div>
            <p className="m-0 mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/85">
              {interaction.response.message || '平台已接收处理。'}
            </p>
          </div>
        </div>
      ) : null}

      {blocking ? (
        <div className="flex items-start gap-2">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Bot className="size-3.5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-medium text-foreground">{botName}</span>
              <Badge className="inline-flex items-center gap-1 border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                <LoaderCircle className="size-2.5 animate-spin" aria-hidden />
                执行中
              </Badge>
              <Button
                type="button"
                variant="outline"
                className="h-6 rounded-md border-destructive px-2 text-[11px] text-destructive hover:bg-destructive hover:text-destructive-foreground"
                loading={loading}
                leftIcon={<CircleStop className="size-3" aria-hidden />}
                onClick={() => void onCancel(interaction.interactionId)}
              >
                终止
              </Button>
            </div>
            <p className="m-0 mt-1 text-sm leading-6 text-muted-foreground">待办助手正在处理，返回前暂不能继续发送。</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface ConversationReplyProps {
  value: string;
  disabled: boolean;
  loading: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

function ConversationReply({ value, disabled, loading, onChange, onSend }: ConversationReplyProps) {
  return (
    <div className="mt-3 border-t border-divider pt-3">
      <div
        className={cn(
          'rounded-lg border border-border bg-background px-3 py-2 focus-within:border-primary',
          disabled && 'opacity-60',
        )}
      >
        <Textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            onSend();
          }}
          disabled={disabled}
          placeholder={disabled ? '待办助手执行中，暂无法回复…' : '回复，与待办助手继续对话；Enter 发送，Shift+Enter 换行'}
          rows={2}
          className="min-h-0 resize-none border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
          aria-label="继续当前互动"
        />
        <div className="mt-1 flex justify-end">
          <Button
            type="button"
            variant="primary"
            size="icon"
            className="size-7 rounded-lg"
            loading={loading}
            disabled={disabled || !value.trim()}
            aria-label="发送回复"
            onClick={onSend}
          >
            <Send className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
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
  const conversations = useMemo(() => buildConversations(interactions), [interactions]);
  const latestConversation = conversations.at(-1);
  const latestInteraction = latestConversation?.interactions.at(-1);
  const statusAllowsInteraction = canInteractWithTodo(todoStatus);
  const sendDisabled = loading || !canSend || !statusAllowsInteraction;

  useEffect(() => {
    setReplyMessage('');
    setCollapsedByConversation({});
  }, [todoId]);

  const submitReply = async () => {
    const text = replyMessage.trim();
    if (!text || !latestInteraction || sendDisabled) return;
    const accepted = await onSubmit({
      message: text,
      replyToInteractionId: latestInteraction.interactionId,
    });
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
            const messageCount = interactionMessageCount(conversation);

            return (
              <li key={conversation.conversationId}>
                <Card className="overflow-hidden rounded-xl border-border bg-background shadow-none">
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
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
                        <span className="shrink-0 text-sm font-medium text-foreground">
                          {firstInteraction.actorName || humanName}
                        </span>
                        <span className="truncate text-sm text-muted-foreground">
                          {firstInteraction.requestMessage || '互动'}
                        </span>
                      </>
                    ) : (
                      <span className="truncate text-xs text-muted-foreground">
                        {formatTime(firstInteraction.createdAt)} 开始
                      </span>
                    )}
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {messageCount} 条
                    </span>
                  </button>

                  {!collapsed ? (
                    <div className="px-3 pb-3">
                      <div className="space-y-4">
                        {conversation.interactions.map((interaction) => (
                          <InteractionMessage
                            key={interaction.interactionId}
                            interaction={interaction}
                            blocking={
                              interaction.kind === 'human_message' && interaction.interactionId === blockingInteractionId
                            }
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
          <button type="button" className="ml-2 font-semibold text-destructive underline" onClick={() => void onRetry()}>
            重试
          </button>
        </p>
      ) : null}

    </section>
  );
}

export function TodoProductInteractionComposer({
  todoId,
  canSend,
  todoStatus,
  loading,
  onSubmit,
}: Pick<TodoProductInteractionPanelProps, 'todoId' | 'canSend' | 'todoStatus' | 'loading' | 'onSubmit'>) {
  const [message, setMessage] = useState('');
  const statusAllowsInteraction = canInteractWithTodo(todoStatus);
  const sendDisabled = loading || !canSend || !statusAllowsInteraction;

  useEffect(() => {
    setMessage('');
  }, [todoId]);

  const submit = async () => {
    const text = message.trim();
    if (!text || sendDisabled) return;
    const accepted = await onSubmit({ message: text });
    if (accepted) setMessage('');
  };

  if (!statusAllowsInteraction) {
    return (
      <p className="m-0 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        当前待办状态不允许继续互动。
      </p>
    );
  }

  return (
    <section
      role="region"
      aria-label="发起 Product 待办互动"
      className={cn(
        'rounded-xl border border-border bg-card px-3 py-2 focus-within:border-primary',
        sendDisabled && 'opacity-60',
      )}
    >
      <Textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
          event.preventDefault();
          void submit();
        }}
        placeholder={sendDisabled ? '待办助手执行中，暂无法评论…' : '评论，与待办助手对话；Enter 发送，Shift+Enter 换行'}
        rows={2}
        disabled={sendDisabled}
        className="min-h-0 resize-none border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
        aria-label="评论，与待办助手对话"
      />
      <div className="mt-1 flex justify-end">
        <Button
          type="button"
          variant="primary"
          size="icon"
          className="size-8 rounded-lg"
          loading={loading}
          disabled={sendDisabled || !message.trim()}
          aria-label="发送评论"
          onClick={() => void submit()}
        >
          <Send className="size-4" aria-hidden />
        </Button>
      </div>
    </section>
  );
}
