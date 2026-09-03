import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { ExternalLink, KeyRound } from 'lucide-react';
import React, { useState } from 'react';
import { useCodefuseToken } from './useCodefuseToken';

interface CodefuseAuthModalProps {
  open: boolean;
  botId?: string;
  onClose: () => void;
  onSuccess?: () => void | Promise<void>;
}

export const CodefuseAuthModal: React.FC<CodefuseAuthModalProps> = ({ open, botId, onClose, onSuccess }) => {
  const codefuseTokenUrl = getCapabilities().getAgentCodingInternalResources().value.codefuseTokenUrl;
  const [token, setToken] = useState('');
  const { isSaving, saveToken } = useCodefuseToken(botId);
  const handleSave = async () => {
    const result = await saveToken(token);
    if (!result.ok) return;
    setToken('');
    await onSuccess?.();
    onClose();
  };
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="sm">
        <ModalHeader>
          <ModalTitle className="flex items-center gap-2">
            <KeyRound size={18} />
            CodeFuse 授权
          </ModalTitle>
        </ModalHeader>
        <div className="space-y-5">
          <p className="text-sm text-muted-foreground leading-relaxed">填写授权 Token 后即可使用 CodeFuse 模型能力</p>
          {codefuseTokenUrl ? (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">第一步 · 获取 Token</label>
              <a
                href={codefuseTokenUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
              >
                前往 CodeFuse 复制 Token <ExternalLink size={14} />
              </a>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <label className="text-xs font-medium">
              第二步 · 填写 Token<span className="ml-0.5 text-destructive">*</span>
            </label>
            <Textarea
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="粘贴从 CodeFuse 复制的 Token"
              disabled={isSaving}
              rows={4}
              className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm font-mono outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={isSaving}>
              取消
            </Button>
            <Button onClick={handleSave} loading={isSaving} disabled={isSaving || !token.trim()}>
              保存
            </Button>
          </div>
        </div>
      </ModalContent>
    </Modal>
  );
};
export default CodefuseAuthModal;
