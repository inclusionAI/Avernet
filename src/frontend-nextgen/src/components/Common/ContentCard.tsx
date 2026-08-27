import { Card } from '@/components/ui';
import { cn } from '@/utils/cn';
import React from 'react';

export function ContentCard({ className, ...props }: React.ComponentProps<typeof Card>) {
  return <Card className={cn('overflow-hidden', className)} {...props} />;
}
