import { useSearchParams } from '@umijs/max';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { formatRunTimeOrDash } from '@/components/agentswarm/formatRunTime';
import { Badge } from '@/components/ui/Badge';
import { Pagination } from '@/components/ui/Pagination';
import { Card } from '@/components/ui/Card';
import { ManifestEditor } from './ManifestEditor';
import { ModelSelector, normalizeModel } from './ModelSelector';
import { useBotModelCatalog } from './useBotModelCatalog';
import { getCapabilities } from '@/capabilities';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { MANIFEST_DOCS_URL, summarizeIssues, validateManifest } from '@/domain/agentswarm/manifest';
import {
  readManifestInstructions,
  synthesizeManifest,
  syncManifestInstructions,
} from '@/domain/agentswarm/synthManifest';
import { useDefinitions } from '@/hooks/useDefinitions';
import {
  createDefinition,
  deleteDefinition,
  updateDefinition,
} from '@/services/agentswarm/service';
import type { DefinitionDTO } from '@/services/agentswarm/types';

interface DefinitionForm {
  name: string;
  role: string;
  instructions: string;
  /** The agent's own model. Empty means inherit the Bot's — the same
   *  ``null`` the API reads as "no override", so a blank field and an
   *  unset model are one state rather than two. */
  model: string | null;
  /** Capability YAML. A first-class field, not a leftover: it decides the
   *  agent's persona and tools, and a definition without it loses them. */
  manifestDocument: string;
  /** The document the form opened with: the blank skeleton, not a saved one. */
  manifestPristine: string;
}

/**
 * The YAML a new definition opens with: the document shape, with an empty
 * persona.
 *
 * The template used to carry a canned persona paragraph. That read as content
 * the user had written, so a definition created without touching the YAML
 * shipped an agent whose SOUL.md was boilerplate rather than its own 指令. An
 * empty `content` says the same thing the platform's own synthesizer would:
 * there is a persona document, and nothing in it yet.
 */
const BLANK_MANIFEST = [
  'schema_version: 1',
  'manifest:',
  '  identity:',
  '    - type: SOUL.md',
  '      content: ""',
  '',
].join('\n');

const EMPTY_FORM: DefinitionForm = {
  name: '',
  role: '',
  instructions: '',
  model: null,
  manifestDocument: BLANK_MANIFEST,
  manifestPristine: BLANK_MANIFEST,
};

/**
 * The library creates bot agents only. `llm` exists for the API and its tests —
 * this surface has no entry point for it, so the kind is pinned here rather
 * than offered as a choice the UI cannot meaningfully present.
 */
const LIBRARY_AGENT_KIND = 'bot' as const;

/**
 * The stored definition library: create, revise and retire reusable Agents.
 *
 * A revision writes a new version rather than overwriting, so editing sends the
 * `definition_row_id` that was read; the gateway refuses a stale one with 409
 * instead of silently clobbering a concurrent edit.
 */
