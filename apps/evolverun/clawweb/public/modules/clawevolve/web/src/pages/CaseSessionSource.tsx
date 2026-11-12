import { useState } from 'react';

function SourceValue({ label, value }: { label: string; value?: string }) {
  const [feedback, setFeedback] = useState('');
  async function copy() {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setFeedback('已复制');
    } catch {
      setFeedback('复制失败，请手动复制');
    }
  }
  return <div className="flex min-w-0 flex-wrap items-start gap-2">
    <span>{label}</span>
    <span className="min-w-0 break-all font-mono">{value || '未记录'}</span>
    {value && <button type="button" onClick={copy} aria-label={`复制${label}`} className="text-blue-600 hover:underline">复制</button>}
    <span role="status">{feedback}</span>
  </div>;
}

export function CaseSessionSource({ sourceSessionId, sourceSessionKey }: { sourceSessionId?: string; sourceSessionKey?: string }) {
  return <div className="mt-2 space-y-1 text-xs">
    <SourceValue label="来源 Session ID" value={sourceSessionId} />
    <SourceValue label="来源 Session Key" value={sourceSessionKey} />
  </div>;
}

