import { Fragment, type ReactNode, useEffect, useRef } from 'react';
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
  /** 仅作用于表头单元格（如 sticky 列需与表头底色对齐）。 */
  headerClassName?: string;
  /**
   * 固定列（横向滚动时吸附容器边缘）。定位、不透明垫底、hover 高亮层
   * 与滚动遮挡阴影由通用层注入；勿在 className/headerClassName 手写
   * sticky 背景类——半透明底会透出被遮列文字（hover 内容重叠缺陷，2026-11 修复）。
   */
  sticky?: 'left' | 'right';
}

export interface DataTableProps<Row> {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  getRowKey: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  isRowClickable?: (row: Row) => boolean;
  renderExpandedRow?: (row: Row, rowIndex: number) => ReactNode;
  empty?: ReactNode;
  ariaLabel?: string;
  rowHeight?: 'h-14' | 'h-16' | 'h-18' | 'h-20';
  /** 宽表在窄容器中横向滚动，避免固定列挤压内容列。 */
  minWidth?: string;
}

export function DataTable<Row>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  isRowClickable,
  renderExpandedRow,
  empty,
  ariaLabel = 'DataTable',
  rowHeight = 'h-16',
  minWidth,
}: DataTableProps<Row>) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // 宽表横向滚动感知：容器写入 --sticky-{left,right}-shadow(1/0)，
  // 供固定列边缘阴影层表达「边缘之外仍有被遮内容」，滚到尽头即消失。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      const hasLeft = el.scrollLeft > 0;
      const hasRight = el.scrollWidth - el.clientWidth - el.scrollLeft > 1;
      el.style.setProperty('--sticky-left-shadow', hasLeft ? '1' : '0');
      el.style.setProperty('--sticky-right-shadow', hasRight ? '1' : '0');
    };
    update();
    el.addEventListener('scroll', update, { passive: true });
    // jsdom 等无 ResizeObserver 的环境跳过尺寸监听（scroll 监听与初始写入不受影响）。
    const resizeObserver = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(update);
    resizeObserver?.observe(el);
    return () => {
      el.removeEventListener('scroll', update);
      resizeObserver?.disconnect();
    };
  });

  if (rows.length === 0 && empty !== undefined) {
    return (
      <div ref={scrollRef} className="w-full overflow-x-auto">
        <table aria-label={ariaLabel} className={`w-full table-fixed border-collapse text-sm ${minWidth ?? ''}`}>
          <DataTableHeader columns={columns} />
          <tbody>
            <DataTableEmpty colSpan={columns.length}>{empty}</DataTableEmpty>
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="w-full overflow-x-auto">
      <table aria-label={ariaLabel} className={`w-full table-fixed border-collapse text-sm ${minWidth ?? ''}`}>
        <DataTableHeader columns={columns} />
        <tbody>
          {rows.map((row, rowIndex) => {
            const key = getRowKey(row);
            const expanded = renderExpandedRow?.(row, rowIndex);
            const clickable = !isRowClickable || isRowClickable(row);
            return (
              <Fragment key={key}>
                <DataTableRow
                  row={row}
                  rowIndex={rowIndex}
                  columns={columns}
                  rowHeight={rowHeight}
                  onClick={onRowClick && clickable ? () => onRowClick(row) : undefined}
                />
                {expanded ? (
                  <tr className="border-b border-divider bg-muted/20">
                    <td colSpan={columns.length} className="p-0">
                      {expanded}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
