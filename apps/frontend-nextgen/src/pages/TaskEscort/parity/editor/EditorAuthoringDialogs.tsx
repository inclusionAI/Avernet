import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import type { EditorAuthoringPublish } from '@/hooks/taskEscortParity/useEditorAuthoringPublish';
import { useId, useState } from 'react';
import { EditorCode, EditorDialog } from './EditorPrimitives';

export function EditorPublishDialog({
  open,
  busy,
  note,
  onNote,
  onClose,
  onPublish,
}: {
  open: boolean;
  busy: boolean;
  note: string;
  onNote: (value: string) => void;
  onClose: () => void;
  onPublish: () => void;
}) {
  return (
    <EditorDialog
      open={open}
      onOpenChange={(value) => {
        if (!value && !busy) onClose();
      }}
      title="发布工作流"
      description="发布前会重新校验并保存当前草稿，保存后先展示真实文件差异，审阅确认后才发送发布请求。"
    >
      <p className="m-0 text-xs leading-5 text-muted-foreground">
        发布会更新生效版本。AI 生成或应用草稿不会触发此操作；发布失败会保留已经保存的内容。
      </p>
      <label htmlFor="editor-publish-note" className="text-xs font-medium">
        发布说明（可选）
      </label>
      <Input id="editor-publish-note" value={note} disabled={busy} onChange={(event) => onNote(event.target.value)} />
      <footer className="flex justify-end gap-2">
        <Button variant="outline" disabled={busy} onClick={onClose}>
          暂不发布
        </Button>
        <Button disabled={busy} onClick={onPublish}>
          {busy ? '保存发布中…' : '保存并预览发布差异'}
        </Button>
      </footer>
    </EditorDialog>
  );
}
export function EditorLeaveDialog({
  open,
  onStay,
  onLeave,
}: {
  open: boolean;
  onStay: () => void;
  onLeave: () => void;
}) {
  return (
    <EditorDialog
      open={open}
      onOpenChange={(value) => {
        if (!value) onStay();
      }}
      title="有未保存的草稿"
      description="离开或刷新会丢弃当前未保存的 YAML 和画布草稿；AI 候选不会自动保存。"
    >
      <footer className="flex justify-end gap-2">
        <Button variant="outline" onClick={onStay}>
          继续编辑
        </Button>
        <Button variant="destructive" onClick={onLeave}>
          放弃草稿并继续
        </Button>
      </footer>
    </EditorDialog>
  );
}

export function EditorPublishReviewDialog({ publication }: { publication: EditorAuthoringPublish }) {
  const [confirmed, setConfirmed] = useState(false);
  const confirmationId = useId();
  const candidate = publication.candidate;
  if (!candidate) return null;
  return (
    <EditorDialog
      open
      onOpenChange={(open) => {
        if (!open) publication.close();
      }}
      title="审阅发布文件差异"
      description="以下为已保存候选与当前生效版本的真实文件差异；发布仅使用本次保存的 commit。"
    >
      <p className="break-all font-mono text-xs">candidate: {candidate.review.candidateCommit}</p>
      {publication.stale && (
        <p role="alert" className="text-xs text-destructive">
          当前 YAML 已变化，不能发布旧候选；请重新保存并预览。
        </p>
      )}
      {candidate.review.changes.length === 0 && (
        <p className="text-xs text-muted-foreground">服务端未返回文件变更，请核对候选版本。</p>
      )}
      {candidate.review.changes.map((change) => (
        <details key={change.path} className="space-y-2 rounded-lg border border-input p-3 text-xs" open>
          <summary className="cursor-pointer font-medium">
            {change.operation} · {change.path}
          </summary>
          <p className="font-mono">文件权限：{change.beforeMode ?? '不存在'} → {change.afterMode ?? '不存在'}</p>
          <p>发布前{change.beforeEncoding === 'base64' ? ' · 二进制 Base64（完整内容）' : ''}</p>
          <EditorCode label={`${change.path} 发布前`} text={change.before ?? '（不存在）'} />
          <p>发布后{change.afterEncoding === 'base64' ? ' · 二进制 Base64（完整内容）' : ''}</p>
          <EditorCode label={`${change.path} 发布后`} text={change.after ?? '（已删除）'} />
        </details>
      ))}
      {publication.error && (
        <p role="alert" className="text-xs text-destructive">
          {publication.error}
        </p>
      )}
      <label htmlFor={confirmationId} className="flex items-start gap-2 text-xs leading-5">
        <Checkbox
          id={confirmationId}
          checked={confirmed}
          onCheckedChange={setConfirmed}
          disabled={!!publication.phase || publication.stale}
        />
        我已审阅全部文件差异，确认发布此候选版本
      </label>
      <footer className="flex justify-end gap-2">
        <Button variant="outline" disabled={!!publication.phase} onClick={publication.close}>
          暂不发布
        </Button>
        <Button
          disabled={!confirmed || !!publication.phase || publication.stale}
          onClick={() => void publication.publish(confirmed)}
        >
          {publication.phase === 'publish' ? '发布中…' : '确认发布候选版本'}
        </Button>
      </footer>
    </EditorDialog>
  );
}
