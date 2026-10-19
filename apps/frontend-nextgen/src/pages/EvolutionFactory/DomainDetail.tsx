import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Spin } from '@/components/ui/Spin';
import { Textarea } from '@/components/ui/Textarea';
import type { Template } from '@/domain/evolution/types';
import { evolutionApi } from '@/services/backendApi/clawevolve/controller';
import { history, useParams } from '@umijs/max';
import { ArrowLeft, Pencil } from 'lucide-react';
import { useCallback, useState } from 'react';
import { DomainRuns } from './DomainRuns';
import { ErrorBox, EvolutionHeader, Field, JsonView, Section, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

export default function DomainDetail() {
  const context = useEvolutionContext();
  return <DomainDetailContent key={context.key} />;
}
function DomainDetailContent() {
  const { ownerUserId = '', domainId = '' } = useParams<{ ownerUserId: string; domainId: string }>();
  const identity = useEvolutionContext();
  const writable = identity.ready;
  const [runPage, setRunPage] = useState(1);
  const [tab, setTab] = useState<'detail' | 'tasks'>('detail');
  const domain = useResource(
    useCallback(() => evolutionApi.domain(ownerUserId, domainId), [ownerUserId, domainId]),
    identity.ready,
  );
  const templates = useResource(
    useCallback(() => evolutionApi.templates(ownerUserId, domainId), [ownerUserId, domainId]),
    identity.ready,
  );
  const runs = useResource(
    useCallback(() => evolutionApi.runs(ownerUserId, domainId, (runPage - 1) * 20), [ownerUserId, domainId, runPage]),
    identity.ready,
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editDomain, setEditDomain] = useState(false);
  const [domainName, setDomainName] = useState('');
  const [description, setDescription] = useState('');
  const [editTemplate, setEditTemplate] = useState<Template>();
  const [markdown, setMarkdown] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [result, setResult] = useState<unknown>();
  const mutation = async (operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const data = await operation();
      templates.reload();
      domain.reload();
      setSelected([]);
      return data;
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const startTemplate = async (name: string) => {
    setError('');
    setBusy(true);
    try {
      const template = await evolutionApi.template(ownerUserId, domainId, name);
      const version = template.versions.find((v) => v.version === template.latestVersion);
      if (version?.contentMd === undefined) throw new Error('当前模板内容不可用，请刷新后重试');
      setMarkdown(version.contentMd);
      setDisplayName(template.displayName || template.templateName);
      setEditTemplate(template);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载模板失败');
    } finally {
      setBusy(false);
    }
  };
  const upload = async (files: File[]) => {
    if (!files.length) return;
    try {
      setResult(await mutation(() => evolutionApi.upload(ownerUserId, domainId, files)));
    } catch {
      /* mutation already displays error */
    }
  };
  const batchPublish = async () => {
    const items =
      templates.data
        ?.filter((t) => selected.includes(t.templateName))
        .map((t) => ({ templateName: t.templateName, version: t.latestVersion })) ?? [];
    try {
      const data = (await mutation(() => evolutionApi.publish(ownerUserId, domainId, items))) as Awaited<
        ReturnType<typeof evolutionApi.publish>
      >;
      if (data) {
        setResult(data);
        const failed = data.items.filter((item) => !item.success);
        if (failed.length) setError(failed.map((v) => `${v.templateName}: ${v.reason}`).join('；'));
      }
    } catch {
      /* mutation already displays error */
    }
  };
  return (
    <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
      <div className="w-full min-w-0 space-y-5">
        <EvolutionHeader tab="templates" />
        <header className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="返回评测模板"
            onClick={() => history.push('/evolution-factory?tab=templates')}
          >
            <ArrowLeft size={18} />
          </Button>
          <h2 className="min-w-0 break-words text-xl font-semibold">{domain.data?.name ?? domainId}</h2>
          {writable && domain.data && (
            <Button
              variant="ghost"
              size="icon"
              aria-label="编辑 Domain"
              onClick={() => {
                setDomainName(domain.data!.name);
                setDescription(domain.data!.description || '');
                setEditDomain(true);
              }}
            >
              <Pencil size={14} />
            </Button>
          )}
          <div className="ml-auto flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                domain.reload();
                templates.reload();
                runs.reload();
              }}
            >
              刷新
            </Button>
            {writable && domain.data && (
              <>
                <label
                  className={`inline-flex cursor-pointer items-center whitespace-nowrap rounded-lg border border-input px-3 py-1 text-xs ${
                    busy ? 'pointer-events-none opacity-50' : ''
                  }`}
                >
                  上传模板
                  <input
                    className="sr-only"
                    type="file"
                    multiple
                    disabled={busy}
                    accept=".md,.zip"
                    onChange={(e) => {
                      const files = Array.from(e.target.files ?? []);
                      e.target.value = '';
                      void upload(files);
                    }}
                  />
                </label>
                <ConfirmDialog
                  title="归档 Domain？"
                  description="按现有服务归档该 Domain，后续评测请选择有效 Domain。"
                  onConfirm={async () => {
                    await mutation(() => evolutionApi.archiveDomain(ownerUserId, domainId));
                    history.push('/evolution-factory?tab=templates');
                  }}
                >
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-destructive/40 text-destructive"
                    disabled={busy}
                  >
                    归档 Bench Domain
                  </Button>
                </ConfirmDialog>
              </>
            )}
          </div>
        </header>
        <ErrorBox message={identity.error ?? domain.error} />
        <ErrorBox message={error} />
        {domain.loading && <Spin tip="加载 Domain…" />}
        {domain.data && (
          <div className="-mt-3 space-y-1 pl-12 text-xs text-muted-foreground">
            <p className="break-words">
              {ownerUserId}/{domainId} · {domain.data.description || '暂无描述'}
            </p>
            <p>{domain.data.templateCount} 个模板</p>
          </div>
        )}
        <div role="tablist" aria-label="评测详情" className="flex gap-6 border-b border-border">
          {(
            [
              ['detail', '目标详情'],
              ['tasks', '关联任务'],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              role="tab"
              key={value}
              aria-selected={tab === value}
              className={`border-b-2 px-3 pb-3 text-sm ${
                tab === value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'
              }`}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === 'detail' && (
          <Section title="评测模板">
            <ErrorBox message={templates.error} retry={templates.reload} />
            {writable && (
              <div className="mb-4 flex items-center gap-3">
                <span className="text-xs text-muted-foreground">已选择 {selected.length} 个待发布模板</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!selected.length || busy}
                  onClick={() => void batchPublish()}
                >
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
                    <tr className="border-b border-border" key={t.templateName}>
                      <td className="p-3">
                        <Checkbox
                          aria-label={`选择模板 ${t.templateName}`}
                          disabled={
                            !writable || t.versions.find((v) => v.version === t.latestVersion)?.status !== 'draft'
                          }
                          checked={selected.includes(t.templateName)}
                          onCheckedChange={(checked) =>
                            setSelected((v) =>
                              checked === true ? [...v, t.templateName] : v.filter((n) => n !== t.templateName),
                            )
                          }
                        />
                      </td>
                      <td className="p-3">
                        <div className="truncate font-mono text-xs" title={t.templateName}>
                          {t.templateName}
                        </div>
                      </td>
                      <td className="p-3">
                        <div className="truncate font-medium" title={t.displayName || t.templateName}>
                          {t.displayName || t.templateName}
                        </div>
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
                                await mutation(() =>
                                  evolutionApi.archiveTemplate(ownerUserId, domainId, t.templateName),
                                );
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
        )}
        {tab === 'tasks' && (
          <DomainRuns
            data={runs.data}
            loading={runs.loading}
            error={runs.error}
            reload={runs.reload}
            page={runPage}
            onPage={setRunPage}
          />
        )}
        {result !== null && result !== undefined && (
          <Section title="操作结果">
            <JsonView value={result} />
            <p className="mt-2 text-xs text-muted-foreground">上传/保存不代表已发布，请检查模板状态和发布结果。</p>
          </Section>
        )}
        <Modal
          open={editDomain}
          onOpenChange={(v) => {
            if (!busy) setEditDomain(v);
          }}
        >
          <ModalContent className="max-w-[520px]" aria-describedby={undefined}>
            <ModalHeader>
              <ModalTitle className="text-base">编辑 Domain</ModalTitle>
            </ModalHeader>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                void mutation(() =>
                  evolutionApi.updateDomain(ownerUserId, domainId, { name: domainName.trim(), description }),
                )
                  .then(() => setEditDomain(false))
                  .catch(() => undefined);
              }}
            >
              <ErrorBox message={error} />
              <Field label="Domain ID">
                <Input readOnly value={domainId} />
              </Field>
              <Field label="名称 *">
                <Input required value={domainName} onChange={(e) => setDomainName(e.target.value)} />
              </Field>
              <Field label="描述">
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} />
              </Field>
              <div className="flex justify-end gap-2 border-t border-border pt-4">
                <Button type="button" variant="outline" disabled={busy} onClick={() => setEditDomain(false)}>
                  取消
                </Button>
                <Button type="submit" loading={busy}>
                  确定
                </Button>
              </div>
            </form>
          </ModalContent>
        </Modal>
        <Modal
          open={Boolean(editTemplate)}
          onOpenChange={(v) => {
            if (!busy && !v) setEditTemplate(undefined);
          }}
        >
          <ModalContent size="xl" aria-describedby={undefined}>
            <ModalHeader>
              <ModalTitle className="text-base">{editTemplate?.templateName}</ModalTitle>
            </ModalHeader>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (!editTemplate || !writable) return;
                void mutation(() =>
                  evolutionApi.saveTemplate(ownerUserId, domainId, editTemplate.templateName, {
                    contentMd: markdown,
                    displayName,
                  }),
                )
                  .then(() => setEditTemplate(undefined))
                  .catch(() => undefined);
              }}
            >
              <ErrorBox message={error} />
              <Field label="显示名称">
                <Input readOnly={!writable} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
              </Field>
              <Field label="模板 Markdown">
                <Textarea
                  required
                  readOnly={!writable}
                  rows={16}
                  className="font-mono text-xs"
                  value={markdown}
                  onChange={(e) => setMarkdown(e.target.value)}
                />
              </Field>
              {writable && (
                <Button type="submit" loading={busy}>
                  保存草稿
                </Button>
              )}
            </form>
          </ModalContent>
        </Modal>
      </div>
    </main>
  );
}
