import type { DataTableColumn } from './DataTable';
import { alignClass, justifyClass } from './align';
import { stickyPositionClass, stickyShadowClass } from './sticky';

interface DataTableHeaderProps<Row> {
  columns: DataTableColumn<Row>[];
}

export function DataTableHeader<Row>({ columns }: DataTableHeaderProps<Row>) {
  return (
    <thead className="bg-muted/50 text-[13px] text-muted-foreground">
      <tr>
        {columns.map((column) => (
          <th
            key={column.id}
            scope="col"
            className={[
              'h-11 px-4 text-left align-middle font-medium',
              alignClass(column.headerAlign ?? column.align ?? 'start'),
              column.width ?? '',
              column.className ?? '',
              column.sticky ? stickyPositionClass(column.sticky) : '',
              column.headerClassName ?? '',
            ].join(' ')}
          >
            {column.sticky ? (
              <>
                {/* 白底垫底之上叠表头底色层，保证 sticky 表头列不透出滚动内容（与整行表头视觉一致）。 */}
                <div className="pointer-events-none absolute inset-0 bg-muted/50" />
                <div className={stickyShadowClass(column.sticky)} />
                {/* 与 DataTableCell 同理：包裹层随列对齐语义注入 justify-*。 */}
                <div
                  className={`relative flex h-full items-center ${justifyClass(column.headerAlign ?? column.align ?? 'start')}`}
                >
                  {column.header}
                </div>
              </>
            ) : (
              column.header
            )}
          </th>
        ))}
      </tr>
    </thead>
  );
}
