import { Button } from '@/components/ui/Button';
import { Empty as EmptyState } from '@/components/ui/Empty';
import { Modal as Dialog, ModalContent, ModalDescription, ModalTitle } from '@/components/ui/Modal';
import { Segmented } from '@/components/ui/Segmented';
import { Spin } from '@/components/ui/Spin';
import type { ReactNode } from 'react';

export { EmptyState };
export function LoadingState({ label = '正在读取编辑器数据' }: { label?: string }) {
  return (
    <div role="status">
      <Spin tip={label} />
    </div>
  );
}
export function StateView({ loading, error, retry }: { loading: boolean; error: string | null; retry: () => void }) {
  if (loading) return <LoadingState />;
  if (error)
    return (
      <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5">
        <EmptyState
          title="读取失败"
          description={error}
          action={
            <Button variant="outline" onClick={retry}>
              重试读取
            </Button>
          }
        />
      </div>
    );
  return null;
}
export function Tabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <Segmented
      value={value}
      options={options}
      onChange={onChange}
      aria-label={label}
      className="max-w-full flex-wrap"
    />
  );
}
export function EditorDialog({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ModalContent size="xl">
        <ModalTitle>{title}</ModalTitle>
        <ModalDescription>来自当前工作流的真实配置，仅供查看；未接入保存和发布。</ModalDescription>
        {children}
      </ModalContent>
    </Dialog>
  );
}
export function EditorCode({ label, text }: { label: string; text: string }) {
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className="max-h-[560px] min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-muted/30 p-4 font-mono text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {text}
    </pre>
  );
}
export function EditorFields({ value }: { value: Record<string, unknown> }) {
  return (
    <dl className="space-y-3">
      {Object.entries(value).map(([key, item]) => (
        <div key={key} className="min-w-0">
          <dt className="break-all font-mono text-xs text-muted-foreground">{key}</dt>
          <dd className="m-0 mt-1 whitespace-pre-wrap break-words text-xs">
            {typeof item === 'string' ? item : JSON.stringify(item, null, 2)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
