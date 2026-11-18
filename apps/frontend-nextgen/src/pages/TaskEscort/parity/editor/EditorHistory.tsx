import { Button } from '@/components/ui/Button';
import { editorSnapshotText, editorTextDiff } from '@/domain/taskEscortParity/editor';
import type { EditorDiff } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  useTaskEscortEditorDiff,
  useTaskEscortEditorHistory,
  useTaskEscortEditorSnapshot,
} from '@/hooks/taskEscortParity/useTaskEscortEditor';
import { useState } from 'react';
import { EditorCode, EditorFields, EmptyState, StateView } from './EditorPrimitives';

function VersionDiff({ value }: { value: EditorDiff }) {
  const from = editorSnapshotText(value.from.specJson),
    to = editorSnapshotText(value.to.specJson);
  const changes = editorTextDiff(from, to);
  return (
    <section aria-label="版本差异" className="space-y-4">
      <h3 className="text-sm font-semibold">
        发布 #{value.from.deployNumber} → #{value.to.deployNumber}
      </h3>
      <p className="text-xs text-muted-foreground">文本差异：仅移除公共前后文，不等价于语义补丁，不执行应用。</p>
      {changes.length === 0 ? (
        <EmptyState compact title="两个发布快照内容一致" />
      ) : (
        <pre
          tabIndex={0}
          className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-muted/30 p-4 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {changes.map((line, index) => (
            <span key={index} className={`block ${line.kind === 'removed' ? 'text-destructive' : 'text-success'}`}>
              {line.kind === 'removed' ? '− ' : '+ '}
              {line.text}
            </span>
          ))}
        </pre>
      )}
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <EditorCode label={`原发布 #${value.from.deployNumber}`} text={from} />
        <EditorCode label={`目标发布 #${value.to.deployNumber}`} text={to} />
      </div>
    </section>
  );
}
export function EditorHistory({ context }: { context: TaskEscortParityContext }) {
  const history = useTaskEscortEditorHistory(context, true);
  const [deploy, setDeploy] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [comparison, setComparison] = useState<readonly number[] | null>(null);
  const snapshot = useTaskEscortEditorSnapshot(context, deploy);
  const diff = useTaskEscortEditorDiff(context, comparison);
  const choose = (number: number) => {
    setComparison(null);
    setSelected((current) =>
      current.includes(number) ? current.filter((item) => item !== number) : [...current.slice(-1), number],
    );
  };
  if (history.loading || history.error) return <StateView {...history} retry={history.refresh} />;
  if (!history.data) return null;
  const data = history.data;
  return (
    <section aria-label="工作流发布历史" className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">发布历史</h3>
          <p className="text-xs text-muted-foreground">最近最多 20 条发布记录；不含编辑记录，不代表全部历史。</p>
          <p className="text-xs">
            {data.active ? `生效发布 #${data.active.deployNumber}` : '服务端未返回生效发布，未将最新记录当作生效版本。'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={history.refresh}>
            刷新发布历史
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={selected.length !== 2}
            onClick={() => {
              setDeploy(null);
              setComparison([...selected]);
            }}
          >
            对比所选发布
          </Button>
          <Button variant="outline" size="sm" disabled>
            激活版本
          </Button>
        </div>
      </header>
      {data.active && !data.history.some((item) => item.deployNumber === data.active?.deployNumber) && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setComparison(null);
            setDeploy(data.active!.deployNumber);
          }}
        >
          查看生效快照 #{data.active.deployNumber}
        </Button>
      )}
      {data.history.length === 0 ? (
        <EmptyState title="没有发布记录" description="此工作流尚未返回发布历史，未生成版本或快照。" />
      ) : (
        <ul className="m-0 list-none space-y-2 p-0">
          {data.history.map((item) => (
            <li key={item.deployNumber} className="space-y-3 rounded-lg border border-border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <span className="text-xs font-semibold">
                    发布 #{item.deployNumber} · v{item.version} · {item.action}
                  </span>
                  {data.active?.deployNumber === item.deployNumber && (
                    <span className="ml-2 text-xs text-success">生效中</span>
                  )}
                  {typeof item.gmtCreate === 'number' && (
                    <p className="text-xs text-muted-foreground">{new Date(item.gmtCreate * 1000).toLocaleString()}</p>
                  )}
                  {typeof item.note === 'string' && <p className="break-words text-xs">{item.note}</p>}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`查看快照 #${item.deployNumber}`}
                    onClick={() => {
                      setComparison(null);
                      setDeploy(item.deployNumber);
                    }}
                  >
                    查看快照
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`选择对比 #${item.deployNumber}`}
                    aria-pressed={selected.includes(item.deployNumber)}
                    onClick={() => choose(item.deployNumber)}
                  >
                    {selected.includes(item.deployNumber)
                      ? `已选 ${selected.indexOf(item.deployNumber) + 1}`
                      : '选择对比'}
                  </Button>
                </div>
              </div>
              {item.source !== null && item.source !== undefined && (
                <details>
                  <summary className="cursor-pointer text-xs text-muted-foreground">版本来源（原始字段）</summary>
                  <EditorCode label={`发布来源 #${item.deployNumber}`} text={JSON.stringify(item.source, null, 2)} />
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">激活和回滚写入未接入；当前仅支持查看快照与对比。</p>
      {deploy !== null && (
        <section aria-label="所选发布快照" className="space-y-3">
          <h3 className="text-sm font-semibold">发布快照 #{deploy}</h3>
          <StateView {...snapshot} retry={snapshot.refresh} />
          {snapshot.data && (
            <>
              <EditorFields
                value={{
                  deployNumber: snapshot.data.deployNumber,
                  version: snapshot.data.version,
                  action: snapshot.data.action,
                }}
              />
              <EditorCode label={`发布快照 #${deploy}`} text={editorSnapshotText(snapshot.data.specJson)} />
            </>
          )}
        </section>
      )}
      {comparison && (
        <>
          <StateView {...diff} retry={diff.refresh} />
          {diff.data && <VersionDiff value={diff.data} />}
        </>
      )}
    </section>
  );
}
