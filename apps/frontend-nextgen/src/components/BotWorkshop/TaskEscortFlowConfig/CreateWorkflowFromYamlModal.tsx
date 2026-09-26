import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import type { WorkflowImportErrorField } from '@/services/taskEscort';
import React, { useEffect, useState } from 'react';

interface CreateWorkflowFromYamlModalProps {
  open: boolean;
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: {
    yaml: string;
    command?: string;
    remark?: string;
  }) => Promise<{ ok: true } | { ok: false; field: WorkflowImportErrorField; message: string }>;
}

const CreateWorkflowFromYamlModal: React.FC<CreateWorkflowFromYamlModalProps> = ({
  open,
  loading,
  onOpenChange,
  onCreate,
}) => {
  const [yaml, setYaml] = useState('');
  const [command, setCommand] = useState('');
  const [remark, setRemark] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setYaml('');
      setCommand('');
      setRemark('');
      setYamlError(null);
      setCommandError(null);
    }
  }, [open]);

  const handleCreate = async () => {
    setYamlError(null);
    setCommandError(null);
    const result = await onCreate({
      yaml: yaml.trim(),
      ...(command.trim() ? { command: command.trim() } : {}),
      ...(remark.trim() ? { remark: remark.trim() } : {}),
    });
    if (result.ok) {
      onOpenChange(false);
      return;
    }
    if (result.field === 'command') setCommandError(result.message);
    else setYamlError(result.message);
  };

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !loading && onOpenChange(nextOpen)}>
      <ModalContent size="md">
        <ModalHeader>
          <ModalTitle>从 YAML 创建工作流</ModalTitle>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block text-xs font-medium">
            粘贴 YAML
            <Textarea
              className="mt-1 min-h-48 font-mono"
              value={yaml}
              variant={yamlError ? 'error' : 'default'}
              placeholder="在此粘贴 WorkflowSpec YAML…"
              onChange={(event) => setYaml(event.target.value)}
            />
            {yamlError && <span className="mt-1 block text-[11px] text-destructive">{yamlError}</span>}
          </label>
          <label className="block text-xs font-medium">
            命令（可选）
            <Input
              className="mt-1"
              value={command}
              placeholder="与 id 相同时留空"
              aria-invalid={Boolean(commandError)}
              onChange={(event) => setCommand(event.target.value)}
            />
            {commandError && <span className="mt-1 block text-[11px] text-destructive">{commandError}</span>}
          </label>
          <label className="block text-xs font-medium">
            备注（可选）
            <Input
              className="mt-1"
              value={remark}
              placeholder="命令的简要描述"
              onChange={(event) => setRemark(event.target.value)}
            />
          </label>
        </div>
        <ModalFooter>
          <Button variant="secondary" disabled={loading} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button loading={loading} onClick={() => void handleCreate()}>
            创建
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
};

export default CreateWorkflowFromYamlModal;
