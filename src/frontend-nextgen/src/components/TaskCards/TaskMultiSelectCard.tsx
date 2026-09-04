import { Button } from '@/components/ui';
import { useTaskCardAction } from '@/hooks/useTaskCardAction';
import { cn } from '@/utils/cn';
import { Check } from 'lucide-react';
import { useState } from 'react';
import type { TaskCardData } from './types';

/**
 * 多任务选择卡片 —— 按原始 card SDK 视觉规格自绘。
 * 守卫要求交互按钮用项目 <Button>，故用 <Button variant="ghost"> + className 全量覆盖复刻原按钮观感
 * （编号圆形、选中蓝边/蓝底、淡显）；bg-gray-* 中性底改用 bg-muted token。其余与原卡片一致。
 */
export function TaskMultiSelectCard({ data }: { data: TaskCardData }) {
  const submitTaskCardAction = useTaskCardAction();
  const [selected, setSelected] = useState<number | null>(null);
  const tasks = data.tasks ?? [];

  const handleSelect = (task: { index: number; summary: string }) => {
    if (selected !== null) return;
    setSelected(task.index);
    submitTaskCardAction(`我选择任务 ${task.index}：${task.summary}`);
  };

  return (
    <div className="p-1 w-full flex justify-start task-loop-card-root">
      <div className="w-full max-w-[360px] bg-white border border-gray-100 shadow-md rounded-2xl overflow-hidden transition-all duration-500">
        {/* Header */}
        <div className="px-3.5 pt-3.5 pb-2 border-b border-gray-50">
          <div className="flex items-center gap-1">
            <span className="text-xs leading-none">🔀</span>
            <span className="text-sm font-semibold tracking-tight text-gray-900">多任务选择</span>
          </div>
        </div>

        {/* Prompt */}
        {data.prompt ? (
          <div className="px-3.5 pt-2.5">
            <p className="text-[11px] text-gray-500">{data.prompt}</p>
          </div>
        ) : null}

        {/* Task List */}
        <div className="px-3.5 py-2.5 space-y-1.5">
          {tasks.map((task) => {
            const isSelected = selected === task.index;
            const isDimmed = selected !== null && !isSelected;
            return (
              <Button
                key={task.index}
                variant="ghost"
                disabled={selected !== null}
                onClick={() => handleSelect(task)}
                className={cn(
                  'w-full flex justify-start items-center gap-1.5 p-2.5 rounded-xl border transition-all duration-300 text-left h-auto font-normal',
                  isSelected
                    ? 'border-blue-500 bg-blue-50 disabled:opacity-100'
                    : 'border-gray-100 bg-white hover:border-blue-200 hover:bg-blue-50/30 hover:scale-[1.02]',
                  isDimmed && 'disabled:opacity-40',
                )}
              >
                <div
                  className={cn(
                    'flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold leading-none',
                    isSelected ? 'bg-blue-500 text-white' : 'bg-muted text-gray-500',
                  )}
                >
                  {task.index}
                </div>
                <span className="text-[11px] text-gray-700 leading-relaxed flex-1">{task.summary}</span>
                {isSelected ? <Check className="text-blue-500 h-3 w-3" aria-hidden /> : null}
              </Button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
