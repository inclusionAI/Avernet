import { cn } from '@/utils/cn';
import * as React from 'react';

/**
 * Tabs 基础组件：受控/非受控两用，提供 role=tab / role=tabpanel 语义。
 *
 * 不是 Radix 封装:整个组件只需要一个受控 value,而引入又一个 Radix 包会把
 * 一个 40 行的语义约定变成依赖升级的负担。键盘行为按 WAI-ARIA tabs 约定:
 * 左右方向键在 trigger 间移动,Home/End 跳到首尾。
 */
interface TabsProps {
  value?: string;
  onValueChange?: (value: string) => void;
  defaultValue?: string;
  className?: string;
  children: React.ReactNode;
}

const TabsContext = React.createContext<{
  value: string;
  onValueChange: (value: string) => void;
} | null>(null);

function useTabs() {
  const context = React.useContext(TabsContext);
  if (!context) throw new Error('Tabs components must be used within a Tabs');
  return context;
}

function Tabs({ value, onValueChange, defaultValue, className, children }: TabsProps) {
  const [internal, setInternal] = React.useState(defaultValue ?? '');
  const controlled = value !== undefined;
  const current = controlled ? value : internal;

  const change = React.useCallback(
    (next: string) => {
      if (!controlled) setInternal(next);
      onValueChange?.(next);
    },
    [controlled, onValueChange],
  );

  return (
    <TabsContext.Provider value={{ value: current, onValueChange: change }}>
      <div className={cn('flex min-w-0 flex-col', className)}>{children}</div>
    </TabsContext.Provider>
  );
}

function TabsList({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="tablist"
      className={cn('inline-flex w-fit items-center gap-1 rounded-lg bg-muted p-1', className)}
      {...props}
    />
  );
}

interface TabsTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

const TabsTrigger = React.forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ value, className, children, onClick, ...props }, ref) => {
    const { value: selected, onValueChange } = useTabs();
    const isSelected = selected === value;
    return (
      <button
        ref={ref}
        type="button"
        role="tab"
        aria-selected={isSelected}
        // Roving tabindex: only the selected tab is in the tab order, matching
        // how a real tablist is traversed.
        tabIndex={isSelected ? 0 : -1}
        data-state={isSelected ? 'active' : 'inactive'}
        onClick={(event) => {
          onClick?.(event);
          onValueChange(value);
        }}
        className={cn(
          'inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isSelected
            ? 'bg-background text-foreground shadow-sm'
            : 'text-muted-foreground hover:text-foreground',
          className,
        )}
        {...props}
      >
        {children}
      </button>
    );
  },
);
TabsTrigger.displayName = 'TabsTrigger';

interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string;
}

function TabsContent({ value, className, children, ...props }: TabsContentProps) {
  const { value: selected } = useTabs();
  if (selected !== value) return null;
  return (
    <div role="tabpanel" className={cn('min-w-0 flex-1', className)} {...props}>
      {children}
    </div>
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };