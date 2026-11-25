import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import type { Template } from '@/domain/evolution/types';
import type { Dispatch, SetStateAction } from 'react';
import { ErrorBox, Field } from './shared';

type DomainEditorsProps = {
  editDomain: boolean;
  setEditDomain: Dispatch<SetStateAction<boolean>>;
  editTemplate?: Template;
  setEditTemplate: Dispatch<SetStateAction<Template | undefined>>;
  domainId: string;
  domainName: string;
  setDomainName: Dispatch<SetStateAction<string>>;
  description: string;
  setDescription: Dispatch<SetStateAction<string>>;
  displayName: string;
  setDisplayName: Dispatch<SetStateAction<string>>;
  markdown: string;
  setMarkdown: Dispatch<SetStateAction<string>>;
  busy: boolean;
  error: string;
  writable: boolean;
  saveDomain: () => Promise<unknown>;
  saveTemplate: () => Promise<unknown>;
};
export function DomainEditors({
  editDomain,
  setEditDomain,
  editTemplate,
  setEditTemplate,
  domainId,
  domainName,
  setDomainName,
  description,
  setDescription,
  displayName,
  setDisplayName,
  markdown,
  setMarkdown,
  busy,
  error,
  writable,
  saveDomain,
  saveTemplate,
}: DomainEditorsProps) {
  return (
    <>
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
              void saveDomain();
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
            <div className="flex justify-end gap-2 border-t border-divider pt-4">
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
              void saveTemplate();
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
    </>
  );
}
