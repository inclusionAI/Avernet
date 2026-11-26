import { useEffect, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Segmented } from '@/components/ui/Segmented';
import { Textarea } from '@/components/ui/Textarea';
import { YamlCodeEditor } from '@/pages/Workspace/components/Modals/YamlEditor';
import {
  readManifestForm,
  seededManifestForm,
  writeManifestForm,
  type ManifestForm,
} from '@/domain/agentswarm/manifestForm';

type EditMode = 'form' | 'yaml';

/**
 * Edits a manifest either as a form or as raw YAML.
 *
 * Both modes write the same string; the switch is a view of one document, not a
 * choice of storage. Form mode only models the identity documents — the rest of
 * the manifest is carried through untouched — so switching to the form on a
 * hand-written file never drops the categories it does not render.
 */
export function ManifestEditor({
  value,
  onChange,
  height = 220,
}: {
  value: string;
  onChange: (next: string) => void;
  height?: number;
}) {
  // Start in YAML when the document already has content the form cannot show
  // faithfully (unparseable, or categories outside the form's model); otherwise
  // the form is the friendlier place to begin.
  const [mode, setMode] = useState<EditMode>(() => (value.trim() ? 'yaml' : 'form'));
  const [parseError, setParseError] = useState<string | null>(null);

  // The YAML is the source of truth. The form is a projection of it, re-derived
  // whenever the text changes from outside (another agent selected, a reference
  // resolved, a definition loaded) — never a second store that can drift.
  const [form, setForm] = useState<ManifestForm>(() => readManifestForm(value) ?? seededManifestForm());
  const lastEmitted = useRef(value);
  useEffect(() => {
    if (value === lastEmitted.current) return;
    lastEmitted.current = value;
    const parsed = readManifestForm(value);
    if (parsed) setForm(parsed);
  }, [value]);

  const switchMode = (next: EditMode) => {
    if (next === mode) return;
    if (next === 'form') {
      const parsed = readManifestForm(value);
      if (!parsed) {
        // Refusing the switch beats showing an empty form over a document the
        // user cannot see the shape of; the message says why.
        setParseError('当前 YAML 无法解析，修正后才能切换到表单编辑。');
        return;
      }
      setParseError(null);
      setForm(parsed);
    }
    setMode(next);
  };

  const updateForm = (next: ManifestForm) => {
    const yaml = writeManifestForm(next);
    setForm(next);
    lastEmitted.current = yaml;
    onChange(yaml);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>能力配置 YAML</Label>
        <div className="flex items-center gap-2">
          <Segmented
            aria-label="YAML 编辑方式"
            value={mode}
            onChange={(next) => switchMode(next as EditMode)}
            options={[
              { label: '表单', value: 'form' },
              { label: 'YAML', value: 'yaml' },
            ]}
          />
          {mode === 'form' ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => updateForm({ ...form, identity: [...form.identity, { type: 'SOUL.md', content: '' }] })}
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加文档
            </Button>
          ) : null}
        </div>
      </div>

      {parseError ? (
        <p role="alert" className="m-0 text-xs text-destructive">
          {parseError}
        </p>
      ) : null}

      {mode === 'form' ? (
        <div className="space-y-3">
          {form.identity.length === 0 ? (
            <p className="m-0 text-xs text-muted-foreground">
              还没有身份文档。至少需要一条（例如 SOUL.md），否则 Agent 没有人设。
            </p>
          ) : null}
          {form.identity.map((entry, index) => (
            <div key={index} className="space-y-2 rounded-lg border border-divider p-3">
              <div className="flex items-center gap-2">
                <Input
                  aria-label={`第 ${index + 1} 个文档类型`}
                  className="w-40"
                  value={entry.type}
                  onChange={(event) =>
                    updateForm({
                      ...form,
                      identity: form.identity.map((item, i) =>
                        i === index ? { ...item, type: event.target.value } : item,
                      ),
                    })
                  }
                />
                <span className="min-w-0 flex-1" />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`移除第 ${index + 1} 个文档`}
                  onClick={() =>
                    updateForm({ ...form, identity: form.identity.filter((_, i) => i !== index) })
                  }
                >
                  <Trash2 className="size-3.5 text-destructive" aria-hidden />
                </Button>
              </div>
              <Textarea
                aria-label={`第 ${index + 1} 个文档内容`}
                rows={5}
                value={entry.content}
                placeholder="这份文档的内容，例如人设描述。"
                onChange={(event) =>
                  updateForm({
                    ...form,
                    identity: form.identity.map((item, i) =>
                      i === index ? { ...item, content: event.target.value } : item,
                    ),
                  })
                }
              />
            </div>
          ))}
          <p className="m-0 text-xs leading-5 text-muted-foreground">
            表单只编辑身份文档；mcp、skills 等其他分类与手写注释请在 YAML 模式中维护。
          </p>
        </div>
      ) : (
        <YamlCodeEditor value={value} onChange={onChange} height={height} />
      )}
    </div>
  );
}