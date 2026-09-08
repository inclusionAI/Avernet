import { Skeleton } from '@/components/ui/Skeleton';
import React from 'react';

/** 列宽须与 BotTable 的 7 列一一对应,否则加载态与数据态的列位置会错位;行数固定,不跟随 pageSize。 */
const COLUMN_WIDTHS = ['w-[280px]', '', 'w-[96px]', 'w-[80px]', 'w-[220px]', 'w-[360px]', 'w-[72px]'];

export interface BotTableSkeletonProps {
  rows?: number;
}

const BotTableSkeleton: React.FC<BotTableSkeletonProps> = ({ rows = 6 }) => (
  <div role="status" aria-label="Bot 列表加载中" className="w-full overflow-x-auto">
    <table aria-hidden className="w-full table-fixed border-collapse text-sm">
      <thead className="bg-muted/30">
        <tr>
          {COLUMN_WIDTHS.map((width, index) => (
            <th key={index} className={`h-10 px-4 ${width}`}>
              <Skeleton.Line className="w-14" />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }, (_, rowIndex) => (
          <tr key={rowIndex} className="h-16 border-b border-border">
            {COLUMN_WIDTHS.map((width, columnIndex) => (
              <td key={columnIndex} className={`px-4 ${width}`}>
                {columnIndex === 0 ? (
                  <div className="flex items-center gap-3">
                    <Skeleton.Block className="h-5 w-5 shrink-0 rounded-full" />
                    <div className="min-w-0 flex-1 space-y-2">
                      <Skeleton.Line className="w-2/3" />
                      <Skeleton.Line className="w-1/3" />
                    </div>
                  </div>
                ) : (
                  <Skeleton.Line className={columnIndex === 1 ? 'w-4/5' : 'w-12'} />
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

export default BotTableSkeleton;
