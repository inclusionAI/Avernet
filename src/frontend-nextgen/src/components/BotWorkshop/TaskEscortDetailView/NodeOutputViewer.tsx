import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, Copy } from 'lucide-react';
import { useCallback, useState } from 'react';

interface NodeOutputViewerProps {
  label: string;
  data: string | null;
  className?: string;
}

function prettyPrint(data: string): string {
  try {
    const parsed = JSON.parse(data);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return data;
  }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API not available
    }
  }, [text]);

  return (
    <Button variant="ghost" size="icon" className="h-5 w-5" onClick={handleCopy}>
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </Button>
  );
}

export function NodeOutputViewer({ label, data, className }: NodeOutputViewerProps) {
  const [expanded, setExpanded] = useState(false);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  if (!data) {
    return (
      <div className={cn('rounded-md bg-muted px-3 py-2 text-xs italic text-muted-foreground', className)}>
        暂无{label}数据
      </div>
    );
  }

  const formatted = prettyPrint(data);

  return (
    <div className={cn('rounded-md border border-border bg-background', className)}>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleToggle}
        className="flex h-auto w-full items-center justify-between rounded-b-none px-3 py-2 text-left text-xs font-medium hover:bg-muted"
      >
        <span>{label}</span>
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-180')} />
      </Button>

      {expanded && (
        <div className="border-t border-border">
          <div className="flex items-center justify-between bg-muted px-3 py-1">
            <span className="text-[10px] text-muted-foreground">
              {formatted.length > 1024 ? `${(formatted.length / 1024).toFixed(1)} KB` : `${formatted.length} 字符`}
            </span>
            <CopyButton text={formatted} />
          </div>
          <pre className="max-h-[360px] overflow-auto p-3 font-mono text-xs leading-relaxed text-foreground">
            {formatted}
          </pre>
        </div>
      )}
    </div>
  );
}
