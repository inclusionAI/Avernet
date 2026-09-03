import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { BackendRequestError, setCallerCodefuseAuth } from '@/services/botWorkshop/agentCodingLegacyService';
import { ExternalLink, KeyRound } from 'lucide-react';
import React, { useState } from 'react';
import { toast } from 'sonner';

export interface CallerCfuseAuthModalProps {
  open: boolean;
  botId: string;
  onClose: () => void;
  onSuccess?: () => void | Promise<void>;
}
export const CallerCfuseAuthModal: React.FC<CallerCfuseAuthModalProps> = ({ open, botId, onClose, onSuccess }) => {
  const codefuseTokenUrl = getCapabilities().getAgentCodingInternalResources().value.codefuseTokenUrl;
  const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);
  const handleSave = async () => {
    if (!token.trim()) {
      toast.error('请先填写 CodeFuse 授权码');
      return;
    }
    setSaving(true);
    try {
      const response = await setCallerCodefuseAuth(botId, token.trim());
      if (response.success === false) {
        toast.error(response.message || '授权失败');
        return;
      }
      setToken('');
      toast.success('授权成功');
      await onSuccess?.();
      onClose();
    } catch (error) {
      const message = error instanceof BackendRequestError ? error.message : '授权失败';
      toast.error(message);
      console.error('[CallerCfuseAuthModal] authorization failed', error);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="sm">
        <ModalHeader>
          <ModalTitle className="flex items-center gap-2">
            <KeyRound size={18} />
            授权我的 CodeFuse 额度
          </ModalTitle>
        </ModalHeader>
        <div className="space-y-5">
          <p className="text-sm text-muted-foreground leading-relaxed">
            该应用 Bot 需要使用你的 CodeFuse 额度。请复制授权 Token 并粘贴到下方。
          </p>
          {codefuseTokenUrl ? (
            <a
              href={codefuseTokenUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
            >
              前往 CodeFuse 复制 Token <ExternalLink size={14} />
            </a>
          ) : null}
          <Textarea
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴从 CodeFuse 复制的 Token"
            disabled={saving}
            rows={4}
            className="w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm font-mono outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={saving}>
              取消
            </Button>
            <Button onClick={handleSave} loading={saving} disabled={saving || !token.trim()}>
              授权
            </Button>
          </div>
        </div>
      </ModalContent>
    </Modal>
  );
};
export default CallerCfuseAuthModal;
