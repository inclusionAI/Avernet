import { history, useLocation } from '@umijs/max';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import React, { useEffect } from 'react';

import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { HealthCheckView } from '@/components/BotWorkshop/BotHealthCheckDrawer/HealthCheckView';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { BotHealthOverallStatus } from '@/domain/botHealthCheck';
import { useBotHealthCheck } from '@/hooks/useBotHealthCheck';
import { useBotWorkshopDetail } from '@/hooks/useBotWorkshopDetail';
import { useBotWorkshopRequestIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { botHealthCheckService } from '@/services/botHealthCheck';

const overallMeta: Record<
  BotHealthOverallStatus,
  { label: string; tone: 'neutral' | 'primary' | 'success' | 'warning' | 'error' }
> = {
  healthy: { label: '健康', tone: 'success' },
  warning: { label: '需关注', tone: 'warning' },
  critical: { label: '异常', tone: 'error' },
  scanning: { label: '检测中', tone: 'primary' },
  unknown: { label: '暂无结果', tone: 'neutral' },
};

const BotHealthCheckPage: React.FC = () => {
  const params = new URLSearchParams(useLocation().search);
  const id = params.get('id');
  const requestIdentity = useBotWorkshopRequestIdentity();
  const detail = useBotWorkshopDetail(id, false, requestIdentity.ready);
  const healthCheck = useBotHealthCheck();
  const { openHealthCheck, target } = healthCheck;

  useEffect(() => {
    if (!id || !detail.bot || !requestIdentity.ready || !requestIdentity.userId || target?.botId === detail.bot.id) {
      return;
    }
    const nextTarget = botHealthCheckService.toTarget(detail.bot, requestIdentity.userId);
    if (!nextTarget) return;
    openHealthCheck(nextTarget);
  }, [id, detail.bot, requestIdentity.ready, requestIdentity.userId, target?.botId, openHealthCheck]);

  const meta = healthCheck.summary ? overallMeta[healthCheck.summary.overallStatus] : undefined;
  const bot = detail.bot;

  if (!id) {
    return (
      <Empty
        title="缺少 Bot 标识"
        description="请从 Bot 工坊重新进入。"
        action={<Button onClick={() => history.push('/bot-workshop')}>返回 Bot 工坊</Button>}
      />
    );
  }

  if (requestIdentity.loading) return <Spin tip="正在获取当前用户身份…" />;
  if (requestIdentity.error)
    return (
      <Empty
        title="无法加载用户身份"
        description={requestIdentity.error}
        action={<Button onClick={() => history.push('/bot-workshop')}>返回 Bot 工坊</Button>}
      />
    );
  if (detail.loading) return <Spin tip="加载 Bot 配置…" />;
  if (detail.error || !bot)
    return (
      <Empty
        title="无法查看 Bot"
        description={detail.error ?? 'Bot 不存在或无权访问'}
        action={<Button onClick={() => history.push('/bot-workshop')}>返回 Bot 工坊</Button>}
      />
    );

  return (
    <main className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          aria-label="返回 Bot 工坊"
          onClick={() => history.push('/bot-workshop')}
          leftIcon={<ArrowLeft className="size-4" />}
        />
        <BotAvatar name={bot.name} />
        <div className="min-w-0 flex-1">
          <h1 className="m-0 truncate text-base font-semibold">{bot.name}</h1>
          <p className="m-0 mt-0.5 text-xs text-muted-foreground">
            {bot.runtime.engine} · {bot.deployment === 'local' ? '本地' : '云端'}
          </p>
        </div>
        {meta ? <Badge tone={meta.tone}>{meta.label}</Badge> : null}
        <Button
          variant="secondary"
          leftIcon={<RefreshCw className="size-4" />}
          disabled={healthCheck.loading || healthCheck.checking}
          onClick={() => void healthCheck.refresh()}
        >
          刷新结果
        </Button>
      </header>
      <section className="app-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
        {healthCheck.loading && !healthCheck.summary ? (
          <div className="flex flex-col items-center justify-center py-20">
            <Spin tip="正在加载健康检查结果" />
          </div>
        ) : null}

        {!healthCheck.loading && healthCheck.error ? (
          <Empty
            title="健康检查结果加载失败"
            description={healthCheck.error}
            action={
              <Button variant="secondary" onClick={() => void healthCheck.refresh()}>
                重试
              </Button>
            }
          />
        ) : null}

        {!healthCheck.loading && !healthCheck.error && !healthCheck.summary ? (
          <Empty
            title="暂无健康检查结果"
            description="可以点击重新检测，结果生成后刷新查看。"
            action={<Button onClick={() => void healthCheck.runDiagnose()}>重新检测</Button>}
          />
        ) : null}

        {healthCheck.summary ? (
          <HealthCheckView
            summary={healthCheck.summary}
            capability={healthCheck.capability}
            botName={bot.name}
            loading={healthCheck.loading}
            checking={healthCheck.checking}
            onReDiagnose={healthCheck.runDiagnose}
          />
        ) : null}
      </section>
    </main>
  );
};

export default BotHealthCheckPage;
