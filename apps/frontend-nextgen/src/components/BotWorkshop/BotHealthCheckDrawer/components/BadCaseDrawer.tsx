import { parseEvidence } from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import type { BotHealthCheckItem } from '@/domain/botHealthCheck';

interface BadCaseDrawerProps {
  item: BotHealthCheckItem | null;
  onOpenChange: (open: boolean) => void;
}

export function BadCaseDrawer({ item, onOpenChange }: BadCaseDrawerProps) {
  const { sourceType, lowScoreSessionIds, benchmarkName, benchmarkScope } = parseEvidence(item?.evidence);
  const isSession = sourceType === 'session';

  return (
    <Drawer open={Boolean(item)} onOpenChange={onOpenChange}>
      <DrawerContent side="right" size="md" aria-describedby="bad-case-description">
        <DrawerHeader>
          <DrawerTitle className="text-lg font-semibold text-[var(--color-fg)]">Bad Case</DrawerTitle>
          <DrawerDescription id="bad-case-description" className="text-sm text-[var(--color-muted)]">
            {item?.name ?? '查看异常样本'}
          </DrawerDescription>
        </DrawerHeader>
        {item ? (
          <div className="space-y-4 p-6 pt-0">
            <div className="flex items-center gap-2">
              <Badge tone={isSession ? 'primary' : 'neutral'}>来源类型：{isSession ? 'Session' : 'Benchmark'}</Badge>
            </div>
            {isSession && (
              <div className="space-y-3">
                <div className="text-sm font-medium text-[var(--color-fg)]">低分会话记录</div>
                {lowScoreSessionIds.length > 0 ? (
                  <div className="space-y-2">
                    {lowScoreSessionIds.map((sessionId, index) => (
                      <Card key={sessionId} className="bg-muted p-3 shadow-none">
                        <div className="text-xs text-[var(--color-muted)]">#{index + 1}</div>
                        <code className="block break-words text-sm text-[var(--color-fg)]">{sessionId}</code>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-[var(--color-muted)]">暂无低分会话记录</div>
                )}
                {item.badCase ? (
                  <Card className="bg-muted px-3 py-2 text-sm text-[var(--color-fg)] shadow-none">{item.badCase}</Card>
                ) : null}
              </div>
            )}
            {!isSession && (
              <div className="space-y-3">
                <div className="text-sm font-medium text-[var(--color-fg)]">基准评测信息</div>
                {benchmarkName ? (
                  <div className="text-sm text-[var(--color-fg)]">
                    <span className="text-[var(--color-muted)]">评测名称：</span>
                    {benchmarkName}
                  </div>
                ) : null}
                {benchmarkScope ? (
                  <div className="text-sm text-[var(--color-fg)]">
                    <span className="text-[var(--color-muted)]">评测范围：</span>
                    {benchmarkScope}
                  </div>
                ) : null}
                {!benchmarkName && !benchmarkScope ? (
                  <div className="text-sm text-[var(--color-muted)]">暂无 Bad Case 数据</div>
                ) : null}
              </div>
            )}
          </div>
        ) : null}
      </DrawerContent>
    </Drawer>
  );
}
