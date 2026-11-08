import { Link } from '@umijs/max';

export type SquareNavigationResource = 'bot' | 'group' | 'expert';

const ITEMS: Array<{ resource: SquareNavigationResource; label: string; path: string }> = [
  { resource: 'expert', label: '专家市场', path: '/collaboration-square/experts' },
  { resource: 'bot', label: '公开Bot', path: '/collaboration-square/bots' },
  { resource: 'group', label: '公开协作群', path: '/collaboration-square/groups' },
];

export function SquareNavigation({ active }: { active: SquareNavigationResource }) {
  return (
    <nav
      className="shrink-0 bg-background/80 py-2 backdrop-blur-md"
      aria-label="协作广场资源导航"
    >
      <div className="mx-auto flex w-full max-w-7xl items-center gap-2 px-4 sm:px-6 lg:px-8">
        {ITEMS.map((item) => (
          <Link
            key={item.resource}
            to={item.path}
            aria-current={active === item.resource ? 'page' : undefined}
            className={`flex h-10 items-center rounded-lg px-4 text-sm font-normal transition-colors hover:text-primary ${
              active === item.resource ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted'
            }`}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
