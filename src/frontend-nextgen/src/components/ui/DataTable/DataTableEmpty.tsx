import type { ReactNode } from 'react';

interface DataTableEmptyProps {
  colSpan: number;
  children: ReactNode;
}

export function DataTableEmpty({ colSpan, children }: DataTableEmptyProps) {
  return (
    <tr>
      <td colSpan={colSpan} className="h-32 px-4 py-10 text-center text-sm text-muted-foreground">
        {children}
      </td>
    </tr>
  );
}
