import type { ReactNode } from 'react';
import { alignClass, justifyClass, type DataTableAlign } from './align';
import { stickyPositionClass, stickyShadowClass, type StickySide } from './sticky';

export interface DataTableCellProps {
  width?: string;
  align?: DataTableAlign;
  truncate?: boolean;
  children: ReactNode;
  className?: string;
  /** 固定列：定位、不透明垫底、hover 高亮与滚动遮挡阴影由通用层注入。 */
  sticky?: StickySide;
}

export function DataTableCell({
  width,
  align = 'start',
  truncate = false,
  children,
  className,
  sticky,
}: DataTableCellProps) {
  return (
    <td
      className={[
        'align-middle px-4',
        alignClass(align),
        width ?? '',
        sticky ? stickyPositionClass(sticky) : '',
        className ?? '',
        truncate ? 'max-w-0 truncate' : '',
      ].join(' ')}
    >
      {sticky ? (
        <>
          {/* hover 高亮层叠在不透明垫底之上，替代 group-hover:bg-muted/40——半透明底会透出被遮列文字。 */}
          <div className="pointer-events-none absolute inset-0 bg-muted/40 opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
          <div className={stickyShadowClass(sticky)} />
          {/* 包裹层随列 align 注入 justify-*，防止 flex 默认 flex-start 打掉右对齐列的原对齐语义。 */}
          <div className={`relative flex h-full items-center ${justifyClass(align)}`}>{children}</div>
        </>
      ) : truncate ? (
        <span className="block truncate">{children}</span>
      ) : (
        children
      )}
    </td>
  );
}
