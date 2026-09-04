import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { Card, CardContent } from '@/components/ui';
import { TaskClarifyCard } from './TaskClarifyCard';
import { TaskMultiSelectCard } from './TaskMultiSelectCard';
import { TaskReadyCard } from './TaskReadyCard';
import { readTaskCardData } from './shared';
import type { TaskCardData } from './types';

/** task-loop skill 的本地公开卡片入口，不依赖 cardId 或内部卡片运行时。 */
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
        <Card className="w-full max-w-[420px] border-border">
          <CardContent className="p-4 text-center text-xs text-muted-foreground">暂无任务卡片数据</CardContent>
        </Card>
      );
  }
}
