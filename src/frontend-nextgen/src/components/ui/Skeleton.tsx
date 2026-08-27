import { cn } from '@/utils/cn';

// bg-foreground/10 + animate-pulse：可置于任意背景(muted/white/dark)均可见的柔灰 shimmer 占位。
// 关键：旧 bg-muted 会在 bg-muted 页面(如 Admin 内容区)上与背景同色而不可见；showcase 的
// bg-muted 骨架靠贴在 bg-card(白)才可见，这里贴在 muted 上需换以叠色。ui/ 内 animate-pulse 不受门禁限。
function Block({ className }: { className?: string }) {
  return <div aria-hidden className={cn('rounded-lg bg-foreground/10 animate-pulse', className)} />;
}
function Line({ className }: { className?: string }) {
  return <Block className={cn('h-3 rounded-full', className)} />;
}
function ListItem() {
  return (
    <div className="flex items-center gap-3 p-3">
      <Block className="h-10 w-10 shrink-0 rounded-xl" />
      <div className="flex-1 space-y-2">
        <Line className="w-1/2" />
        <Line className="w-4/5" />
      </div>
    </div>
  );
}
function CardSkeleton() {
  return (
    <div className="space-y-3 p-5">
      <Block className="h-10 w-10" />
      <Line />
      <Line className="w-3/4" />
    </div>
  );
}
export const Skeleton = { Block, Line, ListItem, Card: CardSkeleton };
