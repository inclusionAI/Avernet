import type { DataTableColumn } from './DataTable';
import { DataTableCell } from './DataTableCell';

interface DataTableRowProps<Row> {
  row: Row;
  rowIndex: number;
  columns: DataTableColumn<Row>[];
  rowHeight: 'h-14' | 'h-16' | 'h-20';
  onClick?: () => void;
}

export function DataTableRow<Row>({ row, rowIndex, columns, rowHeight, onClick }: DataTableRowProps<Row>) {
  const interactive = Boolean(onClick);
  const rowClassName = [
    'group border-b border-border transition-colors duration-150',
    rowHeight,
    interactive ? 'cursor-pointer hover:bg-muted/40' : 'hover:bg-muted/40',
  ].join(' ');

  return (
    <tr
      className={rowClassName}
      aria-rowindex={rowIndex + 2}
      onClick={onClick}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
    >
      {columns.map((column) => (
        <DataTableCell key={column.id} width={column.width} align={column.align} className={column.className}>
          {column.cell(row, rowIndex)}
        </DataTableCell>
      ))}
    </tr>
  );
}
