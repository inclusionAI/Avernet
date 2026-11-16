import { Button, Card, Input, Textarea } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupContextView } from '@/services/workspace/groupContextService';
import { useGroupPresets } from '../../hooks/useGroupPresets';

export function GroupPresetPanel({ view }: { view: GroupContextView }) {
  const { list, draft, setDraft, error, busy, canWrite, reload, savePreset, removePreset, cancelDraft } = useGroupPresets(view);
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">群规则</h3>
        {canWrite && <Button size="sm" disabled={busy || !!draft} onClick={() => setDraft({ name: '', description: '' })}>新增</Button>}
      </div>
      <p className="text-xs text-muted-foreground">为会话共享信息预定义名称和用途。</p>
      {error && (
        <div role="alert" className="space-y-2 text-sm text-destructive">
          <p>{error}</p>
          {!list && <Button size="sm" variant="outline" onClick={reload}>重试</Button>}
        </div>
      )}
      {!list && !error && <p role="status" className="text-sm text-muted-foreground">正在加载群规则…</p>}
      {list && !list.presets.length && <p className="text-sm text-muted-foreground">暂无群规则</p>}
      {canWrite && draft && (
        <Card className="p-3">
          <form className="space-y-3" onSubmit={(event) => {
            event.preventDefault();
            const preset = { name: draft.name.trim(), description: draft.description };
            if (!preset.name) return;
            void savePreset(preset, draft.originalName);
          }}>
            <label className="block space-y-1 text-sm">
              <span>名称</span>
              <Input required value={draft.name} disabled={busy} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
            <label className="block space-y-1 text-sm">
              <span>描述</span>
              <Textarea value={draft.description} disabled={busy} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" disabled={busy} onClick={cancelDraft}>取消</Button>
              <Button type="submit" disabled={busy || !draft.name.trim()}>保存</Button>
            </div>
          </form>
        </Card>
      )}
      {list?.presets.map((preset) => (
        <Card key={preset.name} className="space-y-2 p-3">
          <h4 className="break-words text-sm font-medium">{preset.name}</h4>
          <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{preset.description || '暂无描述'}</p>
          {canWrite && (
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" disabled={busy || !!draft} onClick={() => setDraft({ ...preset, originalName: preset.name })}>编辑</Button>
              <ConfirmDialog
                title={`删除「${preset.name}」？`}
                description="已创建的共享信息会保留。"
                confirmText="删除"
                confirmVariant="destructive"
                disabled={busy || !!draft}
                onConfirm={() => removePreset(preset.name)}
              >
                <Button size="sm" variant="ghost" className="text-destructive" disabled={busy || !!draft}>删除</Button>
              </ConfirmDialog>
            </div>
          )}
        </Card>
      ))}
    </section>
  );
}
