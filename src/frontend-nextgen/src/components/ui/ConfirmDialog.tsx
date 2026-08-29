import React from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from './AlertDialog';

export interface ConfirmDialogProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactElement;
  open?: boolean;
  loading?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel?: () => void;
  confirmText?: string;
  cancelText?: string;
  confirmVariant?: 'primary' | 'destructive';
  disabled?: boolean;
}

/** ConfirmDialog：封装同步/异步确认，同时支持触发器模式与外部受控模式。 */
export function ConfirmDialog({
  title,
  description,
  children,
  open: controlledOpen,
  loading: controlledLoading,
  onConfirm,
  onCancel,
  confirmText = '确定',
  cancelText = '取消',
  confirmVariant = 'primary',
  disabled = false,
}: ConfirmDialogProps) {
  const [internalLoading, setInternalLoading] = React.useState(false);
  const [internalOpen, setInternalOpen] = React.useState(false);
  const controlled = controlledOpen !== undefined;
  const open = controlled ? controlledOpen : internalOpen;
  const loading = controlledLoading ?? internalLoading;

  const handleOpenChange = (next: boolean) => {
    if (loading) return;
    if (controlled) {
      if (!next) onCancel?.();
      return;
    }
    setInternalOpen(next);
    if (!next) onCancel?.();
  };

  const handleConfirm = async () => {
    if (disabled || loading) return;
    if (controlled) {
      await onConfirm();
      return;
    }
    setInternalLoading(true);
    try {
      await onConfirm();
      setInternalOpen(false);
    } catch {
      // 保持打开，错误提示由调用方负责展示。
    } finally {
      setInternalLoading(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      {children ? (
        <AlertDialogTrigger asChild disabled={disabled}>
          {children}
        </AlertDialogTrigger>
      ) : null}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description ? <AlertDialogDescription>{description}</AlertDialogDescription> : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{cancelText}</AlertDialogCancel>
          <AlertDialogAction
            variant={confirmVariant}
            onClick={(event) => {
              event.preventDefault();
              void handleConfirm();
            }}
            disabled={loading}
          >
            {loading ? '处理中…' : confirmText}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
