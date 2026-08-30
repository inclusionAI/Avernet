import { cn } from '@/utils/cn';
import * as SwitchPrimitive from '@radix-ui/react-switch';
import React from 'react';

export interface SwitchProps extends React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root> {
  size?: 'sm' | 'md';
  loading?: boolean;
}

/** Switch：二元设置开关，支持受控/非受控与 loading 状态。 */
const Switch = React.forwardRef<React.ElementRef<typeof SwitchPrimitive.Root>, SwitchProps>(
  ({ className, size = 'md', loading = false, disabled, ...props }, ref) => (
    <SwitchPrimitive.Root
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'peer inline-flex shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input',
        size === 'sm' ? 'h-5 w-9' : 'h-6 w-11',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        className={cn(
          'pointer-events-none block rounded-full bg-background shadow-lg ring-0 transition-transform',
          size === 'sm' ? 'size-4 data-[state=checked]:translate-x-4' : 'size-5 data-[state=checked]:translate-x-5',
        )}
      />
    </SwitchPrimitive.Root>
  ),
);
Switch.displayName = SwitchPrimitive.Root.displayName;

export { Switch };
