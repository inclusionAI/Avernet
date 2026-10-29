import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import { useDwsHosting } from '@/hooks/useDwsHosting';
import { ArrowLeft, ArrowRight, ExternalLink, RefreshCw, Save, ShieldCheck } from 'lucide-react';

export function DwsHostingPanel({ botId }: { botId: string }) {
  const hosting = useDwsHosting(botId);
  const { status, draft, config, busy, loading, dirty, error } = hosting;
  if (loading)
    return (
      <div className="p-6 text-sm text-muted-foreground" role="status">
        正在加载消息托管…
      </div>
    );
  if (!draft || !status)
    return (
      <div className="space-y-4 p-6">
        <p role="alert">{error || '消息托管暂不可用'}</p>
        <Button variant="outline" onClick={hosting.reload}>
          <RefreshCw className="size-4" />
          重试
        </Button>
      </div>
    );
  const loginPending = ['starting', 'pending'].includes(status.authorization);
  const canStart = config!.version > 0 && !dirty && status.authorization === 'authorized';
  const patch = (values: Partial<typeof draft>) => hosting.setDraft({ ...draft, ...values });
  const challengeUrl = status.challenge?.verification_url;
  // COSEC: the server allowlists login hosts; never render an executable scheme.
  const safeUrl = challengeUrl?.startsWith('https://') ? challengeUrl : undefined;
  return (
    <div className="flex min-h-full flex-col bg-background">
      {/* 通栏 header:h-14 与其它子菜单(能力/资源/定时等)同高,同为 56px。 */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-5">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">消息托管</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">钉钉 · 全部单聊</p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-xs" role="status">
            {status.enabled ? (status.error ? '运行异常' : status.intake_ready ? '托管中' : '恢复消息中') : '未开启'}
          </span>
          <Switch
            aria-label="开启消息托管"
            checked={status.enabled}
            disabled={busy || (!status.enabled && !canStart)}
            onCheckedChange={hosting.enable}
          />
        </div>
      </header>
      <div className="mx-auto w-full max-w-3xl space-y-6 p-4 sm:p-6">
        {error && (
          <div className="space-y-2">
            <p role="alert" className="break-words text-sm text-destructive">
              {error}
            </p>
            <Button variant="outline" disabled={busy} onClick={hosting.reload}>
              <RefreshCw className="size-4" />
              重新载入已保存规则
            </Button>
          </div>
        )}
        {status.error && (
          <p role="alert" className="text-sm text-destructive">
            托管已停止，请关闭后检查授权和运行环境。
          </p>
        )}
        {(status.unknown_executions > 0 || status.unknown_deliveries > 0) && (
          <p role="alert" className="text-sm text-destructive">
            存在结果待核实的任务：执行 {status.unknown_executions} 条，发送 {status.unknown_deliveries}{' '}
            条。相关会话已暂停自动处理。
          </p>
        )}
        <section className="space-y-3 border-b border-border pb-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-medium">钉钉授权</h3>
            <span className="text-xs text-muted-foreground">
              {status.authorization === 'authorized'
                ? '已授权'
                : loginPending
                ? '等待授权'
                : status.authorization === 'failed'
                ? '授权失败或已超时'
                : '待检测'}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={busy || status.enabled || loginPending} onClick={hosting.check}>
              <ShieldCheck className="size-4" />
              检测授权
            </Button>
            <Button variant="outline" disabled={busy || status.enabled || loginPending} onClick={hosting.authorize}>
              <ExternalLink className="size-4" />
              授权钉钉
            </Button>
          </div>
          {status.challenge && (
            <div className="space-y-2 rounded-md border border-border p-3">
              <p className="text-sm">
                设备码：<strong className="font-mono">{status.challenge.user_code}</strong>
              </p>
              {safeUrl && (
                <a href={safeUrl} target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline">
                  前往钉钉完成授权
                </a>
              )}
            </div>
          )}
        </section>
        <fieldset disabled={busy} className="space-y-5">
          <div className="space-y-2">
            <h3 className="text-sm font-medium">
              托管时段 <span className="font-normal text-muted-foreground">（北京时间）</span>
            </h3>
            <div className="flex flex-wrap gap-4">
              {(
                [
                  ['always', '一直托管'],
                  ['night', '仅夜间（20:00–08:30）'],
                  ['custom', '自定义'],
                ] as const
              ).map(([value, label]) => (
                <label className="flex items-center gap-2 text-xs" key={value}>
                  <input
                    type="radio"
                    name={`hosting-window-${botId}`}
                    value={value}
                    checked={draft.window === value}
                    onChange={() => patch({ window: value })}
                  />
                  {label}
                </label>
              ))}
            </div>
            {draft.window === 'custom' && (
              <Input
                aria-label="自定义托管时段"
                maxLength={1000}
                value={draft.custom_window}
                onChange={(e) => patch({ custom_window: e.target.value })}
              />
            )}
          </div>
          <label className="block space-y-2 text-sm font-medium">
            回复规则
            <Textarea
              aria-label="回复规则"
              rows={5}
              maxLength={10000}
              value={draft.rules}
              onChange={(e) => patch({ rules: e.target.value })}
            />
          </label>
          <label className="block space-y-2 text-sm font-medium">
            人格 / 回复风格
            <Textarea
              aria-label="回复风格"
              rows={3}
              maxLength={2000}
              value={draft.persona}
              onChange={(e) => patch({ persona: e.target.value })}
            />
          </label>
          <div className="flex items-center justify-between gap-4 text-sm">
            <span>转告本人</span>
            <span className="text-muted-foreground">Bot 内置钉钉通知</span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <span className="text-xs text-muted-foreground">
              {dirty ? '有未保存的修改' : `已保存 · 版本 ${config!.version}`}
            </span>
            <Button
              disabled={
                !draft.rules.trim() ||
                (!dirty && config!.version > 0) ||
                (draft.window === 'custom' && !draft.custom_window.trim())
              }
              onClick={hosting.save}
            >
              <Save className="size-4" />
              保存并确认规则
            </Button>
          </div>
        </fieldset>
        <section className="space-y-3 border-t border-border pt-5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">会话接管</h3>
            <Button variant="outline" disabled={busy} onClick={() => hosting.list(0)}>
              <RefreshCw className="size-4" />
              加载会话
            </Button>
          </div>
          {hosting.conversations?.items.map((item) => (
            <div
              key={item.conversation_id}
              className="flex items-center justify-between gap-3 border-b border-border py-3"
            >
              <span className="min-w-0 break-all text-xs">{item.conversation_id}</span>
              <Button
                className="shrink-0"
                variant="outline"
                disabled={busy}
                onClick={() => hosting.takeover(item.conversation_id, !item.taken_over)}
              >
                {item.taken_over ? '恢复托管' : '人工接管'}
              </Button>
            </div>
          ))}
          {hosting.conversations?.total === 0 && <p className="text-xs text-muted-foreground">暂无会话</p>}
          {hosting.conversations && (
            <div className="flex items-center justify-end gap-2">
              <Button
                aria-label="上一页"
                variant="outline"
                disabled={busy || hosting.offset === 0}
                onClick={() => hosting.list(hosting.offset - 20)}
              >
                <ArrowLeft className="size-4" />
              </Button>
              <span className="text-xs">共 {hosting.conversations.total} 个会话</span>
              <Button
                aria-label="下一页"
                variant="outline"
                disabled={busy || hosting.offset + 20 >= hosting.conversations.total}
                onClick={() => hosting.list(hosting.offset + 20)}
              >
                <ArrowRight className="size-4" />
              </Button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
