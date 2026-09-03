import { cn } from '@/utils/cn';
import { Slot } from '@radix-ui/react-slot';
import { LoaderCircle } from 'lucide-react';
import React from 'react';

type ButtonVariant = 'default' | 'primary' | 'secondary' | 'outline' | 'ghost' | 'destructive' | 'link';
type ButtonSize = 'sm' | 'md' | 'default' | 'lg' | 'icon';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

// D3: variant 对齐 showcase 规范集。primary 作 default 的别名（保历史调用点零改动）。
// secondary/outline 同为线框样式（历史 secondary 即线框观感）；ghost/destructive/link 对齐 showcase。
// token 一律走 shadcn 语义（bg-primary / text-primary-foreground / border-input 等），不再用 --color-* 或硬编码 text-white。
const variantClasses: Record<ButtonVariant, string> = {
  default: 'border-transparent bg-primary text-primary-foreground hover:opacity-90',
  primary: 'border-transparent bg-primary text-primary-foreground hover:opacity-90',
  secondary: 'border border-input bg-background text-foreground hover:bg-accent',
  outline: 'border border-input bg-background text-foreground hover:bg-accent',
  ghost: 'border-transparent bg-transparent hover:bg-accent hover:text-foreground',
  destructive: 'border-transparent bg-destructive text-destructive-foreground hover:opacity-90',
  link: 'border-transparent bg-transparent text-primary underline underline-offset-4 hover:opacity-80',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 rounded-lg px-3 text-xs',
  md: 'h-8 rounded-lg px-4 text-sm',
  default: 'h-8 rounded-lg px-4 text-sm',
  lg: 'h-8 rounded-lg px-5 text-sm',
  icon: 'h-8 w-8 rounded-lg p-0',
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      asChild,
      variant = 'default',
      size = 'default',
      loading,
      disabled,
      className,
      leftIcon,
      rightIcon,
      children,
      type = 'button',
      ...props
    },
    ref,
  ) => {
    const classes = cn(
      'inline-flex shrink-0 items-center justify-center gap-2 border font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
      variantClasses[variant],
      sizeClasses[size],
      className,
    );

    if (asChild) {
      return (
        <Slot ref={ref} className={classes} {...props}>
          {children}
        </Slot>
      );
    }

    return (
      <button
        ref={ref}
        type={type === 'submit' ? 'submit' : type === 'reset' ? 'reset' : 'button'}
        disabled={disabled || loading}
        className={classes}
        {...props}
      >
        {loading ? <LoaderCircle aria-hidden className="h-4 w-4 animate-spin" /> : leftIcon}
        {children}
        {!loading && rightIcon}
      </button>
    );
  },
);
Button.displayName = 'Button';
