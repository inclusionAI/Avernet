import { Button } from '@/components/ui/Button';
import { authoringSpec } from '@/domain/taskEscortParity/workflowAuthoring';
import { useEffect, useState } from 'react';
import type { EditorSettingsProps } from '../EditorInspector';
import { EditorCode } from '../EditorPrimitives';
import { TextField } from './Fields';
export function SettingsDraft({ spec, onChange, disabled, onPendingChange, open }: EditorSettingsProps) {
  const [draft, setDraft] = useState(spec);
  const [error, setError] = useState('');
  const [applied, setApplied] = useState(JSON.stringify(spec));
  const dirty = JSON.stringify(draft) !== applied;
  useEffect(() => {
    onPendingChange?.(open && dirty);
  }, [open, dirty, onPendingChange]);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);
  const readonly = disabled || !onChange;
  const settings = Object.fromEntries(Object.entries(spec).filter(([key]) => key !== 'nodes'));
  return (
    <div className="space-y-4">
      <TextField
        label="流程名称"
        value={draft.title}
        disabled={readonly}
        onChange={(title) => setDraft({ ...draft, title })}
      />
      <TextField
        label="流程描述"
        value={draft.description}
        disabled={readonly}
        multiline
        onChange={(description) => setDraft({ ...draft, description })}
      />
      <details className="rounded-md border border-input p-3">
        <summary className="cursor-pointer text-sm">完整流程配置（不含节点，只读）</summary>
        <EditorCode label="流程配置 JSON" text={JSON.stringify(settings, null, 2)} />
        <p className="text-xs text-muted-foreground">复杂设置保留原值，不补默认参数、身份或输出。</p>
      </details>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {dirty && (
        <p role="status" className="text-xs text-muted-foreground">
          有未应用设置，请应用或放弃草稿后关闭。
        </p>
      )}
      <Button
        disabled={readonly || !dirty}
        onClick={() => {
          if (readonly) return;
          try {
            authoringSpec(draft, spec.id);
            onChange?.(draft);
            setApplied(JSON.stringify(draft));
            setError('');
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : String(cause));
          }
        }}
      >
        应用流程设置
      </Button>
      <Button
        variant="outline"
        onClick={() => {
          setDraft(spec);
          setApplied(JSON.stringify(spec));
          setError('');
        }}
      >
        放弃设置草稿
      </Button>
      <p className="text-xs text-muted-foreground">
        {readonly ? '设置只读。' : '显式应用仅更新本地草稿，不直接保存或发布。'}
      </p>
    </div>
  );
}
