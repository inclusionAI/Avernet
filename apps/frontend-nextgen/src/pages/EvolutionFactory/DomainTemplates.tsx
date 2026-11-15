import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Pagination } from '@/components/ui/Pagination';
import type { Template } from '@/domain/evolution/types';
import type { Dispatch, SetStateAction } from 'react';
import { useState } from 'react';
import { ErrorBox, Hint } from './shared';

export function DomainTemplates({
  templates,
  writable,
  selected,
  setSelected,
  busy,
  startTemplate,
  archiveTemplate,
}: {
  templates: { data?: Template[]; loading: boolean; error: string; reload: () => void };
  writable: boolean;
  selected: string[];
  setSelected: Dispatch<SetStateAction<string[]>>;
  busy: boolean;
  startTemplate: (name: string) => Promise<void>;
  archiveTemplate: (name: string) => Promise<unknown>;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const total = templates.data?.length ?? 0;
  const currentPage = Math.max(1, Math.min(page, Math.ceil(total / pageSize) || 1));
  const visible = templates.data?.slice((currentPage - 1) * pageSize, currentPage * pageSize) ?? [];
  return (
    <section aria-label="评测模板" className="min-w-0">
      <h2 className="sr-only">评测模板</h2>
      <ErrorBox message={templates.error} retry={templates.reload} />
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[1000px] table-fixed text-left text-sm">
          <colgroup>
            {[32, 216, 244, 144, 144, 216, 204].map((width, i) => (
              <col key={i} style={{ width }} />
            ))}
          </colgroup>
          <thead className="bg-muted">
            <tr>
              {['选择', '模板名', '展示名称', '最新版本', '发布版本', '状态', '操作'].map((v) => (
                <th className="whitespace-nowrap p-3 text-xs font-medium" key={v}>
                  {v === '选择' ? <span className="sr-only">选择</span> : v}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((t) => (
              <tr className="h-[54px] border-b border-divider" key={t.templateName}>
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
                    <div className="truncate text-[13px]">{t.templateName}</div>
                  </Hint>
                </td>
                <td className="p-3">
                  <Hint content={t.displayName || t.templateName}>
                    <div className="truncate font-medium">{t.displayName || t.templateName}</div>
                  </Hint>
                </td>
                <td className="whitespace-nowrap p-3">
                  <span className="text-[13px]">V{t.latestVersion}</span>
                </td>
                <td className="whitespace-nowrap p-3">
                  <span className="text-[13px]">{t.publishedVersion === null ? '—' : `V${t.publishedVersion}`}</span>
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
                      className="h-7 px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary"
                      disabled={busy}
                      onClick={() => void startTemplate(t.templateName)}
                    >
                      {writable ? '编辑' : '查看'}
                    </Button>
                    {writable && (
                      <ConfirmDialog
                        title={`归档 ${t.templateName}？`}
                        onConfirm={async () => {
                          await archiveTemplate(t.templateName);
                        }}
                      >
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary"
                          disabled={busy}
                        >
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
      {total > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {writable && <span className="text-xs text-muted-foreground">已选择 {selected.length} 个模板</span>}
          <div className="min-w-0 flex-1">
            <Pagination
              current={currentPage}
              pageSize={pageSize}
              total={total}
              onChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(1);
              }}
              layout="between"
            />
          </div>
        </div>
      )}
      {!templates.loading && !templates.error && !templates.data?.length && (
        <p className="mt-3 text-sm text-muted-foreground">暂无模板</p>
      )}
    </section>
  );
}
