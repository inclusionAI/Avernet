import { Button, Textarea } from '@/components/ui';
import { ChatHeaderActions, type ChatHeaderActionsProps } from '@/pages/Workspace/Chat/components/ChatHeaderActions';
import { cn } from '@/utils/cn';
import { ArrowUp, Bot, Check, ShieldCheck, UserRound } from 'lucide-react';
import { DEMO_BOT_NAME, DEMO_MESSAGES, DEMO_SESSION_TITLE } from './demoContent';

export function DemoConversation({
  headerActions,
  onOpenEscort,
}: {
  headerActions: ChatHeaderActionsProps;
  onOpenEscort: () => void;
}) {
  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/30 px-4 py-2 text-[10px] text-muted-foreground">
        <span className="rounded border border-primary/20 bg-primary/10 px-1.5 py-0.5 font-semibold text-primary">
          DEMO
        </span>
        <span>独立交互演示 · 无需登录 · 仅本地模拟，无真实请求</span>
      </div>
      <header className="task-escort-demo-chat-header border-b border-border px-5 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Bot className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="m-0 truncate text-sm font-semibold">{DEMO_BOT_NAME}</h1>
            <p className="m-0 mt-1 truncate text-[10px] text-muted-foreground">{DEMO_SESSION_TITLE}</p>
          </div>
        </div>
        <ChatHeaderActions {...headerActions} className="shrink-0" />
      </header>
      <div className="task-escort-demo-messages">
        <div className="task-escort-demo-message-column">
          <p className="my-1 text-center text-[10px] text-muted-foreground">今天 · 演示会话</p>
          {DEMO_MESSAGES.map((message) => {
            const fromBot = message.role === 'assistant';
            return (
              <article key={message.id} className="flex items-start gap-3" aria-label={fromBot ? 'Bot 示例消息' : '用户示例消息'}>
                <span
                  className={cn(
                    'flex size-8 shrink-0 items-center justify-center rounded-lg',
                    fromBot ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                  )}
                >
                  {fromBot ? <Bot className="size-4" aria-hidden="true" /> : <UserRound className="size-4" aria-hidden="true" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="mb-2 flex items-center gap-2 text-[10px] text-muted-foreground">
                    <span className="font-medium text-foreground">{fromBot ? DEMO_BOT_NAME : '演示访客'}</span>
                    <time>{message.time}</time>
                  </div>
                  <p className={cn('m-0 rounded-xl px-4 py-3 text-xs leading-7', fromBot ? 'bg-primary/5' : 'bg-muted/50')}>
                    {message.text}
                  </p>
                </div>
              </article>
            );
          })}
          <section className="ml-11 rounded-xl border border-primary/20 bg-primary/5 p-4" aria-label="护航状态提示">
            <div className="flex items-center gap-2 text-xs font-medium">
              <ShieldCheck className="size-4 shrink-0 text-primary" aria-hidden="true" />
              任务护航已就绪
              <span className="ml-auto text-[10px] font-normal text-muted-foreground">模拟状态</span>
            </div>
            <p className="mb-3 mt-2 text-xs leading-6 text-muted-foreground">
              从聊天侧打开运行概览与流程编辑器；收起后，演示草稿仍保留在本页内存中。
            </p>
            <Button variant="outline" size="sm" leftIcon={<ShieldCheck className="size-3" />} onClick={onOpenEscort}>
              打开任务护航
            </Button>
          </section>
        </div>
      </div>
      <div className="task-escort-demo-composer">
        <div className="rounded-2xl border border-input bg-background p-3 shadow-sm">
          <Textarea
            aria-label="示例消息（只读，不会发送）"
            readOnly
            value="帮我检查关键指标，并生成今天的巡检报告。"
            rows={2}
            className="min-h-0 resize-none border-0 bg-transparent p-1 text-xs shadow-none"
          />
          <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1"><Check className="size-3" aria-hidden="true" />只读示例 · 不发送消息</span>
            <Button disabled size="sm" aria-label="发送已禁用（仅演示）" className="size-7 p-0">
              <ArrowUp className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
        <p className="mb-0 mt-2 text-center text-[10px] text-muted-foreground">所有 Bot、消息、流程及运行记录均为虚构示例</p>
      </div>
    </>
  );
}
