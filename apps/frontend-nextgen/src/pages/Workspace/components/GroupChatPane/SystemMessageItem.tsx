import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { ChatMessage } from '@tc-chat/core';
import { getMessageTime, parseSystemEvent } from './messageHelpers';

/**
 * 系统事件行（dmore artboard-010：「05-25 10:00 用户 **风太** 已加入会话」形态）。
 *
 * 居中弱化纯文字条（对齐 chat 页 b2-b3 已还原口径：12px text-content-soft、无 severity
 * 图标、无 SystemNotice 胶囊描边）；content 可解析出「身份+名字+动作」时名字半粗强调，
 * 解析失败降级为「时间 + 原文」整行，不隐藏消息（spec: collaboration-message-area）。
 */
export function SystemMessageItem({ message }: { message: ChatMessage }) {
  const time = getMessageTime(message);
  const event = parseSystemEvent(message.content);

  return (
    <div className="flex min-w-0 max-w-full items-center justify-center gap-1 px-2 pb-3">
      {time ? <span className="shrink-0 text-xs leading-4 text-content-soft">{time}</span> : null}
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <p tabIndex={0} className="m-0 min-w-0 max-w-[70%] truncate text-xs leading-4 text-content-soft">
              {event ? (
                <>
                  {event.identity ? <span>{event.identity} </span> : null}
                  <span className="font-semibold">{event.name}</span>
                  <span> {event.action}</span>
                </>
              ) : (
                message.content
              )}
            </p>
          </TooltipTrigger>
          <TooltipContent className="max-w-[min(32rem,calc(100vw-2rem))] whitespace-pre-wrap break-words border-[var(--color-system-message-tooltip)] bg-[var(--color-system-message-tooltip)] text-[var(--color-system-message-tooltip-foreground)]">
            {message.content}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
