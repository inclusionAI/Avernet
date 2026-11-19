import type { BotHealthPatchFixRequest } from '@/hooks/useBotHealthCheck';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Spin } from '@/components/ui/Spin';
import {
  Modal,
  ModalClose,
  ModalContent,
  ModalDescription,
  ModalFooter,
  ModalHeader,
  ModalTitle,
} from '@/components/ui/Modal';
import { FileDiff } from 'lucide-react';

interface PatchPreviewModalProps {
  request: BotHealthPatchFixRequest | null;
  loading?: boolean;
  applying?: boolean;
  onConfirm: () => void | Promise<unknown>;
  onOpenChange: (open: boolean) => void;
}

const RISK_META: Record<string, { label: string; tone: 'success' | 'warning' | 'error' }> = {
  low: { label: '低风险', tone: 'success' },
  medium: { label: '中风险', tone: 'warning' },
  high: { label: '高风险', tone: 'error' },
};

/**
 * 一键修复确认弹窗：展示补丁预览 diff，确认后应用。
 * 对齐老前端 Harness 的 PatchPreviewModal 行为（preview → 确认 → apply）。
 */
export function PatchPreviewModal({ request, loading, applying, onConfirm, onOpenChange }: PatchPreviewModalProps) {
  const patch = request?.patch;
  const preview = request?.preview;
  const risk = preview ? RISK_META[preview.risk_level] ?? RISK_META.low : undefined;

  return (
    <Modal open={Boolean(patch)} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby="patch-preview-description">
        <ModalHeader>
          <ModalTitle className="flex items-center gap-2 text-base font-semibold text-[var(--color-fg)]">
            <FileDiff className="size-4 text-[var(--color-primary)]" aria-hidden />
            确认修复：{patch?.name}
          </ModalTitle>
          <ModalDescription id="patch-preview-description" className="text-xs text-muted-foreground">
            修复将更新 Bot 的 AGENTS.md 配置，请确认以下变更内容。
          </ModalDescription>
        </ModalHeader>

        <div className="max-h-[60vh] space-y-3 overflow-y-auto px-6 pb-2">
          {patch?.description ? (
            <p className="text-sm text-[var(--color-muted)]">{patch.description}</p>
          ) : null}

          {loading ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Spin tip="正在生成修复预览..." />
            </div>
          ) : preview ? (
            <>
              {risk ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-[var(--color-muted)]">变更风险：</span>
                  <Badge tone={risk.tone}>{risk.label}</Badge>
                </div>
              ) : null}
              {preview.operations.length > 0 ? (
                <div className="space-y-3">
                  {preview.operations.map((operation, index) => (
                    <div key={`${operation.op}-${operation.target}-${index}`} className="rounded-xl border border-border/60">
                      <div className="border-b border-divider px-3 py-2 text-xs font-semibold text-[var(--color-fg)]">
                        {operation.op_summary || `${operation.op} · ${operation.target}`}
                      </div>
                      {operation.diff ? (
                        <pre className="max-h-64 overflow-auto rounded-b-xl bg-[var(--color-panel-muted)] p-3 text-xs leading-relaxed">
                          {operation.diff}
                        </pre>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </div>

        <ModalFooter>
          <ModalClose asChild>
            <Button variant="ghost" disabled={applying}>
              取消
            </Button>
          </ModalClose>
          <Button disabled={loading || applying || !preview} loading={applying} onClick={() => void onConfirm()}>
            应用修复
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
