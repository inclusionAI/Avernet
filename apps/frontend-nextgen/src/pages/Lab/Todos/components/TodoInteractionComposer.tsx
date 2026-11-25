import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Textarea } from '@/components/ui/Textarea';
import type { CreateTodoInteractionInput, TodoBotOption } from '@/domain/todos/models';
import { AtSign, Send, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { InteractionReplyReference } from './todoInteractionModel';

interface TodoInteractionComposerProps {
  replyTarget: InteractionReplyReference | null;
  loading: boolean;
  mentionBots: TodoBotOption[];
  mentionBotsLoading: boolean;
  mentionBotsError: string | null;
  onClearReply: () => void;
  onSubmit: (input: CreateTodoInteractionInput) => Promise<boolean>;
}

export function TodoInteractionComposer({
  replyTarget,
  loading,
  mentionBots,
  mentionBotsLoading,
  mentionBotsError,
  onClearReply,
  onSubmit,
}: TodoInteractionComposerProps) {
  const [message, setMessage] = useState('');
  const [selectedMentionIds, setSelectedMentionIds] = useState<string[]>([]);
  const selectedMentionBots = useMemo(
    () => selectedMentionIds.flatMap((id) => mentionBots.find((bot) => bot.botId === id) ?? []),
    [mentionBots, selectedMentionIds],
  );

  const toggleMention = (bot: TodoBotOption, checked: boolean) => {
    setSelectedMentionIds((current) =>
      checked
        ? current.includes(bot.botId)
          ? current
          : [...current, bot.botId]
        : current.filter((id) => id !== bot.botId),
    );
    if (checked) {
      setMessage((current) => `${current}${current && !current.endsWith(' ') ? ' ' : ''}@${bot.botName} `);
    }
  };

  const submit = async () => {
    const input: CreateTodoInteractionInput = { message };
    if (replyTarget) input.replyToEventId = replyTarget.eventId;
    if (selectedMentionIds.length) {
      input.mentions = selectedMentionIds.map((actorId) => ({ actorType: 'bot', actorId }));
    }
    if (!(await onSubmit(input))) return;
    setMessage('');
    setSelectedMentionIds([]);
    onClearReply();
  };

  return (
    <div className="space-y-2">
      {replyTarget ? (
        <div className="flex items-start gap-3 rounded-lg border-l-2 border-primary/45 bg-muted/45 px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="m-0 text-xs font-medium text-muted-foreground">
              回复 f{replyTarget.floor} · {replyTarget.label}
            </p>
            <p className="m-0 mt-1 line-clamp-2 break-words text-xs leading-5 text-foreground/80">
              {replyTarget.content}
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 rounded-md"
            aria-label="取消回复"
            disabled={loading}
            onClick={onClearReply}
          >
            <X className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : null}
      <Textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder="告诉 Bot 需要补充或修改的内容"
        rows={4}
        disabled={loading}
      />
      {selectedMentionBots.length ? (
        <div className="flex flex-wrap gap-1.5" aria-label="已选择的 Bot">
          {selectedMentionBots.map((bot) => (
            <span
              key={bot.botId}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary"
            >
              @{bot.botName}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-4 rounded-full text-primary hover:bg-primary/15"
                aria-label={`移除 @${bot.botName}`}
                disabled={loading}
                onClick={() => toggleMention(bot, false)}
              >
                <X className="size-3" aria-hidden />
              </Button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <Popover>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="h-8 rounded-lg px-3 text-xs text-muted-foreground"
              disabled={loading || mentionBotsLoading || mentionBots.length === 0}
              leftIcon={<AtSign className="size-4" aria-hidden />}
            >
              {mentionBotsLoading ? '加载空间 Bot…' : '@ Bot'}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 p-2">
            <p className="m-0 px-2 pb-2 text-xs text-muted-foreground">选择当前待办空间内的 Bot</p>
            <div className="app-scrollbar max-h-60 space-y-1 overflow-y-auto">
              {mentionBots.map((bot) => {
                const checked = selectedMentionIds.includes(bot.botId);
                return (
                  <label
                    key={bot.botId}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-muted"
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(next) => toggleMention(bot, next)}
                      disabled={loading}
                      aria-label={`@${bot.botName}`}
                    />
                    <span className="min-w-0 flex-1 truncate">{bot.botName}</span>
                  </label>
                );
              })}
            </div>
          </PopoverContent>
        </Popover>
        <Button
          className="h-8 rounded-lg px-4 text-xs"
          onClick={() => void submit()}
          loading={loading}
          disabled={!message.trim()}
          leftIcon={<Send className="size-4" aria-hidden />}
        >
          发送互动
        </Button>
      </div>
      {mentionBotsError ? (
        <p className="m-0 text-xs text-muted-foreground">空间 Bot 暂不可用，本次仍可发送普通互动。</p>
      ) : null}
    </div>
  );
}
