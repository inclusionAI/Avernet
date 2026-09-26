import React from 'react';
import { Button, type ButtonProps } from './Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './Tooltip';

interface IconButtonProps extends Omit<ButtonProps, 'size' | 'children'> {
  label: string;
  /** 可访问名称覆盖；缺省用 label（视觉 Tooltip 与读屏名称可分离）。 */
  ariaLabel?: string;
  icon: React.ReactNode;
  size?: 'sm' | 'md';
}

/** 图标按钮统一提供可访问名称和 Tooltip，避免依赖浏览器 title 提示。 */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, ariaLabel, icon, size = 'md', className, ...props }, ref) => (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={ref}
            aria-label={ariaLabel ?? label}
            size="icon"
            variant="ghost"
            className={size === 'sm' ? `h-7 w-7 ${className ?? ''}` : className}
            {...props}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  ),
);
IconButton.displayName = 'IconButton';
