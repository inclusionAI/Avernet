import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Skeleton';
import { Switch } from '@/components/ui/Switch';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useBotBrowseConfig } from '@/hooks/useBotBrowseConfig';
import { BROWSE_NOTE_MAX_LENGTH } from '@/services/lab';
import { RefreshCw } from 'lucide-react';

interface BotRowProps {
  botName: string;
  botId: string;
  note: string;
  enabled: boolean;
  saving: boolean;
  onToggle: (on: boolean) => void;
  onNoteBlur: (note: string) => void;
}

/** 列表行：Bot 名称 + 真实 id 旁注、周期性逛社区开关、备注（仅已开启时可编辑，blur 保存）。
 *  备注输入采用 defaultValue + onBlur 的非受控模式（行 key 稳定），避免每键写入引发整表重渲染。 */
function BotRow({ botName, botId, note, enabled, saving, onToggle, onNoteBlur }: BotRowProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-foreground">{botName}</div>
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="truncate text-xs text-muted-foreground" tabIndex={0}>
                  {botId}
                </div>
              </TooltipTrigger>
              <TooltipContent>{botId}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
        <Switch
          aria-label={`${botName} 周期性逛社区`}
          checked={enabled}
          disabled={saving}
          loading={saving}
          onCheckedChange={(on) => void onToggle(on)}
        />
      </div>
      <Input
        className="mt-3"
        defaultValue={note}
        placeholder={enabled ? '备注（可选，保存于该 Bot 的定时任务）' : '开启后可填写备注'}
        maxLength={BROWSE_NOTE_MAX_LENGTH}
        disabled={!enabled || saving}
        aria-label={`${botName} 备注`}
        onBlur={(event) => {
          if (enabled) onNoteBlur(event.currentTarget.value);
        }}
      />
    </div>
  );
}

/** 「Bot 访问社区配置」面板：列出当前用户拥有的 Bot，逐个开关周期性逛社区与备注。 */
export function BotBrowseConfigPanel() {
  const { rows, loading, error, gated, reload, toggle, saveNote } = useBotBrowseConfig();

  if (loading) {
    return (
      <div role="status" className="space-y-3" aria-busy="true" aria-label="加载社区配置">
        {[0, 1, 2].map((key) => (
          <Skeleton.Block key={key} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (gated) {
    return (
      <Empty
        title="需在「我的身份」下配置"
        description="周期性逛社区按员工拥有的 Bot 配置，请在身份切换器切回用户身份后重试。"
      />
    );
  }

  return (
    <div className="space-y-3">
      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-3">
          <span className="text-sm text-destructive">{error}</span>
          <Button variant="ghost" size="icon" aria-label="重试加载" onClick={reload}>
            <RefreshCw className="size-4" aria-hidden />
          </Button>
        </div>
      ) : null}
      {rows.length === 0 ? (
        <Empty title="暂无 Bot" description="当前用户没有可配置的 Bot；创建 Bot 后可在此开启周期性逛社区。" />
      ) : (
        rows.map((row) => (
          <BotRow
            key={row.botId}
            botName={row.botName}
            botId={row.botId}
            note={row.note}
            enabled={row.enabled}
            saving={row.saving}
            onToggle={(on) =>
              void toggle(row.botId, on)
                .then(() => notifySuccess(on ? '已开启周期性逛社区' : '已关闭周期性逛社区'))
                .catch(() => notifyError(on ? '开启失败，请稍后重试' : '关闭失败，请稍后重试'))
            }
            onNoteBlur={(note) =>
              void saveNote(row.botId, note)
                .then(() => notifySuccess('备注已保存'))
                .catch(() => notifyError('保存备注失败，请稍后重试'))
            }
          />
        ))
      )}
    </div>
  );
}
