import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { AlertCircle } from 'lucide-react';
import { TaskClarifyCard } from './TaskClarifyCard';
import { TaskMultiSelectCard } from './TaskMultiSelectCard';
import { TaskReadyCard } from './TaskReadyCard';
import { readTaskCardData } from './shared';
import type { TaskCardData } from './types';

/**
 * task-loop skill 的本地公开卡片入口，不依赖 cardId 或内部卡片运行时。
 * 视觉按原始 card SDK 规格（各子卡片自绘），仅交互按钮/输入用项目 <Button>/<Textarea> 守卫合规。
 */
export function TaskLoopCard(props: PanelContentProps) {
  const data = readTaskCardData(props.params ?? {}) as TaskCardData;
  switch (data.type) {
    case 'task_clarify':
      return <TaskClarifyCard data={data} />;
    case 'task_multi_select':
      return <TaskMultiSelectCard data={data} />;
    case 'task_ready':
      return <TaskReadyCard data={data} />;
    default:
      return (
        <div className="p-1 w-full flex justify-start task-loop-card-root">
          <div className="w-full max-w-[360px] bg-white border border-gray-100 shadow-md rounded-2xl p-4 text-center">
            <AlertCircle className="block mx-auto mb-1.5 h-4 w-4 text-gray-300" aria-hidden />
            <p className="text-[11px] text-gray-400">暂无数据</p>
          </div>
        </div>
      );
  }
}
