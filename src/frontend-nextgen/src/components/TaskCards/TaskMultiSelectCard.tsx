import { Card, CardContent, CardHeader, CardTitle, Button } from '@/components/ui';
import { Check } from 'lucide-react';
import { useState } from 'react';
import { submitTaskCardAction } from '@/services/tasks/taskCardBridge';
import type { TaskCardData } from './types';

export function TaskMultiSelectCard({ data }: { data: TaskCardData }) {
  const [selected, setSelected] = useState<number | null>(null);
  return (
    <Card className="w-full max-w-[420px] border-border shadow-sm">
      <CardHeader className="border-b border-border px-4 py-3">
        <CardTitle className="text-sm">多任务选择</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-4">
        {data.prompt ? <p className="m-0 text-xs text-muted-foreground">{data.prompt}</p> : null}
        <div className="space-y-2">
          {(data.tasks ?? []).map((task) => (
            <Button
              key={task.index}
              variant={selected === task.index ? 'default' : 'secondary'}
              className="h-auto w-full justify-start whitespace-normal px-3 py-2 text-left text-xs"
              disabled={selected !== null}
              onClick={() => {
                setSelected(task.index);
                submitTaskCardAction(`我选择任务 ${task.index}：${task.summary}`);
              }}
              rightIcon={selected === task.index ? <Check className="h-3.5 w-3.5" aria-hidden /> : undefined}
            >
              {task.index}. {task.summary}
            </Button>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
