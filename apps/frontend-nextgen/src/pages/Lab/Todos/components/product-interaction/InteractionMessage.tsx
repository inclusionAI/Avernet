import { Badge, Button } from '@/components/ui';
import type { TodoProductInteraction } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { Bot, CircleStop, LoaderCircle, User } from 'lucide-react';
import { formatInteractionTime, INTERACTION_STATE_CLASSES, INTERACTION_STATE_LABELS } from './interactionConversation';

interface InteractionMessageProps {
  interaction: TodoProductInteraction;
  blocking: boolean;
  loading: boolean;
  humanName: string;
  botName: string;
  onCancel: (interactionId: string) => Promise<boolean> | boolean;
}

export function InteractionMessage({
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
              <Badge className="border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">停止请求</Badge>
            ) : null}
            <Badge className={cn('border px-1.5 py-0.5 text-[10px]', INTERACTION_STATE_CLASSES[interaction.deliveryState])}>
              {INTERACTION_STATE_LABELS[interaction.deliveryState]}
            </Badge>
            <time className="text-xs text-muted-foreground/70">{formatInteractionTime(interaction.createdAt)}</time>
          </div>
          <p className="m-0 mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/85">
            {interaction.requestMessage || (isStopRequest ? '请求停止当前处理。' : '')}
          </p>
          {interaction.failureCode ? <p className="m-0 mt-1 text-xs text-destructive">失败码：{interaction.failureCode}</p> : null}
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
              <Badge className="border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">助手</Badge>
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
