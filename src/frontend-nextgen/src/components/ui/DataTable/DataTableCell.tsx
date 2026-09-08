import type { ReactNode } from 'react';
import { alignClass, type DataTableAlign } from './align';

export interface DataTableCellProps {
  width?: string;
  align?: DataTableAlign;
  truncate?: boolean;
  children: ReactNode;
  className?: string;
}

export function DataTableCell({ width, align = 'start', truncate = false, children, className }: DataTableCellProps) {
  return (
    <td
      className={[
        'align-middle px-4',
        alignClass(align),
        width ?? '',
        className ?? '',
        truncate ? 'max-w-0 truncate' : '',
      ].join(' ')}
    >
      {truncate ? <span className="block truncate">{children}</span> : children}
    </td>
  );
}
