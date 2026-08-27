import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotChannel, BotChannelInput } from '@/domain/botAdvancedConfig';
import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

const empty: BotChannelInput = { description: '', clientId: '', clientSecret: '' };
export function ChannelConfigPanel({
  channels,
  editable,
  onCreate,
  onToggle,
  onDelete,
}: {
  channels: BotChannel[];
  editable: boolean;
  onCreate: (input: BotChannelInput) => Promise<void>;
  onToggle: (channel: BotChannel) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="m-0 text-base font-semibold">渠道</h2>
          <p className="mt-1 text-xs text-[var(--color-muted)]">绑定钉钉机器人，并管理草稿渠道的启停。</p>
        </div>
        <Button disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => setOpen(true)}>
          绑定渠道
        </Button>
      </div>
      {channels.length ? (
        channels.map((channel) => (
          <Card key={channel.id}>
            <CardContent className="flex items-center gap-4">
              <div className="min-w-0 flex-1">
                <p className="m-0 font-medium">钉钉 · {channel.description || channel.clientId}</p>
                <p className="m-0 mt-1 text-xs text-[var(--color-muted)]">
                  Client ID：{channel.clientId} · Secret {channel.hasSecret ? '已配置' : '未配置'}
                </p>
              </div>
              <Badge tone={channel.status === 'active' ? 'success' : 'neutral'}>
                {channel.status === 'active' ? '已启用' : '已停用'}
              </Badge>
              <Button variant="secondary" size="sm" disabled={!editable} onClick={() => void onToggle(channel)}>
                {channel.status === 'active' ? '停用' : '启用'}
              </Button>
              <ConfirmDialog
                title="删除渠道"
                description="删除后需重新配置凭证。"
                confirmVariant="destructive"
                disabled={!editable}
                onConfirm={() => onDelete(channel.id)}
              >
                <Button variant="ghost" size="icon" aria-label="删除渠道" leftIcon={<Trash2 className="size-4" />} />
              </ConfirmDialog>
            </CardContent>
          </Card>
        ))
      ) : (
        <Empty title="暂无绑定渠道" description="当前仅支持钉钉渠道。" />
      )}
      <Modal open={open} onOpenChange={setOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>绑定钉钉渠道</ModalTitle>
          </ModalHeader>
          <div className="space-y-3">
            <Input
              placeholder="用途说明"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
            <Input
              placeholder="Client ID"
              value={form.clientId}
              onChange={(e) => setForm({ ...form, clientId: e.target.value })}
            />
            <Input
              type="password"
              placeholder="Client Secret"
              value={form.clientSecret}
              onChange={(e) => setForm({ ...form, clientSecret: e.target.value })}
            />
          </div>
          <ModalFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!form.clientId.trim() || !form.clientSecret.trim()}
              onClick={() =>
                void onCreate(form).then(() => {
                  setOpen(false);
                  setForm(empty);
                })
              }
            >
              保存
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
