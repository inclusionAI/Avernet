import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import type { BotChannel, BotChannelInput } from '@/domain/botAdvancedConfig';
import { Pencil, Plus, Trash2 } from 'lucide-react';
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
  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="m-0 text-sm font-semibold">渠道</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">绑定钉钉机器人，并管理草稿渠道的启停。</p>
        </div>
        <Button size="sm" disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => setOpen(true)}>
          绑定渠道
        </Button>
      </div>
      <div className="space-y-3 px-5 py-4">
        {channels.length ? (
          channels.map((channel) => (
            <div key={channel.id} className="flex items-center gap-4 rounded-lg border border-border p-3">
              <div className="min-w-0 flex-1">
                <p className="m-0 font-medium">钉钉 · {channel.description || channel.clientId}</p>
                <p className="m-0 mt-1 text-xs text-muted-foreground">
                  Client ID：{channel.clientId} · Secret {channel.hasSecret ? '已配置' : '未配置'}
                </p>
                <p className="m-0 mt-1 text-xs text-muted-foreground">
                  流式输出：{channel.enableStreamingCards ? '已开启' : '已关闭'} · 私聊：
                  {channel.dmPolicy === 'open' ? '允许' : '禁止'}
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
          <Empty title="暂无绑定渠道" description="当前仅支持钉钉渠道。" />
        )}
      </div>
      <ChannelFormModal
        open={open}
        channel={editing}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setEditing(undefined);
        }}
        onSubmit={(input) => (editing ? onUpdate(editing.id, input) : onCreate(input))}
      />
    </div>
  );
}
