import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
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
          visibleChannels.map((channel) => (
            <div key={channel.id} className="flex items-center gap-4 rounded-lg border border-border p-3">
              <div className="min-w-0 flex-1">
                <p className="m-0 font-medium">钉钉 · {channel.description || channel.clientId}</p>
                <p className="m-0 mt-1 text-xs text-muted-foreground">
                  {bindingMode === 'bcn_gateway' ? `Robot Code：${channel.robotCode || '未配置'} · ` : ''}
                  Client ID：{channel.clientId} · Secret {channel.hasSecret ? '已配置' : '未配置'}
                </p>
                <p className="m-0 mt-1 text-xs text-muted-foreground">
                  流式输出：{channel.enableStreamingCards ? '已开启' : '已关闭'}
                  {bindingMode === 'plugin'
                    ? ` · 私聊：${channel.dmPolicy === 'open' ? '允许' : '禁止'}`
                    : ` · 会话：${channel.groupChatScope === 'conversation_shared' ? '群内共享' : '按发送者隔离'}`}
                  {channel.createdAt
                    ? ` · 创建于 ${new Date(channel.createdAt).toLocaleString('zh-CN', { hour12: false })}`
                    : ''}
                </p>
              </div>
              <Badge tone={channel.status === 'active' ? 'success' : 'neutral'}>
                {channel.status === 'active' ? '已启用' : '已停用'}
              </Badge>
              <Button variant="secondary" size="sm" disabled={!editable} onClick={() => void onToggle(channel)}>
                {channel.status === 'active' ? '停用' : '启用'}
              </Button>
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
                <Button variant="ghost" size="icon" aria-label="删除渠道" leftIcon={<Trash2 className="size-4" />} />
              </ConfirmDialog>
            </div>
          ))
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
