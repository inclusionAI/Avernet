import { Button } from '@/components/ui/Button';
import { history } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';

export function LabTasksHeader() {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex shrink-0 items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="返回" onClick={() => history.back()} className="-ml-2">
          <ArrowLeft className="size-5" aria-hidden />
        </Button>
        <h1 className="m-0 text-2xl font-semibold tracking-tight text-foreground">任务</h1>
      </div>
      <p className="m-0 text-sm leading-6 text-muted-foreground">
        查看跨Bot、跨群的协同任务，可在
        <Button
          variant="link"
          className="h-auto px-1 py-0 text-sm no-underline"
          onClick={() => history.push('/workspace/chat')}
        >
          对话
        </Button>
        或
        <Button
          variant="link"
          className="h-auto px-1 py-0 text-sm no-underline"
          onClick={() => history.push('/workspace/collaboration')}
        >
          协作群
        </Button>
        中发起此类任务
      </p>
    </header>
  );
}
