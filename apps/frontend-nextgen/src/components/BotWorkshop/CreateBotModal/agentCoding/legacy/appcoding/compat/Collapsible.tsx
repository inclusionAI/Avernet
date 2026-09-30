import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { ChevronDown } from 'lucide-react';
import React from 'react';

type ContextValue = { open: boolean; setOpen: (open: boolean) => void };
const CollapsibleContext = React.createContext<ContextValue>({ open: false, setOpen: () => undefined });

export function Collapsible({
  open: controlledOpen,
  onOpenChange,
  defaultOpen = false,
  className,
  children,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = (next: boolean) => {
    if (controlledOpen === undefined) setUncontrolledOpen(next);
    onOpenChange?.(next);
  };
  return (
    <CollapsibleContext.Provider value={{ open, setOpen }}>
      <div className={className} data-state={open ? 'open' : 'closed'}>
        {children}
      </div>
    </CollapsibleContext.Provider>
  );
}

export const CollapsibleTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { children: React.ReactNode }
>(({ children, onClick, ...props }, ref) => {
  const { open, setOpen } = React.useContext(CollapsibleContext);
  return (
    <Button
      variant="ghost"
      {...props}
      ref={ref}
      type="button"
      data-state={open ? 'open' : 'closed'}
      aria-expanded={open}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setOpen(!open);
        onClick?.(event);
      }}
    >
      {children}
    </Button>
  );
});

export const CollapsibleContent = React.forwardRef<
  HTMLDivElement,
  { children: React.ReactNode; className?: string; forceMount?: boolean }
>(({ children, className, forceMount = false }, ref) => {
  const { open } = React.useContext(CollapsibleContext);
  const [mounted, setMounted] = React.useState(open);
  React.useEffect(() => {
    if (open) setMounted(true);
  }, [open]);
  if (!forceMount && !mounted && !open) return null;
  return (
    <div
      ref={ref}
      className={cn(
        'grid transition-all duration-300 ease-in-out',
        open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        className,
      )}
      onTransitionEnd={() => {
        if (!open) setMounted(false);
      }}
      data-state={open ? 'open' : 'closed'}
    >
      <div className={open ? 'overflow-visible' : 'overflow-hidden'}>{children}</div>
    </div>
  );
});

export function CollapsibleSection({
  title,
  children,
  defaultOpen = false,
  className,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen} className={className}>
      <CollapsibleTrigger className="group flex h-auto min-h-0 w-full cursor-pointer items-center justify-start gap-1.5 rounded-none border-0 bg-transparent px-0 py-1 text-left text-xs font-medium text-muted-foreground shadow-none transition-colors hover:bg-transparent hover:text-foreground">
        <ChevronDown
          size={13}
          className="text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
        />
        <span>{title}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  );
}