export function DefinitionLibrary({ tenant }: { tenant: string }) {
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  // Nine to a page: the grid is three columns, so 9 fills it exactly instead
  // of leaving a ragged last row.
  const [pageSize, setPageSize] = useState(9);
  const { definitions, total, loading, error, refresh } = useDefinitions(tenant, {
    keyword, page, pageSize,
  });

  const [editing, setEditing] = useState<DefinitionDTO | 'new' | null>(null);
  const [form, setForm] = useState<DefinitionForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DefinitionDTO | null>(null);

  // A definition's model must be one the engine can route to, so the choices
  // come from a Bot's catalogue rather than a bundled list. The catalogue is the
  // same for every Bot on a deployment, so any of the caller's Bots will answer
  // it — the library reads the first one it finds rather than asking the user to
  // pick a Bot it has nowhere to use.
  const resolved = getCapabilities().getHumanIdentity();
  const userId = resolved.status === 'available' ? resolved.value?.userId ?? null : null;
  const { chatBots, isLoading: botsLoading } = useOwnedBots(userId, Boolean(userId));
  const catalogBot = useMemo(
    () => chatBots.find((bot) => (bot.status ?? 'ACTIVE') === 'ACTIVE') ?? chatBots[0] ?? null,
    [chatBots],
  );
  const catalog = useBotModelCatalog(catalogBot, userId);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setEditing('new');
  };

  const openEdit = (definition: DefinitionDTO) => {
    // A definition with no stored manifest still has a document: the platform
    // synthesizes one from 指令. Showing an empty editor made the card's
    // 「合成 SOUL.md」 badge disagree with the form, and looked like the persona
    // had been lost.
    const stored = definition.manifest_document ?? '';
    setForm({
      name: definition.name,
      role: definition.role,
      instructions: definition.instructions,
      model: definition.model ?? null,
      manifestDocument: stored.trim() ? stored : synthesizeManifest(definition.instructions),
      // Pristine = what the editor opened with, so an untouched synthesized
      // document is not submitted as if the user had written it.
      manifestPristine: stored.trim() ? '' : synthesizeManifest(definition.instructions),
    });
    setFormError(null);
    setEditing(definition);
  };

  // A run detail can link straight to the definition it was submitted from.
  // Opening it here rather than on a page of its own keeps one editor: a
  // definition is revised by whatever shows it, and two entry points would drift.
  //
  // The link carries the name as well as the id. The list is paged nine at a
  // time, so the definition being asked for is usually on another page; without
  // narrowing first the page would have to find it by scanning, and a definition
  // past page one could never be reached.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedDefinition = searchParams.get('definition') ?? '';
  const requestedName = searchParams.get('name') ?? '';
  const filterApplied = useRef(false);
  useEffect(() => {
    if (!requestedDefinition || filterApplied.current) return;
    filterApplied.current = true;
    if (requestedName) setKeyword(requestedName);
    setPage(1);
    // The filter stays: the user arrived looking for this one, and an empty
    // search box over a list they did not choose would be confusing.
    const next = new URLSearchParams(searchParams);
    next.delete('definition');
    next.delete('name');
    setSearchParams(next, { replace: true });
  }, [requestedDefinition, requestedName, searchParams, setSearchParams]);

  // Land on the definition rather than opening its editor: the reader came from
  // a run to see what that submission was made of, and an editor is a place to
  // change it. The matching card is called out so it is not just one of nine.
  const highlighted = requestedDefinition;
  const highlightedRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (!highlighted || loading) return;
    // Optional: jsdom and older browsers do not implement it, and a missing
    // scroll must not take the page down.
    highlightedRef.current?.scrollIntoView?.({ block: 'center' });
  }, [highlighted, loading, definitions]);

  const close = () => {
    setEditing(null);
    setFormError(null);
  };

  /**
   * The YAML to submit.
   *
   * Always a real document: a definition without one has to synthesize it on
   * every read just to show what its persona is. When the user has not touched
   * the editor, the current 指令 still decides the persona, so the document is
   * regenerated from it — the same text the platform would have produced.
   */
  const submittedManifest = (current: DefinitionForm): string => {
    const text = current.manifestDocument.trim();
    const pristine = text === current.manifestPristine.trim();
    if (!pristine) return text;
    return synthesizeManifest(current.instructions);
  };

  const save = async () => {
    if (!form.name.trim()) {
      setFormError('请填写定义名称');
      return;
    }
    // Check the YAML before leaving the form. The gateway validates the same
    // document and answers 422, but that reply lands after the user has lost
    // their place in a long edit; the same rule applies here while the cursor
    // is still where they left it.
    const manifestText = submittedManifest(form);
    if (manifestText) {
      const validation = validateManifest(manifestText);
      if (!validation.ok) {
        setFormError(`能力配置 YAML 有误：${summarizeIssues(validation.issues)}`);
        return;
      }
    }
    setBusy(true);
    setFormError(null);
    try {
      if (editing === 'new') {
        await createDefinition(tenant, {
          name: form.name.trim(),
          role: form.role,
          instructions: form.instructions,
          kind: LIBRARY_AGENT_KIND,
          model: normalizeModel(form.model),
          manifest_document: submittedManifest(form),
        });
        notifySuccess('定义已保存到个人库');
      } else if (editing) {
        await updateDefinition(tenant, {
          definition_id: editing.definition_id,
          definition_row_id: editing.definition_row_id,
          name: form.name.trim(),
          role: form.role,
          instructions: form.instructions,
          kind: LIBRARY_AGENT_KIND,
          model: normalizeModel(form.model),
          manifest_document: submittedManifest(form),
        });
        notifySuccess('定义已更新');
      }
      close();
      refresh();
    } catch (reason) {
      // A 409 means someone else revised it while this form was open; saying so
      // is more useful than a generic failure.
      const message = reason instanceof Error ? reason.message : '保存失败';
      // Two refusals share 409 and both used to read as "someone changed this
      // under you", which sent people to refresh when the real problem was a
      // name they already use.
      // Match on the reason rather than a code: the gateway names a taken name
      // in prose ("a live definition named 'x' already exists"), and older
      // deployments send only that, so a code-only check would miss it and fall
      // through to the concurrent-edit advice — the wrong fix for the user.
      if (/definition_name_conflict|already exists|已被占用|已被使用/i.test(message)) {
        setFormError(`名称「${form.name.trim()}」已被占用，请换一个名称。`);
      } else if (/definition_version_conflict|superseded/i.test(message)) {
        setFormError('该定义已被修改，请刷新后再试。');
      } else if (/409|conflict|冲突/i.test(message)) {
        // A 409 with no reason we recognise: the version moved on is the likelier
        // cause, and re-reading a definition is harmless.
        setFormError('该定义已被修改，请刷新后再试。');
      } else if (/manifest|identity/i.test(message)) {
        // The gateway's own validator is authoritative and names the offending
        // path; quoting it is more useful than a generic failure.
        setFormError(`能力配置 YAML 被拒绝：${message}`);
      } else {
        setFormError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deleteDefinition(tenant, pendingDelete.definition_id);
      notifySuccess('定义已删除');
    } catch (reason) {
      notifyError(reason instanceof Error ? reason.message : '删除失败');
    } finally {
      setPendingDelete(null);
      refresh();
    }
  };

  return (
    <section className="flex flex-col gap-3" aria-label="我的 Agent 库">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold">我的 Agent 库 · {total}</h3>
        <Button type="button" size="sm" variant="outline" onClick={openCreate}>
          <Plus className="mr-1 size-3.5" aria-hidden />
          新建 Agent 定义
        </Button>
      </div>

      <Input
        aria-label="搜索定义"
        className="max-w-sm"
        placeholder="按名称搜索定义"
        value={keyword}
        onChange={(event) => {
          setKeyword(event.target.value);
          // A new filter is a new result set; staying on page 4 of the old one
          // would show an empty page.
          setPage(1);
        }}
      />

      {error ? (
        <p role="alert" className="m-0 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {!loading && !definitions.length ? (
        <Empty
          title="还没有保存的 Agent 定义"
          description="新建 Agent 定义后，可以在配置任务时直接引用。"
        />
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
          {definitions.map((definition) => (
            <li
              key={definition.definition_id}
              ref={
                definition.definition_id === highlighted ? highlightedRef : undefined
              }
            >
              <Card
                className={[
                  'flex h-full flex-col',
                  definition.definition_id === highlighted
                    ? 'ring-2 ring-primary ring-offset-2 ring-offset-background'
                    : '',
                ].join(' ')}
              >
                <div className="flex items-start gap-2 px-4 pb-0 pt-3.5">
                  {/* A span, not CardTitle: that one is an <h3>, whose UA heading
                      weight and tracking sat oddly against the 12px field text. */}
                  <span
                    className="min-w-0 flex-1 truncate text-sm font-medium leading-5 text-foreground"
                    title={definition.name}
                  >
                    {definition.name}
                  </span>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`编辑定义 ${definition.name}`}
                      onClick={() => openEdit(definition)}
                    >
                      <Pencil className="size-3.5" aria-hidden />
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`删除定义 ${definition.name}`}
                      onClick={() => setPendingDelete(definition)}
                    >
                      <Trash2 className="size-3.5 text-destructive" aria-hidden />
                    </Button>
                  </div>
                </div>
                <dl className="m-0 flex flex-col gap-1 px-4 py-3">
                  <div className="flex items-baseline gap-2">
                    <dt className="w-8 shrink-0 text-[11px] font-semibold text-foreground">职责</dt>
                    <dd
                      className="m-0 min-w-0 flex-1 truncate text-xs leading-5 text-foreground"
                      title={definition.role.trim() || undefined}
                    >
                      {definition.role.trim() || '未填写'}
                    </dd>
                  </div>
                  <div className="flex items-baseline gap-2">
                    <dt className="w-8 shrink-0 text-[11px] font-semibold text-foreground">指令</dt>
                    <dd
                      className="m-0 min-w-0 flex-1 truncate text-xs leading-5 text-muted-foreground"
                      title={definition.instructions.trim() || undefined}
                    >
                      {definition.instructions.trim() ||
                        '未填写指令：该 Agent 的人设来自能力配置 YAML。'}
                    </dd>
                  </div>
                </dl>
                <div className="flex flex-wrap items-center gap-1.5 border-t border-divider px-4 py-2.5">
                  <Badge tone={definition.model ? 'primary' : 'outline'}>
                    {definition.model || '跟随默认 Bot'}
                  </Badge>
                  <Badge tone="neutral">{definition.kind === 'bot' ? 'Bot' : definition.kind}</Badge>
                  {definition.manifest_document.trim() ? (
                    <Badge tone="purple">自定义 YAML</Badge>
                  ) : (
                    <Badge tone="outline">合成 SOUL.md</Badge>
                  )}
                  <span className="ml-auto text-[11px] text-muted-foreground">
                    {formatRunTimeOrDash(definition.created_at)}
                  </span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {!loading && total > 0 ? (
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          onChange={setPage}
          onPageSizeChange={(next) => {
            setPageSize(next);
            setPage(1);
          }}
          // Multiples of three: the grid is three columns, so any other size
          // leaves a ragged last row.
          pageSizeOptions={[9, 18, 36]}
          layout="between"
        />
      ) : null}

      <Modal open={editing !== null} onOpenChange={(open) => (open ? undefined : close())}>
        <ModalContent size="xl">
          <ModalHeader>
            <ModalTitle>{editing === 'new' ? '新建 Agent 定义' : '编辑 Agent 定义'}</ModalTitle>
          </ModalHeader>
          <div className="flex flex-col gap-4">
            <div className="space-y-2">
              <Label htmlFor="definition-name">
                名称 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="definition-name"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="definition-role">职责</Label>
              <Textarea
                id="definition-role"
                rows={2}
                value={form.role}
                onChange={(event) => setForm({ ...form, role: event.target.value })}
              />
            </div>
            <ModelSelector
              label="独立模型"
              inheritLabel="跟随默认 Bot"
              hint={
                botsLoading || catalog.loading
                  ? '正在读取执行 Bot 的模型目录…'
                  : catalog.error
                    ? `读取执行 Bot 的模型目录失败（${catalog.error}），可直接输入模型名称。`
                    : catalogBot
                      ? `可选模型来自执行 Bot「${catalogBot.displayName}」；选择「跟随默认 Bot」时该 Agent 使用所绑定 Bot 的模型。`
                      : '未找到可用的执行 Bot，可直接输入模型名称。'
              }
              customLabel="自定义模型"
              customPlaceholder="输入执行 Bot 可用的模型标识"
              models={catalog.models}
              value={form.model}
              onChange={(model) => setForm({ ...form, model })}
            />
            <div className="space-y-2">
              <Label htmlFor="definition-instructions">指令</Label>
              <Textarea
                id="definition-instructions"
                rows={4}
                value={form.instructions}
                onChange={(event) => {
                  // 指令 and the YAML's SOUL.md content are the same text, so the
                  // manifest follows this edit — carrying mcp/skills untouched.
                  const instructions = event.target.value;
                  setForm({
                    ...form,
                    instructions,
                    manifestDocument: syncManifestInstructions(form.manifestDocument, instructions),
                  });
                }}
              />
              <p className="m-0 text-xs leading-5 text-muted-foreground">
                具体行为要求，等同 YAML 中 SOUL.md 的 content；两处改任一处都会同步。
              </p>
            </div>
            {/* YAML is a first-class part of a definition: it decides the persona
                and tools. It is editable as a form or raw YAML — one document,
                two views — so editing a definition never silently drops it. */}
            <ManifestEditor
              value={form.manifestDocument}
              onChange={(manifestDocument) => {
                // The reverse direction: editing the YAML's SOUL.md writes the
                // same text back to 指令, so the two never disagree.
                const mirrored = readManifestInstructions(manifestDocument);
                setForm({
                  ...form,
                  manifestDocument,
                  instructions: mirrored === null ? form.instructions : mirrored,
                });
              }}
            />
            <p className="m-0 text-xs leading-5 text-muted-foreground">
              identity / mcp / skills / resources / engine_config / cli_tools 的完整语法见
              <a href={MANIFEST_DOCS_URL} target="_blank" rel="noreferrer" className="ml-1 text-primary hover:underline">
                配置清单规范
              </a>
              。留空则由「指令」生成 SOUL.md。
            </p>
            {/* A save refusal has to be readable at a glance: it sits under a
                tall form, and the user is looking for what went wrong. */}
            {formError ? (
              <p
                role="alert"
                className="m-0 rounded-md bg-destructive/5 px-3 py-2 text-sm font-semibold text-destructive"
              >
                {formError}
              </p>
            ) : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={close}>
              取消
            </Button>
            <Button type="button" disabled={busy} onClick={() => void save()}>
              {busy ? '保存中…' : '保存'}
            </Button>
          </div>
        </ModalContent>
      </Modal>

      <ConfirmDialog
        title="删除定义"
        description={
          pendingDelete ? `删除「${pendingDelete.name}」？历史记录仍保留，但不能再被引用。` : undefined
        }
        open={pendingDelete !== null}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
        confirmText="删除"
        confirmVariant="destructive"
      />
    </section>
  );
}