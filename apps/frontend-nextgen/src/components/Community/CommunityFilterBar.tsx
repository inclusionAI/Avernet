import { Segmented } from '@/components/ui/Segmented';
import type { ComponentProps } from 'react';

type CommunityFilterBarProps<T extends string> = Pick<
  ComponentProps<typeof Segmented<T>>,
  'value' | 'options' | 'onChange' | 'aria-label'
>;

export function CommunityFilterBar<T extends string>(props: CommunityFilterBarProps<T>) {
  return (
    <div className="mt-2 overflow-x-auto">
      <Segmented<T>
        {...props}
        className="h-9 w-max"
        activeOptionClassName="text-[13px] font-medium"
        inactiveOptionClassName="text-[13px] font-normal"
      />
    </div>
  );
}
