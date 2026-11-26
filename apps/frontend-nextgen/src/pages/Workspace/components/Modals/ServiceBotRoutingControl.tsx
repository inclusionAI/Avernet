import { Button, Segmented, Spin, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import { SERVICE_BOT_STAGE_OPTIONS } from '@/domain/collaboration/serviceBotRouting';
import { Info } from 'lucide-react';
import type { useServiceBotRouting } from '../../hooks/useServiceBotRouting';

export function ServiceBotRoutingControl({
  actorId,
  botName,
  routing,
}: {
  actorId: string;
  botName: string;
  routing: ReturnType<typeof useServiceBotRouting>;
}) {
  const state = routing.states[actorId];
  if (state?.status === 'non_service') return null;
  if (!state || state.status === 'loading') {
    return <Spin tip="识别 Bot 类型" className="flex-row gap-1 py-0 [&>svg]:size-3 [&>span]:text-xs" />;
  }
  if (state.status === 'error') {
    return (
      <div className="inline-flex items-center gap-1 text-xs text-destructive">
        <span role="alert">Bot 类型识别失败，请重试</span>
        <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => void routing.retry(actorId)}>
          重试
        </Button>
      </div>
    );
  }
  if (state.status !== 'service') return null;
  return (
    <div className="inline-flex min-w-0 flex-wrap items-center gap-1.5" role="group" aria-label={`${botName}路由`}>
      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-muted-foreground">
        路由方式
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" aria-label={`${botName}路由方式说明`} className="size-4 border-0 p-0">
                <Info className="size-3" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-72 space-y-1 leading-relaxed">
              <p>动态路由规则：优先使用生产环境；生产不可用时，依次尝试预发、草稿。</p>
              <p>指定路由：仅使用你选择的运行阶段。</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </span>
      <Segmented
        aria-label={`${botName}路由方式`}
        value={state.routingMode}
        options={[
          { value: 'dynamic', label: '动态路由规则' },
          { value: 'specified', label: '指定路由' },
        ]}
        className="[&_button]:h-7 [&_button]:px-2.5 [&_button]:text-xs"
        onChange={(mode) => routing.selectRoutingMode(actorId, mode)}
      />
      {state.routingMode === 'specified' && (
        <Segmented
          aria-label={`${botName}运行阶段`}
          value={state.stage ?? ''}
          options={SERVICE_BOT_STAGE_OPTIONS}
          className="border border-border bg-background [&_button]:h-7 [&_button]:px-2.5 [&_button]:text-xs"
          activeOptionClassName="bg-primary text-primary-foreground hover:bg-primary"
          onChange={(stage) => {
            if (stage) routing.selectStage(actorId, stage);
          }}
        />
      )}
    </div>
  );
}
