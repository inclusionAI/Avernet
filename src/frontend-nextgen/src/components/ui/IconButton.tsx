import React from 'react';
import { Button, type ButtonProps } from './Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './Tooltip';

interface IconButtonProps extends Omit<ButtonProps, 'size' | 'children'> {
  label: string;
  icon: React.ReactNode;
  size?: 'sm' | 'md';
}

/** 图标按钮统一提供可访问名称和 Tooltip，避免依赖浏览器 title 提示。 */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, icon, size = 'md', className, ...props }, ref) => (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={ref}
            aria-label={label}
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
