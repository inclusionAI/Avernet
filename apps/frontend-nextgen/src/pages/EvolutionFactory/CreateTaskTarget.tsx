import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { CreateDraft } from '@/domain/evolution/createTask';
import type { SpaceBot } from '@/domain/evolution/types';
import type { Dispatch, SetStateAction } from 'react';
import { Choice, ErrorBox, Field, Section } from './shared';

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
        <div className="grid gap-x-5 gap-y-5 md:grid-cols-2">
          <Field label="任务名称 *" labelHint="任务名称最多 128 字，用于识别任务。">
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
        <div className="max-w-[490px]">
          <Field label="目标 Bot *">
            <Choice
              label="目标 Bot"
              value={draft.botId ? JSON.stringify([draft.ownerId, draft.botId, draft.botEnv]) : ''}
              disabled={bots.loading}
              options={botItems.map((bot) => ({
                value: JSON.stringify([bot.ownerId, bot.botId, bot.env]),
                label: `${bot.botName || bot.botId} · ${bot.ownerId} / ${bot.botId} / ${bot.env}`,
              }))}
              onChange={(value) => {
                const bot = botItems.find((item) => JSON.stringify([item.ownerId, item.botId, item.env]) === value);
                if (!bot) return;
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
                }));
              }}
            />
          </Field>
          {bots.loading && (
            <p role="status" className="mt-2 text-xs text-muted-foreground">
              正在加载 Bot…
            </p>
          )}
          {!bots.loading && !bots.error && !botItems.length && (
            <p className="mt-2 text-xs text-muted-foreground">当前空间暂无支持 OpenClaw 的 Bot</p>
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
