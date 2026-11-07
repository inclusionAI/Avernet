import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Spin } from '@/components/ui/Spin';
import type { Template } from '@/domain/evolution/types';
import { history, useParams } from '@umijs/max';
import { ArrowLeft, Pencil } from 'lucide-react';
import { useCallback, useState } from 'react';
import { DomainEditors } from './DomainEditors';
import { DomainRuns } from './DomainRuns';
import { DomainTemplates } from './DomainTemplates';
import { ErrorBox, EvolutionHeader, JsonView, Section, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

function DomainDetailContent() {
  const { ownerUserId = '', domainId = '' } = useParams<{ ownerUserId: string; domainId: string }>();
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
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
        <EvolutionHeader tab="templates" showTabs={false} />
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
        <div role="tablist" aria-label="评测详情" className="flex gap-6 border-b border-divider">
          {(
            [
              ['detail', '目标详情'],
              ['tasks', '关联任务'],
            ] as const
          ).map(([value, label]) => (
            <Button
              variant="ghost"
              style={{ height: 'auto', whiteSpace: 'normal' }}
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
            </Button>
          ))}
        </div>
        {tab === 'detail' && (
          <DomainTemplates
            templates={templates}
            writable={writable}
            selected={selected}
            setSelected={setSelected}
            busy={busy}
            batchPublish={batchPublish}
            startTemplate={startTemplate}
            archiveTemplate={(name) => mutation(() => evolutionApi.archiveTemplate(ownerUserId, domainId, name))}
          />
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
        <DomainEditors
          editDomain={editDomain}
          setEditDomain={setEditDomain}
          editTemplate={editTemplate}
          setEditTemplate={setEditTemplate}
          domainId={domainId}
          domainName={domainName}
          setDomainName={setDomainName}
          description={description}
          setDescription={setDescription}
          displayName={displayName}
          setDisplayName={setDisplayName}
          markdown={markdown}
          setMarkdown={setMarkdown}
          busy={busy}
          error={error}
          writable={writable}
          saveDomain={() =>
            mutation(() => evolutionApi.updateDomain(ownerUserId, domainId, { name: domainName.trim(), description }))
              .then(() => setEditDomain(false))
              .catch(() => undefined)
          }
          saveTemplate={async () => {
            if (!editTemplate || !writable) return;
            await mutation(() =>
              evolutionApi.saveTemplate(ownerUserId, domainId, editTemplate.templateName, {
                contentMd: markdown,
                displayName,
              }),
            )
              .then(() => setEditTemplate(undefined))
              .catch(() => undefined);
          }}
        />
      </div>
    </main>
  );
}

export default function DomainDetail() {
  const context = useEvolutionContext();
  return <DomainDetailContent key={context.key} />;
}
