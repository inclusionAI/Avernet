import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/services/botWorkshop';
import { botContainerService, type ContainerSnapshot } from '@/services/botWorkshop/botContainerService';
import { Boxes } from 'lucide-react';
import React from 'react';

export interface BotContainerInfoProps {
  bot: BotDomain;
  onOpen: (bot: BotDomain) => void;
}

function isEligible(bot: BotDomain) {
  return bot.deployment === 'cloud' && bot.serviceMode === 'service' && bot.lifecycle === 'running';
}

/** 运行态服务 Bot 的容器概览。列表接口未携带 summary 时，仅为当前页合格 Bot 请求一次容器快照。 */
export function BotContainerInfo({ bot, onOpen }: BotContainerInfoProps) {
  const [snapshot, setSnapshot] = React.useState<ContainerSnapshot>();
  const [failed, setFailed] = React.useState(false);
  const eligible = isEligible(bot);

  React.useEffect(() => {
    if (!eligible) return undefined;
    let active = true;
    setSnapshot(undefined);
    setFailed(false);
    void botContainerService
      .list(bot.id)
      .then((next) => {
        if (active) setSnapshot(next);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [bot.id, eligible]);

  if (!eligible) return null;
  const summary = snapshot?.summary;
  const label = summary ? `容器 ${summary.healthy}/${summary.total}` : failed ? '容器实例' : '容器加载中';
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
      className={toneClass}
      leftIcon={<Boxes aria-hidden className="size-3.5" />}
      onClick={(event) => {
        event.stopPropagation();
        onOpen(bot);
      }}
    >
      {label}
    </Button>
  );
}
