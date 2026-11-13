import { Button } from '@/components/ui/Button';
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { runStatusLabel } from '@/services/botScheduledTasks/results';
import { stageNames, type RoutineRun } from '@/services/botScheduledTasks/types';
import { SafeHtml } from '@/shared/html/SafeHtml';
import { sanitizeMarkdownHtml } from '@/shared/html/sanitizeHtml';
import MarkdownIt from 'markdown-it';
import { X } from 'lucide-react';
import { useState } from 'react';

// Historical execution text is Markdown, never executable HTML.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });

const roleNames: Record<string, string> = {
  user: '用户', human: '用户', assistant: 'Bot', bot: 'Bot', system: '系统', tool: '工具',
};

/** Read the selected historical row only; never query a different runtime or substitute today's prompt. */
export function RoutineRunConversation({ run, botType }: { run: RoutineRun; botType?: string }) {
  const [open, setOpen] = useState(false);
  const hideMissingTarget = Boolean(botType?.trim()) && botType !== 'service';
  const messages = (run.messages || []).filter((message) => message.content.trim());
  const fields = [['执行输入', run.input], ['执行输出', run.output]] as const;
  const hasContent = messages.length > 0 || fields.some(([, value]) => value?.trim()) || run.error?.trim();
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>查看会话</Button>
      <Drawer open={open} onOpenChange={setOpen}>
        <DrawerContent size="lg" className="max-w-full" showClose={false}>
          <DrawerHeader className="mb-5 border-b border-border pb-4">
            <div className="flex items-center justify-between gap-4">
              <DrawerTitle className="text-lg font-semibold tracking-tight text-foreground">执行会话详情</DrawerTitle>
              <DrawerClose asChild>
                <Button variant="ghost" size="icon" className="shrink-0" aria-label="关闭执行会话">
                  <X aria-hidden className="size-4" />
                </Button>
              </DrawerClose>
            </div>
            <DrawerDescription className="text-sm text-muted-foreground">仅展示本次执行已报告的内容，不代表完整聊天记录。</DrawerDescription>
          </DrawerHeader>
          <dl className="mb-5 space-y-2 break-all text-xs text-muted-foreground">
            <div>执行记录：{run.id}</div>
            <div>状态：{runStatusLabel(run.status)}</div>
            {(!hideMissingTarget || run.runtime_stage?.trim()) && <div>环境：{run.runtime_stage ? stageNames[run.runtime_stage] || run.runtime_stage : '未报告'}</div>}
            {(!hideMissingTarget || run.device_uuid?.trim()) && <div>运行实例：{run.device_uuid || '未报告'}</div>}
            {(!hideMissingTarget || run.operator_id?.trim()) && <div>Caller：{run.operator_id || '未报告'}</div>}
          </dl>
          <div className="space-y-4">
            {messages.length > 0 ? messages.map((message, index) => (
              <section key={index} className="rounded-lg border border-border bg-card p-4">
                <h3 className="mb-2 text-xs font-medium text-muted-foreground">{roleNames[message.role] || message.role}</h3>
                <p className="m-0 whitespace-pre-wrap break-words text-sm">{message.content}</p>
              </section>
            )) : fields.map(([label, value]) => value?.trim() ? (
              <section key={label} className="rounded-lg border border-border bg-card p-4">
                <h3 className="mb-2 text-xs font-medium text-muted-foreground">{label}</h3>
                <SafeHtml
                  className="markdown-body min-w-0 break-words text-sm [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto"
                  html={sanitizeMarkdownHtml(markdown.render(value))}
                />
              </section>
            ) : null)}
            {run.error?.trim() && (
              <section className="rounded-lg border border-destructive/30 bg-destructive/10 p-4">
                <h3 className="mb-2 text-xs font-medium text-destructive">执行错误</h3>
                <p className="m-0 whitespace-pre-wrap break-words text-sm">{run.error}</p>
              </section>
            )}
            {!hasContent && <Empty title="未报告会话内容" description="当前接口或引擎未返回本次执行的消息、输入、输出或错误。" />}
          </div>
        </DrawerContent>
      </Drawer>
    </>
  );
}
