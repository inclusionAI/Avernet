import type { DataTableAlign } from './DataTable';

export { type DataTableAlign };

export function alignClass(align: DataTableAlign): string {
  switch (align) {
    case 'center':
      return 'text-center';
    case 'end':
      return 'text-right';
    case 'start':
    default:
      return 'text-left';
  }
}

/**
 * 列对齐语义 → flex 主轴对齐类。sticky 单元格的内容包在 flex 容器里渲染
 * （见 DataTableCell/Header 的 relative flex 包裹层），必须随列的 align 注入
 * 对应 justify-*，否则 flex 默认 flex-start 会把右对齐列的内容推到左侧。
 */
export function justifyClass(align: DataTableAlign): string {
  switch (align) {
    case 'center':
      return 'justify-center';
    case 'end':
      return 'justify-end';
    case 'start':
    default:
      return 'justify-start';
  }
}
