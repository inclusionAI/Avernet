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
  unknown,
  input,
  onClose,
}: {
  botId?: string;
  status?: ExpertBotCreationStatus;
  error?: string;
  unknown: boolean;
  input: BotCreateInput;
  onClose: () => void;
}) {
  const locate = () => {
    const spaceId = Number(input.spaceId);
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
      {input.serviceMode === 'service' && status?.bot && (
        <p className="text-sm text-muted-foreground">服务 Bot 草稿已创建，配置进度单独显示；发布请沿原发布流程操作。</p>
      )}
      {status?.message && <p className="whitespace-pre-wrap text-sm">{status.message}</p>}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {botId && <p className="text-xs text-muted-foreground">Bot：{botId}</p>}
      <div className="flex gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          留在当前页面
        </Button>
        <Button type="button" onClick={locate}>
          前往 Bot 列表
        </Button>
      </div>
    </section>
  );
}
