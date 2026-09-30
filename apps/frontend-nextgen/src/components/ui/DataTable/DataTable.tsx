import type { ReactNode } from 'react';
import { DataTableEmpty } from './DataTableEmpty';
import { DataTableHeader } from './DataTableHeader';
import { DataTableRow } from './DataTableRow';

export type DataTableAlign = 'start' | 'center' | 'end';

export interface DataTableColumn<Row> {
  id: string;
  header: ReactNode;
  cell: (row: Row, rowIndex: number) => ReactNode;
  width?: string;
  align?: DataTableAlign;
  headerAlign?: DataTableAlign;
  className?: string;
}

export interface DataTableProps<Row> {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  getRowKey: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  empty?: ReactNode;
  ariaLabel?: string;
  rowHeight?: 'h-14' | 'h-16' | 'h-20';
}

export function DataTable<Row>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  empty,
  ariaLabel = 'DataTable',
  rowHeight = 'h-16',
}: DataTableProps<Row>) {
  if (rows.length === 0 && empty !== undefined) {
    return (
      <div className="w-full overflow-x-auto">
        <table aria-label={ariaLabel} className="w-full table-fixed border-collapse text-sm">
          <DataTableHeader columns={columns} />
          <tbody>
            <DataTableEmpty colSpan={columns.length}>{empty}</DataTableEmpty>
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="w-full overflow-x-auto">
      <table aria-label={ariaLabel} className="w-full table-fixed border-collapse text-sm">
        <DataTableHeader columns={columns} />
        <tbody>
          {rows.map((row, rowIndex) => (
            <DataTableRow
              key={getRowKey(row)}
              row={row}
              rowIndex={rowIndex}
              columns={columns}
              rowHeight={rowHeight}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
