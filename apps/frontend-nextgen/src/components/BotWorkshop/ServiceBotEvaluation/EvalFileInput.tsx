import type { ChangeEvent } from 'react';
import { useRef } from 'react';

interface EvalFileInputProps {
  label: string;
  accept?: string;
  file?: File;
  onSelect: (file?: File) => void;
}

export function EvalFileInput({ label, accept, file, onSelect }: EvalFileInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <label className="flex h-20 cursor-pointer flex-col justify-center rounded-lg border border-dashed border-border bg-muted/30 px-4 text-center transition hover:border-primary/50 hover:bg-primary/5">
      <span className="text-sm font-medium text-foreground">{label}</span>
      <span className="mt-1 truncate text-xs text-muted-foreground">{file?.name ?? '未选择文件'}</span>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(event: ChangeEvent<HTMLInputElement>) => onSelect(event.target.files?.[0])}
      />
    </label>
  );
}
