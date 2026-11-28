/** Read-only immutable revision comparison; never render Bot/model content as HTML. */
import { Button } from '@/components/ui/Button';
import { genomeErrorMessage } from '@/services/rsi/errors';
import { getGenomeDiff, type GenomeDiff } from '@/services/rsi/service';
import type { RsiScope } from '@/types/rsi';
import { useEffect, useState } from 'react';

export function GenomeDiffView({ diff }: { diff: GenomeDiff }) {
  return (
    <div className="space-y-2 text-xs">
      <p className="break-all text-muted-foreground">{diff.against} → {diff.revision}</p>
      <p>风险：{diff.risk_tier} · rewrite：{diff.rewrite_flags.join('、') || '无'}</p>
      {diff.changes.length ? diff.changes.map((change) => (
        <details key={change.gene} className="rounded border border-input p-2" open>
          <summary className="cursor-pointer break-all font-mono">{change.kind} · {change.gene}</summary>
          {change.diff !== null ? (
            <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">{change.diff}</pre>
          ) : <p className="text-muted-foreground">未提供文本 Diff（可能是二进制、结构变更或超过大小限制），不表示没有变更。</p>}
        </details>
      )) : <p>这两个版本没有基因差异。</p>}
    </div>
  );
}

function RevisionDiffBody({ scope, revision, against }: { scope: RsiScope; revision: string; against: string }) {
  const [diff, setDiff] = useState<GenomeDiff | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void getGenomeDiff(scope, revision, against).then((value) => {
      if (!active) return;
      if (value.revision !== revision || value.against !== against) throw new Error('diff identity mismatch');
      setDiff(value);
    }).catch((cause: unknown) => {
      if (active) setError(genomeErrorMessage(cause, '真实 Diff 读取失败；不能据此判断候选没有修改。'));
    });
    return () => { active = false; };
  }, [scope, revision, against, attempt]);
  if (error) return (
    <div className="space-y-1">
      <p role="alert" className="text-xs text-destructive">{error}</p>
      <Button size="sm" variant="outline" onClick={() => { setError(''); setAttempt((value) => value + 1); }}>重试读取 Diff</Button>
    </div>
  );
  return diff ? <GenomeDiffView diff={diff} /> : <p className="text-xs text-muted-foreground">正在读取真实 Diff…</p>;
}

export function RevisionDiff(props: { scope: RsiScope; revision: string; against: string }) {
  // Exact hashes, not movable refs; discard late reads on actor/Bot/revision changes.
  return <RevisionDiffBody key={JSON.stringify([props.scope, props.revision, props.against])} {...props} />;
}
