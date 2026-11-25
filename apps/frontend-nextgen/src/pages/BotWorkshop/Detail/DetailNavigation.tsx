import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import React from 'react';

interface DetailNavigationItem<T extends string> {
  key: T;
  label: string;
  shortLabel?: string;
  icon: React.ReactNode;
}

interface DetailNavigationProps<T extends string, U extends string> {
  hidden?: boolean;
  mainTabs: Array<DetailNavigationItem<T>>;
  moreTabs: Array<DetailNavigationItem<U>>;
  activeTab: T;
  activeMore?: U;
  onTabChange: (tab: T) => void;
  onMoreChange: (tab: U) => void;
}

function NavigationButton<T extends string>({
  item,
  active,
  onClick,
}: {
  item: DetailNavigationItem<T>;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              'size-11 flex-col gap-1 rounded-lg text-[13px] font-normal text-muted-foreground',
              active && 'bg-muted text-foreground hover:bg-muted hover:text-foreground',
            )}
            aria-label={item.label}
            onClick={onClick}
          >
            {item.icon}
            <span className="leading-4">{item.shortLabel ?? item.label.slice(0, 2)}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent side="right">{item.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function DetailNavigation<T extends string, U extends string>({
  hidden,
  mainTabs,
  moreTabs,
  activeTab,
  activeMore,
  onTabChange,
  onMoreChange,
}: DetailNavigationProps<T, U>) {
  return (
    <nav
      aria-label="Bot 编辑模块"
      className={cn(
        'bot-editor-nav app-scrollbar flex w-12 shrink-0 flex-col items-center gap-3 overflow-y-auto border-r border-divider bg-card py-3',
        hidden && 'hidden',
      )}
    >
      {mainTabs.map((item) => (
        <NavigationButton
          key={item.key}
          item={item}
          active={activeTab === item.key && !activeMore}
          onClick={() => onTabChange(item.key)}
        />
      ))}
      {/* artboard-047:主/更多两组之间为 24×1 短分隔线(而不是整列长线)。 */}
      {moreTabs.length && mainTabs.length ? <span aria-hidden className="h-px w-6 bg-divider" /> : null}
      {moreTabs.map((item) => (
        <NavigationButton
          key={item.key}
          item={item}
          active={activeMore === item.key}
          onClick={() => onMoreChange(item.key)}
        />
      ))}
    </nav>
  );
}
