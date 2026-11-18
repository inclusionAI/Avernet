import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { EditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { EditorCode } from './EditorPrimitives';

export function EditorAuthoringYaml({ authoring, canEdit }: { authoring: EditorAuthoring; canEdit: boolean }) {
  return (
    <section aria-label="工作流规格 YAML" className="min-w-0 space-y-3 rounded-lg border border-input bg-card p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-xs font-semibold">{canEdit ? 'YAML 草稿' : '保存态 YAML · 只读'}</h3>
        {canEdit && <span className="text-xs text-muted-foreground">{authoring.dirty ? '未保存' : '已保存'}</span>}
      </header>
      {canEdit ? (
        <>
          <label htmlFor="editor-authoring-yaml" className="sr-only">
            工作流 YAML
          </label>
          <Textarea
            id="editor-authoring-yaml"
            value={authoring.yaml}
            onChange={(event) => authoring.editYaml(event.target.value)}
            disabled={!!authoring.phase}
            spellCheck={false}
            rows={22}
            aria-describedby="editor-yaml-help"
            className="min-h-[360px] resize-y whitespace-pre font-mono text-xs leading-5"
          />
          <p id="editor-yaml-help" className="m-0 text-xs leading-5 text-muted-foreground">
            编辑后先校验并应用到画布；保存也会校验当前完整 YAML。工作流标识和版本不可修改，未知扩展字段原样保留。
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={!!authoring.phase} onClick={() => void authoring.validate()}>
              {authoring.phase === 'validate' ? '校验中…' : '校验 YAML'}
            </Button>
            <Button size="sm" disabled={!!authoring.phase || !authoring.validated} onClick={authoring.applyYaml}>
              应用 YAML 到画布
            </Button>
          </div>
          {authoring.pendingYaml && (
            <p role="status" className="text-xs text-muted-foreground">
              YAML 有未应用内容，画布仍显示上次应用的草稿。
            </p>
          )}
          {authoring.warnings.map((warning, index) => (
            <p key={index} className="text-xs text-muted-foreground">
              {warning}
            </p>
          ))}
        </>
      ) : (
        <EditorCode label="工作流 YAML" text={authoring.yaml} />
      )}
    </section>
  );
}
