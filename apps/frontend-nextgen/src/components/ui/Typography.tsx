import { cn } from '@/utils/cn';
import React from 'react';

type TypographyVariant = 'body' | 'pageTitle' | 'caption' | 'label' | 'value' | 'tableHeader' | 'button' | 'nav';

const variantClasses: Record<TypographyVariant, string> = {
  body: '',
  pageTitle: 'text-[24px] font-semibold tracking-tight text-foreground',
  caption: 'text-xs text-muted-foreground',
  label: 'text-xs font-medium text-muted-foreground',
  value: 'text-xs font-medium text-foreground',
  tableHeader: 'text-sm font-medium text-muted-foreground',
  button: 'text-xs font-semibold',
  nav: 'text-sm font-medium',
};

export interface TypographyProps extends React.HTMLAttributes<HTMLElement> {
  as?: React.ElementType;
  variant?: TypographyVariant;
  htmlFor?: string;
}

export function Typography({ as: Component = 'span', variant = 'body', className, ...props }: TypographyProps) {
  return <Component className={cn(variant === 'body' ? '' : variantClasses[variant], className)} {...props} />;
}

export const PageTitle = (props: TypographyProps) => <Typography as="h1" variant="pageTitle" {...props} />;
export const CaptionText = (props: TypographyProps) => <Typography as="p" variant="caption" {...props} />;
export const LabelText = (props: TypographyProps) => <Typography as="span" variant="label" {...props} />;
export const ValueText = (props: TypographyProps) => <Typography as="span" variant="value" {...props} />;
export const TableHeaderText = (props: TypographyProps) => <Typography as="span" variant="tableHeader" {...props} />;
export const ButtonText = (props: TypographyProps) => <Typography as="span" variant="button" {...props} />;
export const NavText = (props: TypographyProps) => <Typography as="span" variant="nav" {...props} />;
