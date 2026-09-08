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
