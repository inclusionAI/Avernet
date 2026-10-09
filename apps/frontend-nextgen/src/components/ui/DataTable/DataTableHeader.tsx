import type { DataTableColumn } from './DataTable';
import { alignClass } from './align';

interface DataTableHeaderProps<Row> {
  columns: DataTableColumn<Row>[];
}

export function DataTableHeader<Row>({ columns }: DataTableHeaderProps<Row>) {
  return (
    <thead className="bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
      <tr>
        {columns.map((column) => (
          <th
            key={column.id}
            scope="col"
            className={[
              'h-10 px-4 text-left align-middle font-medium',
              alignClass(column.headerAlign ?? column.align ?? 'start'),
              column.width ?? '',
              column.className ?? '',
            ].join(' ')}
          >
            {column.header}
          </th>
        ))}
      </tr>
    </thead>
  );
}
