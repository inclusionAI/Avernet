import { Button, Segmented, Spin } from '@/components/ui';
import { SERVICE_BOT_STAGE_OPTIONS } from '@/domain/collaboration/serviceBotRouting';
import type { useServiceBotRouting } from '../../hooks/useServiceBotRouting';

export function ServiceBotRoutingFields({
  actorIds,
  botName,
  routing,
}: {
  actorIds: string[];
  botName: (id: string) => string;
  routing: ReturnType<typeof useServiceBotRouting>;
}) {
  const visibleIds = actorIds.filter((id) => routing.states[id]?.status !== 'non_service');
  if (!visibleIds.length) return null;
  return (
    <section aria-label="服务 Bot 路由" className="shrink-0 space-y-2 rounded-lg border border-slate-200/60 p-3">
      {visibleIds.map((id) => {
        const state = routing.states[id];
        return (
          <div key={id} className="space-y-2" role="group" aria-label={`${botName(id)}路由`}>
            <p className="text-sm font-medium">{botName(id)}</p>
            {!state || state.status === 'loading' ? (
              <Spin tip="识别 Bot 类型" />
            ) : state.status === 'error' ? (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <span role="alert">Bot 类型识别失败，请重试</span>
                <Button variant="ghost" size="sm" onClick={() => void routing.retry(id)}>
                  重试
                </Button>
              </div>
            ) : state.status === 'service' ? (
              <>
                <Segmented
                  aria-label={`${botName(id)}路由方式`}
                  value={state.routingMode}
                  options={[
                    { value: 'dynamic', label: '动态路由规则' },
                    { value: 'specified', label: '指定路由' },
                  ]}
                  onChange={(mode) => routing.selectRoutingMode(id, mode)}
                />
                {state.routingMode === 'specified' && (
                  <>
                    <Segmented
                      aria-label={`${botName(id)}运行阶段`}
                      value={state.stage ?? ''}
                      options={SERVICE_BOT_STAGE_OPTIONS}
                      onChange={(stage) => {
                        if (stage) routing.selectStage(id, stage);
                      }}
                    />
                    {!state.stage && <p className="text-xs text-muted-foreground">请选择运行阶段</p>}
                  </>
                )}
              </>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}
