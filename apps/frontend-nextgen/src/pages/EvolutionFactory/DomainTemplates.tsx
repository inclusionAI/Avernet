import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { Template } from '@/domain/evolution/types';
import type { Dispatch, SetStateAction } from 'react';
import { ErrorBox, Hint, Section } from './shared';

export function DomainTemplates({
  templates,
  writable,
  selected,
  setSelected,
  busy,
  batchPublish,
  startTemplate,
  archiveTemplate,
}: {
  templates: { data?: Template[]; loading: boolean; error: string; reload: () => void };
  writable: boolean;
  selected: string[];
  setSelected: Dispatch<SetStateAction<string[]>>;
  busy: boolean;
  batchPublish: () => Promise<void>;
  startTemplate: (name: string) => Promise<void>;
  archiveTemplate: (name: string) => Promise<unknown>;
}) {
  return (
    <Section title="评测模板">
      <ErrorBox message={templates.error} retry={templates.reload} />
      {writable && (
        <div className="mb-4 flex items-center gap-3">
          <span className="text-xs text-muted-foreground">已选择 {selected.length} 个待发布模板</span>
          <Button variant="outline" size="sm" disabled={!selected.length || busy} onClick={() => void batchPublish()}>
            批量发布
          </Button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] table-fixed text-left text-sm">
          <colgroup>
            {[55, 240, 240, 100, 100, 100, 165].map((width, i) => (
              <col key={i} style={{ width }} />
            ))}
          </colgroup>
          <thead className="bg-muted">
            <tr>
              {['选择', '模板名', '展示名称', '最新版本', '发布版本', '状态', '操作'].map((v) => (
                <th className="whitespace-nowrap p-3 text-xs font-medium" key={v}>
                  {v}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {templates.data?.map((t) => (
              <tr className="border-b border-divider" key={t.templateName}>
                <td className="p-3">
                  <Checkbox
                    aria-label={`选择模板 ${t.templateName}`}
                    disabled={!writable || t.versions.find((v) => v.version === t.latestVersion)?.status !== 'draft'}
                    checked={selected.includes(t.templateName)}
                    onCheckedChange={(checked) =>
                      setSelected((v) =>
                        checked === true ? [...v, t.templateName] : v.filter((n) => n !== t.templateName),
                      )
                    }
                  />
                </td>
                <td className="p-3">
                  <Hint content={t.templateName}>
                    <div className="truncate font-mono text-xs">{t.templateName}</div>
                  </Hint>
                </td>
                <td className="p-3">
                  <Hint content={t.displayName || t.templateName}>
                    <div className="truncate font-medium">{t.displayName || t.templateName}</div>
                  </Hint>
                </td>
                <td className="whitespace-nowrap p-3">
                  <span className="rounded bg-primary/10 px-2 py-1 text-xs text-primary">v{t.latestVersion}</span>
                </td>
                <td className="whitespace-nowrap p-3">
                  <span className="rounded bg-success/10 px-2 py-1 text-xs text-success">
                    {t.publishedVersion === null ? '—' : `v${t.publishedVersion}`}
                  </span>
                </td>
                <td className="whitespace-nowrap p-3">
                  <span
                    className={`rounded px-2 py-1 text-xs ${
                      t.status === 'published' ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'
                    }`}
                  >
                    {t.status === 'published' ? '已发布' : t.status === 'draft' ? '草稿' : t.status}
                  </span>
                </td>
                <td className="whitespace-nowrap p-3">
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void startTemplate(t.templateName)}
                    >
                      {writable ? '查看/编辑' : '查看'}
                    </Button>
                    {writable && (
                      <ConfirmDialog
                        title={`归档 ${t.templateName}？`}
                        onConfirm={async () => {
                          await archiveTemplate(t.templateName);
                        }}
                      >
                        <Button size="sm" variant="ghost" disabled={busy}>
                          归档
                        </Button>
                      </ConfirmDialog>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!templates.loading && !templates.error && !templates.data?.length && (
        <p className="mt-3 text-sm text-muted-foreground">暂无模板</p>
      )}
    </Section>
  );
}
