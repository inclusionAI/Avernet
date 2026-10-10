import { Link } from '@umijs/max';

export type SquareNavigationResource = 'bot' | 'group';

const ITEMS: Array<{ resource: SquareNavigationResource; label: string; path: string }> = [
  { resource: 'bot', label: '公开 Bot', path: '/collaboration-square/bots' },
  { resource: 'group', label: '公开协作群', path: '/collaboration-square/groups' },
];

export function SquareNavigation({ active }: { active: SquareNavigationResource }) {
  return (
    <nav
      className="shrink-0 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6"
      aria-label="协作广场资源导航"
    >
      <div className="mx-auto flex w-full max-w-7xl items-stretch gap-8">
        {ITEMS.map((item) => (
          <Link
            key={item.resource}
            to={item.path}
            aria-current={active === item.resource ? 'page' : undefined}
            className={`relative flex h-[54px] items-center px-1 text-sm transition-colors hover:text-primary ${
              active === item.resource ? 'font-medium text-foreground' : 'font-normal text-muted-foreground'
            }`}
          >
            {item.label}
            <span
              aria-hidden
              className={`absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary transition-transform duration-200 ease-out ${
                active === item.resource ? 'scale-x-100' : 'scale-x-0'
              }`}
            />
          </Link>
        ))}
      </div>
    </nav>
  );
}
