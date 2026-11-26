import { useState } from 'react';
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
import { ManifestEditor } from './ManifestEditor';
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
  /** Capability YAML. A first-class field, not a leftover: it decides the
   *  agent's persona and tools, and a definition without it loses them. */
  manifestDocument: string;
}

const EMPTY_FORM: DefinitionForm = { name: '', role: '', instructions: '', manifestDocument: '' };

/**
 * The stored definition library: create, revise and retire reusable Agents.
 *
 * A revision writes a new version rather than overwriting, so editing sends the
 * `definition_row_id` that was read; the gateway refuses a stale one with 409
 * instead of silently clobbering a concurrent edit.
 */
export function DefinitionLibrary({ tenant }: { tenant: string }) {
  const [keyword, setKeyword] = useState('');
  const { definitions, total, loading, error, refresh } = useDefinitions(tenant, keyword);

  const [editing, setEditing] = useState<DefinitionDTO | 'new' | null>(null);
  const [form, setForm] = useState<DefinitionForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DefinitionDTO | null>(null);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setEditing('new');
  };

  const openEdit = (definition: DefinitionDTO) => {
    setForm({
      name: definition.name,
      role: definition.role,
      instructions: definition.instructions,
      manifestDocument: definition.manifest_document,
    });
    setFormError(null);
    setEditing(definition);
  };

  const close = () => {
    setEditing(null);
    setFormError(null);
  };

  const save = async () => {
    if (!form.name.trim()) {
      setFormError('请填写定义名称');
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      if (editing === 'new') {
        await createDefinition(tenant, {
          name: form.name.trim(),
          role: form.role,
          instructions: form.instructions,
          manifest_document: form.manifestDocument,
        });
        notifySuccess('定义已保存到个人库');
      } else if (editing) {
        await updateDefinition(tenant, {
          definition_id: editing.definition_id,
          definition_row_id: editing.definition_row_id,
          name: form.name.trim(),
          role: form.role,
          instructions: form.instructions,
          manifest_document: form.manifestDocument,
        });
        notifySuccess('定义已更新');
      }
      close();
      refresh();
    } catch (reason) {
      // A 409 means someone else revised it while this form was open; saying so
      // is more useful than a generic failure.
      const message = reason instanceof Error ? reason.message : '保存失败';
      setFormError(/409|conflict|冲突/i.test(message) ? '该定义已被修改，请刷新后再试。' : message);
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
    <section className="flex flex-col gap-3" aria-label="我的定义库">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold">我的定义库 · {total}</h3>
        <Button type="button" size="sm" variant="outline" onClick={openCreate}>
          <Plus className="mr-1 size-3.5" aria-hidden />
          新建定义
        </Button>
      </div>

      <Input
        aria-label="搜索定义"
        className="max-w-sm"
        placeholder="按名称搜索定义"
        value={keyword}
        onChange={(event) => setKeyword(event.target.value)}
      />

      {error ? (
        <p role="alert" className="m-0 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {!loading && !definitions.length ? (
        <Empty title="还没有保存的定义" description="新建定义后，可以在配置任务时直接引用。" />
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {definitions.map((definition) => (
            <li
              key={definition.definition_id}
              className="flex flex-wrap items-center gap-2 rounded-lg border border-divider px-3 py-2"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{definition.name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {definition.role || '（无职责说明）'}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                {formatRunTimeOrDash(definition.created_at)}
              </span>
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
            </li>
          ))}
        </ul>
      )}

      <Modal open={editing !== null} onOpenChange={(open) => (open ? undefined : close())}>
        <ModalContent size="lg">
          <ModalHeader>
            <ModalTitle>{editing === 'new' ? '新建定义' : '编辑定义'}</ModalTitle>
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
            <div className="space-y-2">
              <Label htmlFor="definition-instructions">指令</Label>
              <Textarea
                id="definition-instructions"
                rows={4}
                value={form.instructions}
                onChange={(event) => setForm({ ...form, instructions: event.target.value })}
              />
              <p className="m-0 text-xs leading-5 text-muted-foreground">
                未填写 YAML 时，这段文字会成为该 Agent 的 SOUL.md 内容。
              </p>
            </div>
            {/* YAML is a first-class part of a definition: it decides the persona
                and tools. It is editable as a form or raw YAML — one document,
                two views — so editing a definition never silently drops it. */}
            <ManifestEditor
              value={form.manifestDocument}
              onChange={(manifestDocument) => setForm({ ...form, manifestDocument })}
            />
            {formError ? (
              <p role="alert" className="m-0 text-xs text-destructive">
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