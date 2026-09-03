import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import type { BotRenderScreen, BotRenderScreenInput } from '@/domain/botEditor';
import { Pencil, Plus, Smartphone, Trash2 } from 'lucide-react';
import { useState } from 'react';

export function RenderScreenPanel({
  screens,
  editable,
  onSave,
  onDelete,
}: {
  screens: BotRenderScreen[];
  editable: boolean;
  onSave: (input: BotRenderScreenInput, id?: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [editing, setEditing] = useState<BotRenderScreen>();
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [cdnUrl, setCdnUrl] = useState('');
  const start = (screen?: BotRenderScreen) => {
    setFormOpen(true);
    setEditing(screen);
    setName(screen?.name ?? '');
    setCdnUrl(screen?.cdnUrl ?? '');
  };
  const close = () => {
    setFormOpen(false);
    setEditing(undefined);
    setName('');
    setCdnUrl('');
  };
  const save = async () => {
    await onSave({ name: name.trim(), cdnUrl: cdnUrl.trim() }, editing?.id);
    close();
  };
  return (
    <div className="p-5 sm:p-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>副屏组件库</CardTitle>
            <p className="mt-1 text-xs text-[var(--color-muted)]">维护当前 Bot 可加载的 UMD CDN 映射。</p>
          </div>
          <Button disabled={!editable} leftIcon={<Plus className="size-4" />} onClick={() => start()}>
            新增副屏
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {formOpen ? (
            <Card className="grid gap-3 bg-muted/30 p-4 shadow-none">
              <Input
                value={name}
                disabled={!editable}
                placeholder="组件库名称"
                onChange={(e) => setName(e.target.value)}
              />
              <Input
                value={cdnUrl}
                disabled={!editable}
                placeholder="https://cdn.example.com/screen.umd.js"
                onChange={(e) => setCdnUrl(e.target.value)}
              />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={close}>
                  取消
                </Button>
                <Button
                  disabled={!editable || !name.trim() || !/^https?:\/\//.test(cdnUrl.trim())}
                  onClick={() => void save()}
                >
                  保存
                </Button>
              </div>
            </Card>
          ) : null}
          {screens.length ? (
            screens.map((screen) => (
              <div key={screen.id} className="flex items-center gap-3 rounded-lg border border-border p-3">
                <div className="flex size-9 items-center justify-center rounded-lg bg-muted">
                  <Smartphone className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="m-0 truncate text-xs font-medium">{screen.name}</p>
                  <p className="m-0 mt-1 truncate text-xs text-[var(--color-muted)]">{screen.cdnUrl}</p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={!editable}
                  aria-label={`编辑${screen.name}`}
                  leftIcon={<Pencil className="size-4" />}
                  onClick={() => start(screen)}
                />
                <ConfirmDialog
                  title="删除副屏配置"
                  description={`确认删除「${screen.name}」？`}
                  confirmVariant="destructive"
                  disabled={!editable}
                  onConfirm={() => onDelete(screen.id)}
                >
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`删除${screen.name}`}
                    leftIcon={<Trash2 className="size-4" />}
                  />
                </ConfirmDialog>
              </div>
            ))
          ) : (
            <Empty compact title="暂无副屏配置" description="新增组件库名称和公开 CDN 地址后即可供副屏加载。" />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
