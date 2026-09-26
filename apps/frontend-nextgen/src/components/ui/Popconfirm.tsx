import React from 'react';
import { Button } from './Button';
import { Popover, PopoverContent, PopoverTrigger } from './Popover';

export interface PopconfirmProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactElement;
  onConfirm: () => void | Promise<void>;
  onCancel?: () => void;
  confirmText?: string;
  cancelText?: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  disabled?: boolean;
}

/** Popconfirm：轻量确认，不改写 Trigger 原有 onClick。 */
export function Popconfirm({
  title,
  description,
  children,
  onConfirm,
  onCancel,
  confirmText = '确定',
  cancelText = '取消',
  side = 'top',
  disabled = false,
}: PopconfirmProps) {
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const confirm = async () => {
    if (loading || disabled) return;
    setLoading(true);
    try {
      await onConfirm();
      setOpen(false);
    } catch {
      /* reject 保持打开 */
    } finally {
      setLoading(false);
    }
  };
  return (
    <Popover open={open} onOpenChange={(next) => !loading && setOpen(next)}>
      <PopoverTrigger asChild disabled={disabled}>
        {children}
      </PopoverTrigger>
      <PopoverContent side={side} onClick={(event) => event.stopPropagation()}>
        <div className="space-y-2">
          <p className="m-0 text-sm font-medium">{title}</p>
          {description ? <p className="m-0 text-xs text-muted-foreground">{description}</p> : null}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setOpen(false);
                onCancel?.();
              }}
              disabled={loading}
            >
              {cancelText}
            </Button>
            <Button size="sm" onClick={() => void confirm()} disabled={loading}>
              {loading ? '处理中…' : confirmText}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
