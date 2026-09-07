import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Switch } from '@/components/ui/Switch';
import { Textarea } from '@/components/ui/Textarea';
import type { BotEditorEngineStatus, BotEngineConfig } from '@/domain/botEditor';
import { Save } from 'lucide-react';
import { useEffect, useState } from 'react';

export type MoreConfigTab = 'engine' | 'md' | 'node' | 'channel' | 'approval' | 'screen';
const labels: Record<MoreConfigTab, string> = {
  engine: '引擎配置',
  md: 'MD 文档',
  node: '节点',
  channel: '渠道',
  approval: '发布审批',
  screen: '副屏',
};
export function MoreConfigPanel({
  tab,
  config,
  editable,
  engineStatus,
  approvalRequired,
  serviceBot,
  onConfigChange,
  onSave,
  onApprovalChange,
}: {
  tab: MoreConfigTab;
  config: BotEngineConfig;
  editable: boolean;
  engineStatus?: BotEditorEngineStatus;
  approvalRequired: boolean;
  serviceBot: boolean;
  onConfigChange: (value: BotEngineConfig) => void;
  onSave: () => Promise<void>;
  onApprovalChange: (enabled: boolean) => Promise<void>;
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
  if (tab === 'approval')
    return (
      <div className="flex min-h-full flex-col bg-card">
        <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="m-0 text-sm font-semibold">发布审批</h2>
            <p className="m-0 mt-1 text-xs text-muted-foreground">控制服务化 Bot 发布时是否必须经过审批。</p>
          </div>
          <Switch
            checked={approvalRequired}
            disabled={!editable || !serviceBot}
            onCheckedChange={(checked) => void onApprovalChange(checked)}
          />
        </div>
        {!serviceBot ? (
          <div className="px-5 py-4">
            <Empty compact title="仅服务化 Bot 支持" description="当前 Bot 尚未开启服务化，无需设置发布审批。" />
          </div>
        ) : null}
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
        <Button
          size="sm"
          disabled={!editable || Boolean(error)}
          leftIcon={<Save className="size-4" />}
          onClick={() => onSave()}
        >
          保存配置
        </Button>
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
