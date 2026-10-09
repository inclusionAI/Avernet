import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorEngineStatus, BotEngineConfig } from '@/domain/botEditor';
import { RotateCcw, Save } from 'lucide-react';
import { useEffect, useState } from 'react';

export type MoreConfigTab = 'engine' | 'md' | 'node' | 'channel' | 'screen';
const labels: Record<MoreConfigTab, string> = {
  engine: '引擎配置',
  md: 'MD 文档',
  node: '节点',
  channel: '渠道',
  screen: '副屏',
};
export function MoreConfigPanel({
  tab,
  config,
  editable,
  engineStatus,
  restoringDefaults,
  onConfigChange,
  onSave,
  onRestoreDefaults,
}: {
  tab: MoreConfigTab;
  config: BotEngineConfig;
  editable: boolean;
  engineStatus?: BotEditorEngineStatus;
  restoringDefaults?: boolean;
  onConfigChange: (value: BotEngineConfig) => void;
  onSave: () => Promise<void>;
  onRestoreDefaults: () => Promise<void>;
}) {
  const [text, setText] = useState('{}');
  const [error, setError] = useState('');
  useEffect(() => {
    setText(JSON.stringify(config, null, 2));
  }, [config]);
  if (tab === 'node')
    return (
      <div className="flex min-h-full flex-col bg-card">
        <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
          <h2 className="m-0 text-sm font-semibold">运行节点</h2>
          <Badge tone={engineStatus?.running ? 'success' : 'neutral'}>
            {engineStatus?.running ? '运行中' : '未运行'}
          </Badge>
        </div>
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <div className="rounded-lg border border-border p-4">
            <p className="text-xs text-[var(--color-muted)]">引擎</p>
            <p className="mt-1 font-medium">{engineStatus?.engine || '—'}</p>
          </div>
          <div className="rounded-lg border border-border p-4">
            <p className="text-xs text-[var(--color-muted)]">活跃连接</p>
            <p className="mt-1 font-medium">{engineStatus?.activeConnections ?? 0}</p>
          </div>
        </div>
      </div>
    );
  if (tab !== 'engine')
    return (
      <div className="flex min-h-full flex-col bg-card">
        <div className="border-b border-border px-5 py-4">
          <h2 className="m-0 text-sm font-semibold">{labels[tab]}</h2>
        </div>
        <div className="px-5 py-4">
          <Empty
            title={`${labels[tab]}配置待接入`}
            description="PRD 页面入口已保留；Avernet 当前缺少与该配置一一对应的公开 OpenAPI，本期不会模拟保存成功。"
          />
        </div>
      </div>
    );
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">引擎配置</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">直接读写 Bot 草稿态的自由 JSON 配置。</p>
        </div>
        <div className="flex items-center gap-2">
          <ConfirmDialog
            title="确认恢复默认配置？"
            description="当前引擎配置将被默认配置覆盖，保存后立即生效。"
            confirmText="确认恢复"
            loading={restoringDefaults}
            disabled={!editable || restoringDefaults}
            onConfirm={onRestoreDefaults}
          >
            <Button
              variant="outline"
              size="sm"
              disabled={!editable || restoringDefaults}
              leftIcon={<RotateCcw className="size-4" />}
            >
              恢复默认配置
            </Button>
          </ConfirmDialog>
          <Button
            size="sm"
            disabled={!editable || Boolean(error) || restoringDefaults}
            leftIcon={<Save className="size-4" />}
            onClick={() => onSave()}
          >
            保存配置
          </Button>
        </div>
      </div>
      <div className="px-5 py-4">
        <Textarea
          className="min-h-[440px] font-mono text-xs"
          value={text}
          disabled={!editable}
          onChange={(e) => {
            setText(e.target.value);
            try {
              const value = JSON.parse(e.target.value) as BotEngineConfig;
              setError('');
              onConfigChange(value);
            } catch {
              setError('请输入合法 JSON');
            }
          }}
        />
        {error ? <p className="mt-2 text-xs text-[var(--color-danger)]">{error}</p> : null}
      </div>
    </div>
  );
}
