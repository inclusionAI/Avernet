import { cn } from '@/utils/cn';
import { BookOpen, Bot, ChevronDown, ChevronRight, MessageSquare, ShieldCheck, Star, Users } from 'lucide-react';
import { DEMO_BOT_NAME, DEMO_SESSION_TITLE } from './demoContent';

const NAV_ITEMS = [
  { label: '对话', icon: MessageSquare, selected: true },
  { label: 'Bot 管理', icon: Bot },
  { label: '任务护航', icon: ShieldCheck },
  { label: '知识库', icon: BookOpen },
  { label: '协作', icon: Users },
];

/** 静态场景导航刻意不渲染按钮或链接，避免把演示带到真实工作台。 */
export function DemoNavigation({ brandName }: { brandName: string }) {
  return (
    <aside className="task-escort-demo-nav border-r border-border bg-background">
      <div className="flex flex-col items-center gap-2 px-2 py-5">
        <span className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <Bot className="size-5" aria-hidden="true" />
        </span>
        <span className="max-w-full truncate text-xs font-semibold">{brandName}</span>
      </div>
      <nav aria-label="场景导航（静态演示）" className="flex-1 px-2">
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {NAV_ITEMS.map(({ label, icon: Icon, selected }) => (
            <li
              key={label}
              aria-current={selected ? 'page' : undefined}
              className={cn(
                'flex flex-col items-center gap-1.5 rounded-xl px-1 py-3 text-[10px] text-muted-foreground',
                selected && 'bg-primary/10 font-medium text-primary',
              )}
            >
              <Icon className="size-5" aria-hidden="true" />
              <span>{label}</span>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex flex-col items-center gap-2 px-2 py-4 text-[10px] text-muted-foreground">
        <span className="flex size-8 items-center justify-center rounded-full bg-muted text-xs">访</span>
        <span>演示访客</span>
      </div>
    </aside>
  );
}

export function DemoSessionSidebar({ favorite }: { favorite: boolean }) {
  return (
    <aside className="task-escort-demo-sessions border-r border-border bg-muted/20" aria-label="Bot 与会话场景">
      <div className="flex h-16 items-center justify-between border-b border-border px-5">
        <h2 className="m-0 text-sm font-semibold">对话</h2>
        <span className="rounded-md border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">DEMO</span>
      </div>
      <div className="space-y-4 p-3">
        <p className="flex items-center gap-1 px-2 text-xs text-muted-foreground">
          <ChevronDown className="size-3" aria-hidden="true" /> 我的 Bot · 1
        </p>
        <div className="flex items-center gap-2 rounded-lg px-2 py-2">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Bot className="size-4" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="m-0 truncate text-xs font-medium">{DEMO_BOT_NAME}</p>
            <p className="m-0 mt-1 text-[10px] text-muted-foreground">本地示例 · 无需连接</p>
          </div>
          <ChevronDown className="size-3 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="ml-4 border-l border-border pl-3">
          <div className="rounded-lg bg-selected px-3 py-3" aria-current="page">
            <p className="m-0 flex items-center gap-1 text-xs font-medium">
              {favorite && <Star className="size-3 shrink-0 fill-star text-star" aria-label="已收藏" />}
              <span className="truncate">{DEMO_SESSION_TITLE}</span>
            </p>
            <p className="m-0 mt-2 text-[10px] text-muted-foreground">今天 · 3 条示例消息</p>
          </div>
          <div className="flex items-center gap-1 px-3 py-4 text-xs text-muted-foreground">
            <ChevronRight className="size-3" aria-hidden="true" /> 历史巡检示例
          </div>
        </div>
      </div>
      <p className="mt-auto px-5 py-4 text-[10px] leading-relaxed text-muted-foreground">
        静态场景列表，不切换真实 Bot 或会话。
      </p>
    </aside>
  );
}
