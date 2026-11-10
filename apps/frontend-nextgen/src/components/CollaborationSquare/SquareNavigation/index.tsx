import { Link } from '@umijs/max';
import type { ReactNode } from 'react';

export type SquareNavigationResource = 'bot' | 'group' | 'expert';

const ITEMS: Array<{ resource: SquareNavigationResource; label: string; path: string }> = [
  { resource: 'bot', label: '公开 Bot', path: '/collaboration-square/bots' },
  { resource: 'expert', label: '专家', path: '/collaboration-square/experts' },
  { resource: 'group', label: '公开协作群', path: '/collaboration-square/groups' },
];

/**
 * 协作广场统一页头（dmore index.html Frame 1）：60px 高单行，左侧资源 Tab pill，
 * 右侧可注入页级工具（公开Bot Tab 为搜索栏 + 身份选择器）；容器宽度与内容区一致。
 */
export function SquareNavigation({
  active,
  actions,
}: {
  active: SquareNavigationResource;
  /** 页头右侧工具区（搜索/筛选/身份选择器等；详情态等无工具场景不传）。 */
  actions?: ReactNode;
}) {
  return (
    <nav className="shrink-0 bg-background" aria-label="协作广场资源导航">
      <div className="flex h-15 w-full items-center justify-between gap-3 px-5">
        <div className="flex min-w-0 items-center gap-1.5">
          {ITEMS.map((item) => (
            <Link
              key={item.resource}
              to={item.path}
              aria-current={active === item.resource ? 'page' : undefined}
              className={`flex h-8 shrink-0 items-center rounded-md px-3 text-[13px] transition-colors hover:text-primary ${
                active === item.resource
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'font-normal text-muted-foreground hover:bg-muted'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </div>
        {actions ? (
          // 允许收缩：348/338 的定宽工具在窄窗口下收缩截断，不再把页头内容顶出视口。
          <div className="flex min-w-0 items-center gap-3">{actions}</div>
        ) : null}
      </div>
    </nav>
  );
}
