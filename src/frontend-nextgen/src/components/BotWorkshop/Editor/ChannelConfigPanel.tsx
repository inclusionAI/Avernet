import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Switch } from '@/components/ui/Switch';
import type { BotChannel, BotChannelInput, ChannelBindingMode } from '@/domain/botAdvancedConfig';
import { CircleHelp, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { ChannelFormModal } from './ChannelFormModal';

export function ChannelConfigPanel({
  channels,
  editable,
  onCreate,
  onUpdate,
  onToggle,
  onDelete,
}: {
  channels: BotChannel[];
  editable: boolean;
  onCreate: (input: BotChannelInput) => Promise<void>;
  onUpdate: (id: number, input: BotChannelInput) => Promise<void>;
  onToggle: (channel: BotChannel) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<BotChannel>();
  const [bindingMode, setBindingMode] = useState<ChannelBindingMode>('plugin');
  const visibleChannels = channels.filter((channel) => channel.bindingMode === bindingMode);
  const modeLabel = bindingMode === 'plugin' ? '基于开源插件' : '基于BCN';
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">渠道管理</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">分别查看和管理基于开源插件或 BCN 的钉钉机器人。</p>
        </div>
        <Button size="sm" disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => setOpen(true)}>
          新建配置
        </Button>
      </div>
      <div className="space-y-4 px-5 py-4">
        <section className="rounded-xl border border-border bg-background p-4">
          <div className="mb-3 flex items-start justify-between gap-4">
            <div>
              <p className="m-0 text-sm font-semibold">绑定方式</p>
              <p className="m-0 mt-1 text-xs text-muted-foreground">两种方式的凭证和消息策略互相独立。</p>
            </div>
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <CircleHelp className="size-3.5" /> 创建后不可切换
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist" aria-label="渠道绑定方式">
            {(
              [
                ['plugin', '基于开源插件'],
                ['bcn_gateway', '基于BCN'],
              ] as const
            ).map(([mode, label]) => (
              <Button
                key={mode}
                role="tab"
                aria-selected={bindingMode === mode}
                variant={bindingMode === mode ? 'secondary' : 'ghost'}
                className={bindingMode === mode ? 'bg-background shadow-sm hover:bg-background' : ''}
                onClick={() => setBindingMode(mode)}
              >
                {label}
              </Button>
            ))}
          </div>
        </section>
        <div className="flex items-center justify-between">
          <p className="m-0 text-sm font-semibold">钉钉机器人配置 ({visibleChannels.length})</p>
          <Badge tone="neutral">{modeLabel}</Badge>
        </div>
        {visibleChannels.length ? (
          <div className="overflow-x-auto rounded-lg border border-border">
            <div className="grid min-w-[780px] grid-cols-[minmax(150px,1.4fr)_90px_minmax(150px,1.2fr)_100px_150px_100px] gap-3 border-b border-border bg-muted px-4 py-2 text-xs font-medium text-muted-foreground">
              <span>场景描述</span>
              <span>绑定环境</span>
              <span>{bindingMode === 'plugin' ? '机器人 ID' : 'Robot Code'}</span>
              <span>状态</span>
              <span>创建时间</span>
              <span className="text-right">操作</span>
            </div>
            {visibleChannels.map((channel) => (
              <div
                key={channel.id}
                className="grid min-w-[780px] grid-cols-[minmax(150px,1.4fr)_90px_minmax(150px,1.2fr)_100px_150px_100px] items-center gap-3 border-b border-border px-4 py-3 text-xs last:border-b-0"
              >
                <div className="min-w-0">
                  <p className="m-0 truncate font-medium">{channel.description || '未命名场景'}</p>
                  <p className="m-0 mt-1 truncate text-muted-foreground">
                    流式输出：{channel.enableStreamingCards ? '已开启' : '已关闭'}
                  </p>
                </div>
                <Badge tone="neutral">草稿态</Badge>
                <span className="truncate">
                  {bindingMode === 'plugin' ? channel.clientId : channel.robotCode || '未配置'}
                </span>
                <span className="flex items-center gap-2">
                  <Switch
                    size="sm"
                    checked={channel.status === 'active'}
                    disabled={!editable}
                    aria-label={`${channel.description || channel.clientId}渠道状态`}
                    onCheckedChange={() => void onToggle(channel)}
                  />
                  {channel.status === 'active' ? '启用' : '停用'}
                </span>
                <span className="text-muted-foreground">
                  {channel.createdAt ? new Date(channel.createdAt).toLocaleString('zh-CN', { hour12: false }) : '--'}
                </span>
                <span className="flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={!editable}
                    aria-label={`编辑${channel.description || channel.clientId}`}
                    leftIcon={<Pencil className="size-4" />}
                    onClick={() => {
                      setEditing(channel);
                      setOpen(true);
                    }}
                  />
                  <ConfirmDialog
                    title="删除渠道"
                    description="删除后需重新配置凭证。"
                    confirmVariant="destructive"
                    disabled={!editable}
                    onConfirm={() => onDelete(channel.id)}
                  >
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`删除${channel.description || channel.clientId}`}
                      leftIcon={<Trash2 className="size-4" />}
                    />
                  </ConfirmDialog>
                </span>
              </div>
            ))}
          </div>
        ) : (
          <Empty title={`暂无${modeLabel}配置`} description="点击“新建配置”绑定钉钉机器人。" />
        )}
      </div>
      <ChannelFormModal
        open={open}
        channel={editing}
        bindingMode={bindingMode}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setEditing(undefined);
        }}
        onSubmit={(input) => (editing ? onUpdate(editing.id, input) : onCreate(input))}
      />
    </div>
  );
}
