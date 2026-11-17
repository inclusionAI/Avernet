// 更多配置的分发视图：engine 有独立编辑面板，其余 tab 按占位/已实现内容渲染。
import { Badge } from '@/components/ui/Badge';
import { Empty } from '@/components/ui/Empty';
import { EngineConfigEditorPanel } from '@/components/BotWorkshop/Editor/EngineConfigEditorPanel';
import type { BotEditorEngineStatus, BotEngineConfig } from '@/domain/botEditor';

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
  savedConfig,
  editable,
  engineStatus,
  restoringDefaults,
  onConfigChange,
  onSave,
  onRestoreDefaults,
}: {
  tab: MoreConfigTab;
  config: BotEngineConfig;
  /** 最近一次落库成功的引擎配置，diff 基线 */
  savedConfig: BotEngineConfig;
  editable: boolean;
  engineStatus?: BotEditorEngineStatus;
  restoringDefaults?: boolean;
  onConfigChange: (value: BotEngineConfig) => void;
  onSave: () => Promise<void>;
  onRestoreDefaults: () => Promise<void>;
}) {
  if (tab === 'engine')
    return (
      <EngineConfigEditorPanel
        config={config}
        savedConfig={savedConfig}
        editable={editable}
        restoringDefaults={restoringDefaults}
        onConfigChange={onConfigChange}
        onSave={onSave}
        onRestoreDefaults={onRestoreDefaults}
      />
    );
  if (tab === 'node')
    return (
      <div className="flex min-h-full flex-col bg-card">
        <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-divider bg-card px-8 lg:px-10">
          <div className="min-w-0">
            <h2 className="m-0 truncate text-sm font-semibold">运行节点</h2>
            <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">查看当前 Bot 绑定的运行节点与引擎连接状态。</p>
          </div>
          <Badge tone={engineStatus?.running ? 'success' : 'neutral'}>
            {engineStatus?.running ? '运行中' : '未运行'}
          </Badge>
        </div>
        <div className="grid gap-4 px-7 py-5 sm:grid-cols-2">
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
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex h-14 shrink-0 items-center border-b border-divider bg-card px-8 lg:px-10">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold">{labels[tab]}</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">该配置项即将开放，敬请期待。</p>
        </div>
      </div>
      <div className="px-7 py-5">
        <Empty
          title={`${labels[tab]}配置待接入`}
          description="PRD 页面入口已保留；Avernet 当前缺少与该配置一一对应的公开 OpenAPI，本期不会模拟保存成功。"
        />
      </div>
    </div>
  );
}
