import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import type { CallbackCompensationCapabilities } from '@/domain/taskEscortParity/callbackCompensationTypes';
import type { CallbackCompensationState } from '@/hooks/taskEscortParity/callbackCompensation/useCallbackCompensation';
import { BellRing, ShieldCheck } from 'lucide-react';

export function CallbackCompensationTime({ from, to }: { from: string; to: string }) {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const format = (value: string) => {
    const date = new Date(value);
    return Number.isFinite(date.getTime())
      ? `${date.toLocaleString(undefined, { timeZoneName: 'shortOffset', hour12: false })} (${date.toISOString()})`
      : '未选择有效时间';
  };
  return (
    <div className="space-y-1 break-words rounded-lg bg-muted p-3 text-xs text-muted-foreground">
      <p>时区：{timezone}（输入为本地时间，提交为 ISO 绝对时间）</p>
      <p>
        绝对范围：{format(from)} → {format(to)}
      </p>
    </div>
  );
}
export function CallbackCompensationScope({
  state,
  capabilities,
}: {
  state: CallbackCompensationState;
  capabilities: CallbackCompensationCapabilities;
}) {
  const receiver = capabilities.configs.find((config) => config.configId === state.configId);
  return (
    <div className="grid gap-4 md:grid-cols-[1.15fr_1fr]">
      <section className="space-y-4 rounded-xl border border-border bg-card p-5">
        <div>
          <p className="text-[10px] tracking-widest text-muted-foreground">COMPENSATION SCOPE</p>
          <h3 className="mt-1 font-medium">选择补偿范围</h3>
        </div>
        <p className="text-xs leading-6 text-muted-foreground">
          默认近 24 小时。这里只补发成功回调通知，不重跑工作流、不修改运行结果。
        </p>
        <label className="block space-y-2 text-xs">
          接收配置
          <select
            aria-label="接收配置"
            className="h-9 w-full rounded-lg border border-input bg-background px-3 text-foreground disabled:opacity-50"
            value={state.configId}
            disabled={!!state.busy}
            onChange={(event) => state.updateRange({ configId: event.target.value })}
          >
            {!capabilities.configs.length && <option value="">没有可用的接收配置</option>}
            {capabilities.configs.map((config) => (
              <option key={config.configId} value={config.configId}>
                {config.name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex gap-1 rounded-lg border border-border bg-muted p-1" aria-label="补偿范围方式">
          <Button
            size="sm"
            variant={state.mode === 'time' ? 'default' : 'ghost'}
            aria-pressed={state.mode === 'time'}
            disabled={!!state.busy}
            onClick={() => state.updateRange({ mode: 'time' })}
          >
            按结束时间
          </Button>
          <Button
            size="sm"
            variant={state.mode === 'ids' ? 'default' : 'ghost'}
            aria-pressed={state.mode === 'ids'}
            disabled={!!state.busy}
            onClick={() => state.updateRange({ mode: 'ids' })}
          >
            按运行 ID
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-2 text-xs">
            开始时间
            <Input
              type="datetime-local"
              aria-label="开始时间"
              value={state.range.from}
              disabled={!!state.busy || state.mode === 'ids'}
              onChange={(event) => state.updateRange({ from: event.target.value })}
            />
          </label>
          <label className="space-y-2 text-xs">
            结束时间
            <Input
              type="datetime-local"
              aria-label="结束时间"
              value={state.range.to}
              disabled={!!state.busy || state.mode === 'ids'}
              onChange={(event) => state.updateRange({ to: event.target.value })}
            />
          </label>
        </div>
        {state.mode === 'time' ? (
          <CallbackCompensationTime {...state.range} />
        ) : (
          <p className="rounded-lg bg-muted p-3 text-xs text-muted-foreground">
            仅按指定运行 ID 预览，不发送时间范围；服务端逐项核对工作流归属与资格。
          </p>
        )}
        <label className="block space-y-2 text-xs">
          运行 IDs（可选）
          <Textarea
            aria-label="运行 IDs（可选）"
            value={state.runIds}
            disabled={!!state.busy || state.mode === 'time'}
            placeholder="逗号、空格或换行分隔，最多 100 个；仅按运行 ID 时必填"
            onChange={(event) => state.updateRange({ runIds: event.target.value })}
          />
        </label>
        <Button loading={state.busy === 'preview'} disabled={!!state.busy || !receiver} onClick={state.generatePreview}>
          生成预览
        </Button>
      </section>
      <section className="space-y-5 rounded-xl border border-border bg-card p-5">
        <div>
          <p className="text-[10px] tracking-widest text-muted-foreground">VERIFIED RECEIVER</p>
          <h3 className="mt-1 font-medium">服务端脱敏配置</h3>
        </div>
        {receiver ? (
          <>
            <div className="flex items-start gap-3 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <BellRing aria-hidden className="mt-1 size-5 shrink-0 text-primary" />
              <div className="min-w-0 space-y-2">
                <p className="text-sm font-medium">{receiver.name}</p>
                <Badge tone="primary">{capabilities.environment}</Badge>
                <p className="break-all font-mono text-xs text-muted-foreground">{receiver.targetOrigin}</p>
              </div>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-xs">
              <dt className="text-muted-foreground">配置 ID</dt>
              <dd className="break-all font-mono">{receiver.configId}</dd>
              <dt className="text-muted-foreground">逻辑接收者</dt>
              <dd className="break-all font-mono">{receiver.receiverId ?? '服务端未提供'}</dd>
              <dt className="text-muted-foreground">凭据与完整地址</dt>
              <dd>不读取、不展示、不允许自定义</dd>
            </dl>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">服务端未提供可预览的接收配置。</p>
        )}
        <div className="flex gap-2 rounded-lg bg-muted p-3 text-xs leading-6 text-muted-foreground">
          <ShieldCheck aria-hidden className="mt-1 size-4 shrink-0" />
          <p>预览资格、接收方幂等与执行开关均由服务端验证。配置版本变化不会在前端解锁重复投递。</p>
        </div>
      </section>
    </div>
  );
}
