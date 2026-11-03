import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/services/botWorkshop';
import { botContainerService, type ContainerSnapshot } from '@/services/botWorkshop/botContainerService';
import { Boxes } from 'lucide-react';
import React from 'react';

export interface BotContainerInfoProps {
  bot: BotDomain;
  /** 已解析的请求身份；缺失时不发起容器接口请求。 */
  userId?: string;
  /** 列表库存声明的 Owner/协作者容器查看资格。 */
  canView?: boolean;
  onOpen: (bot: BotDomain) => void;
}

function isEligible(bot: BotDomain) {
  return bot.runtime.engine !== 'teclaw' &&
    bot.deployment === 'cloud' && bot.serviceMode === 'service' && bot.lifecycle === 'running';
}

/**
 * 服务 Bot 列表的「容器信息」单元格。
 *
 * 老版本只在独立列内展示“活跃数 / 实例总数”，并通过悬浮文案提示点击查看详情。
 * 列表接口未携带 summary 时，仅为当前页合格 Bot 请求一次容器快照。
 */
export function BotContainerInfo({ bot, userId, canView = true, onOpen }: BotContainerInfoProps) {
  const [snapshot, setSnapshot] = React.useState<ContainerSnapshot>();
  const [failed, setFailed] = React.useState(false);
  const eligible = Boolean(userId) && canView && isEligible(bot);

  React.useEffect(() => {
    if (!eligible) return undefined;
    let active = true;
    setSnapshot(undefined);
    setFailed(false);
    void botContainerService
      .list(bot.id, userId!)
      .then((next) => {
        if (active) setSnapshot(next);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [bot.id, eligible, userId]);

  if (!eligible) return <span className="select-none text-sm text-muted-foreground">—</span>;
  const summary = snapshot?.summary;
  const label = summary ? `${summary.healthy}/${summary.total}` : failed ? '加载失败' : '加载中…';
  const hasIssue = Boolean(summary && (summary.abnormal > 0 || summary.unknown > 0));
  const toneClass = failed
    ? 'border-border text-muted-foreground'
    : hasIssue
    ? 'border-warning/50 text-warning'
    : 'border-success/50 text-success';
  return (
    <Button
      variant="outline"
      size="sm"
      aria-label={`查看 ${bot.name} 容器实例`}
      className={`min-w-[78px] justify-center font-medium ${toneClass}`}
      leftIcon={<Boxes aria-hidden className="size-3.5" />}
      onClick={(event) => {
        event.stopPropagation();
        onOpen(bot);
      }}
    >
      <span>{label}</span>
      {summary ? <span className="sr-only">，共 {summary.total} 个实例，{summary.healthy} 个活跃，点击查看详情</span> : null}
    </Button>
  );
}
