import { Button } from '@/components/ui/Button';
import { switchSpaceContext } from '@/hooks/useSpaceContext';
import type { BotCreateInput } from '@/services/botWorkshop';
import type { ExpertBotCreationStatus } from '@/services/expertMarketService';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import { history } from '@umijs/max';

const LABELS: Record<string, string> = {
  AWAITING_AUTHORIZATION: '等待授权',
  CREATING: '正在创建 Bot',
  APPLYING: 'Bot 已创建，正在配置',
  READY: 'Bot 创建及配置已完成',
  CREATE_FAILED: 'Bot 创建失败',
  APPLY_FAILED: 'Bot 已创建，但配置失败',
  AUTHORIZATION_REJECTED: '授权已拒绝',
  AUTHORIZATION_EXPIRED: '授权已过期',
};
export function ExpertCreationResult({
  botId,
  status,
  error,
  initializing,
  initializationPending,
  afterCreateError,
  unknown,
  input,
  onClose,
  onRecover,
  recovering,
}: {
  botId?: string;
  status?: ExpertBotCreationStatus;
  error?: string;
  initializing?: boolean;
  initializationPending?: boolean;
  afterCreateError?: string;
  unknown: boolean;
  input?: BotCreateInput;
  onClose: () => void;
  onRecover?: () => void;
  recovering?: boolean;
}) {
  const locate = () => {
    const spaceId = Number(input?.spaceId);
    if (Number.isFinite(spaceId) && spaceId > 0) switchSpaceContext(spaceId);
    useBotWorkshopStore
      .getState()
      .setQueryState({ keyword: '', engine: '', deployment: undefined, serviceMode: undefined, page: 1 });
    useBotWorkshopStore.setState({ highlightBotId: botId });
    onClose();
    history.push(botId ? `/bot-workshop?highlight_bot_id=${encodeURIComponent(botId)}` : '/bot-workshop');
  };
  return (
    <section aria-label="专家 Bot 创建结果" className="space-y-3">
      <p role="status">
        {unknown
          ? '首次提交结果未知，请先到 Bot 列表确认，避免重复创建。'
          : status
          ? LABELS[status.state] || status.state
          : '请求已受理，正在查询真实创建进度…'}
      </p>
      {input?.serviceMode === 'service' && status?.bot && (
        <p className="text-sm text-muted-foreground">服务 Bot 草稿已创建，配置进度单独显示；发布请沿原发布流程操作。</p>
      )}
      {status?.message && <p className="whitespace-pre-wrap text-sm">{status.message}</p>}
      {initializing && <p role="status">Bot 已创建，正在执行模板初始化…</p>}
      {initializationPending && <p className="text-sm text-muted-foreground">正在完成模板创建，请稍候…</p>}
      {afterCreateError && <p role="alert" className="text-sm text-destructive">Bot 已创建，但模板初始化未完成：{afterCreateError}</p>}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {botId && <p className="text-xs text-muted-foreground">Bot：{botId}</p>}
      {status?.apply?.apply_id && (
        <div aria-label="配置报告" className="space-y-1 text-sm">
          <p>Apply：{status.apply.apply_id} · {status.apply.result}</p>
          {(status.apply.entries || []).map((entry, index) => (
            <p key={`${entry.category}:${entry.name}:${index}`}>
              {entry.category} / {entry.name}：{entry.action}{entry.error ? ` · ${entry.error}` : ''}{entry.note ? ` · ${entry.note}` : ''}
            </p>
          ))}
          {status.apply.notes?.map((note, index) => <p key={index}>{note}</p>)}
        </div>
      )}
      {status?.state === 'APPLY_FAILED' && status.recoverable !== false && onRecover && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">重试会在原 Bot 按原清单替换声明的配置。若清单声明资源（含空列表），会清理工作区非保护项，请先保存需要保留的输出。</p>
          <Button type="button" disabled={recovering || initializationPending} onClick={onRecover}>在原 Bot 重试配置</Button>
        </div>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="secondary" disabled={initializationPending} onClick={onClose}>
          留在当前页面
        </Button>
        <Button type="button" disabled={initializationPending} onClick={locate}>
          前往 Bot 列表
        </Button>
      </div>
    </section>
  );
}
