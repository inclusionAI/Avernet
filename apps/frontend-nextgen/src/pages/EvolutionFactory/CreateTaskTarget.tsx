import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { CreateDraft } from '@/domain/evolution/createTask';
import type { SpaceBot } from '@/domain/evolution/types';
import type { Dispatch, SetStateAction } from 'react';
import { ErrorBox, Field, Section } from './shared';

export function CreateTaskTarget({
  draft,
  set,
  setDraft,
  bots,
  botPage,
  setBotPage,
}: {
  draft: CreateDraft;
  set: <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => void;
  setDraft: Dispatch<SetStateAction<CreateDraft>>;
  bots: { data?: { items: SpaceBot[] }; loading: boolean; error: string; reload: () => void };
  botPage: number;
  setBotPage: Dispatch<SetStateAction<number>>;
}) {
  const botItems = bots.data?.items ?? [];
  return (
    <>
      <Section title="任务信息" index={1}>
        <div className="max-w-[480px] space-y-5">
          <Field label="任务名称 *">
            <Input
              required
              maxLength={128}
              placeholder="请输入任务名称"
              value={draft.taskName}
              onChange={(e) => set('taskName', e.target.value)}
            />
          </Field>
          <Field label="备注（可选）">
            <Input
              maxLength={1000}
              placeholder="补充对任务的内容、范围或任务要求"
              value={draft.remark}
              onChange={(e) => set('remark', e.target.value)}
            />
          </Field>
        </div>
      </Section>
      <Section title="进化对象" index={2}>
        <ErrorBox message={bots.error} retry={bots.reload} />
        <div
          className="max-h-[380px] space-y-2 overflow-y-auto rounded-xl border bg-muted/20 p-2"
          role="radiogroup"
          aria-label="请选择 Bot"
        >
          {botItems.map((bot) => {
            const selected = draft.botId === bot.botId && draft.ownerId === bot.ownerId && draft.botEnv === bot.env;
            return (
              <Button
                variant="ghost"
                style={{ height: 'auto', whiteSpace: 'normal' }}
                type="button"
                role="radio"
                aria-checked={selected}
                key={JSON.stringify([bot.ownerId, bot.botId, bot.env])}
                className={`flex w-full items-center justify-between gap-4 rounded-xl border bg-card p-4 text-left ${
                  selected ? 'border-primary' : 'border-border'
                }`}
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    botId: bot.botId,
                    ownerId: bot.ownerId,
                    botEnv: bot.env,
                    unambiguousEnvironment: bot.unambiguousEnvironment,
                    sourceDiagnosisTaskId: '',
                    benchDomainId: '',
                    trainDomainId: '',
                    testDomainId: '',
                  }))
                }
              >
                <span className="min-w-0 space-y-2">
                  <strong className="block truncate">{bot.botName || bot.botId}</strong>
                  <span className="block break-all font-mono text-xs text-muted-foreground">
                    {bot.ownerId} / {bot.botId} / {bot.env}
                  </span>
                  <span className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span>
                      {bot.deviceProvider === 'baas'
                        ? 'BaaS · 推荐'
                        : bot.deviceProvider === 'arca'
                        ? 'ARCA · 不推荐'
                        : '平台未知'}
                    </span>
                    <span>{bot.activeEngine}</span>
                    <span>
                      {bot.hasServiceBot ? '已有服务 Bot' : bot.botType === 'service' ? '服务型 Bot' : '普通 Bot'}
                    </span>
                  </span>
                </span>
                <span
                  className={`h-4 w-4 shrink-0 rounded-full border ${
                    selected ? 'border-primary bg-primary' : 'border-border'
                  }`}
                />
              </Button>
            );
          })}
          {!bots.loading && !botItems.length && (
            <p className="p-4 text-sm text-muted-foreground">当前空间暂无支持 OpenClaw 的 Bot</p>
          )}
        </div>
        {(botPage > 1 || botItems.length === 50) && (
          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
            <span>第 {botPage} 页</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={botPage === 1}
              onClick={() => setBotPage((p) => p - 1)}
            >
              上一页
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={botItems.length < 50}
              onClick={() => setBotPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        )}
      </Section>
    </>
  );
}
