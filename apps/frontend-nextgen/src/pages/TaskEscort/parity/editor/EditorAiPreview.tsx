import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { EditorAiDraft } from '@/hooks/taskEscortParity/useEditorAiDraft';
import { useId, useState } from 'react';
import { EditorCode } from './EditorPrimitives';

export function EditorAiPreview({ ai, blocked }: { ai: EditorAiDraft; blocked: boolean }) {
  const [reviewed, setReviewed] = useState(false);
  const reviewId = useId();
  const candidate = ai.candidate;
  if (!candidate?.job.result) return null;
  const before = new Map(candidate.base.nodes.map((node) => [node.id, node]));
  const after = new Map(candidate.spec?.nodes.map((node) => [node.id, node]) ?? []);
  const changed = [...new Set([...before.keys(), ...after.keys()])].filter(
    (id) => JSON.stringify(before.get(id)) !== JSON.stringify(after.get(id)),
  );
  const metadata = candidate.spec
    ? [...new Set([...Object.keys(candidate.base), ...Object.keys(candidate.spec)])].filter(
        (key) => key !== 'nodes' && JSON.stringify(candidate.base[key]) !== JSON.stringify(candidate.spec?.[key]),
      )
    : [];
  const disabled = blocked || !!ai.phase || ai.stale;
  return (
    <section aria-label="AI 草稿预览" className="space-y-3 border-t border-input pt-4">
      <div className="rounded-lg border border-input bg-card p-3">
        <h3 className="m-0 text-sm font-semibold">草稿待审阅</h3>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-5">
          {candidate.job.result.summary || '已生成工作流草稿，请审阅差异后应用'}
        </p>
      </div>
      {ai.stale && (
        <p role="status" className="rounded-lg bg-muted p-3 text-xs">
          当前工作流已更改，草稿已过期。请重新生成后再应用。
        </p>
      )}
      <details className="rounded-lg border border-input p-3 text-xs" open>
        <summary className="cursor-pointer font-medium">查看完整 YAML 草稿</summary>
        <div className="mt-3">
          <EditorCode label="生成的 YAML" text={candidate.job.result.yaml} />
        </div>
      </details>
      <Button size="sm" variant="outline" className="w-full" disabled={disabled} onClick={() => void ai.validate()}>
        {ai.phase === 'validate' ? '校验候选中…' : '校验 AI 草稿'}
      </Button>
      {candidate.spec && (
        <>
          <p role="status" className="text-xs text-success">
            候选校验通过 · 节点变更 {changed.length} 项
          </p>
          {changed.map((id) => (
            <details key={id} className="rounded-lg border border-input p-3 text-xs">
              <summary className="cursor-pointer font-medium">
                {!before.has(id) ? '新增' : !after.has(id) ? '删除' : '更新'} · {id} · 查看节点前后对比
              </summary>
              <div className="mt-3 space-y-2">
                <p className="text-muted-foreground">修改前</p>
                <EditorCode label={`${id} 修改前`} text={before.has(id) ? editorYaml(before.get(id)) : '（不存在）'} />
                <p className="text-muted-foreground">修改后</p>
                <EditorCode label={`${id} 修改后`} text={after.has(id) ? editorYaml(after.get(id)) : '（已删除）'} />
              </div>
            </details>
          ))}
          {metadata.length > 0 && (
            <section className="space-y-2 text-xs">
              <h4 className="font-semibold">顶层元数据变更</h4>
              {metadata.map((key) => (
                <details key={key} className="rounded-lg border border-input p-3">
                  <summary className="cursor-pointer">查看元数据前后对比 · {key}</summary>
                  <div className="mt-3 space-y-2">
                    <EditorCode label={`元数据 ${key} 修改前`} text={editorYaml(candidate.base[key])} />
                    <EditorCode label={`元数据 ${key} 修改后`} text={editorYaml(candidate.spec?.[key])} />
                  </div>
                </details>
              ))}
            </section>
          )}
          {candidate.warnings.map((warning, index) => (
            <p key={index} className="text-xs text-muted-foreground">
              {warning}
            </p>
          ))}
          <label
            htmlFor={reviewId}
            className="flex items-start gap-2 rounded-lg border border-input p-3 text-xs leading-5"
          >
            <Checkbox
              id={reviewId}
              checked={reviewed}
              disabled={disabled}
              onCheckedChange={(value) => setReviewed(value === true)}
            />
            我已审阅并确认以上变更（包括删除节点或修改顶层元数据）
          </label>
        </>
      )}
      <Button
        size="sm"
        className="w-full"
        disabled={disabled || !candidate.spec || !reviewed}
        onClick={() => void ai.apply()}
      >
        {ai.phase === 'apply' ? '应用中…' : '应用草稿到当前 Editor'}
      </Button>
      <p className="m-0 text-xs text-muted-foreground">仅更新画布与 YAML；请审阅后自行保存或发布。</p>
    </section>
  );
}
