import { useEffect, useId, useRef, useState } from 'react';
import type { DiagnosisPage } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

/** Show at most three nearby pages; dedicated first/last controls cover the edges. */
export function paginationTokens(current: number, total: number): number[] {
  const count = Math.min(3, Math.max(0, total));
  const start = Math.max(1, Math.min(current - 1, total - count + 1));
  return Array.from({ length: count }, (_, index) => start + index);
}

type Props = {
  data: DiagnosisPage | null;
  pageSize: number;
  loading: boolean;
  /** Changes only for explicit query/target changes, never for a background refresh. */
  resetKey: string;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
};

export function MonitoringPagination({ data, pageSize, loading, resetKey, onPageChange, onPageSizeChange }: Props) {
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const errorId = useId();
  const nav = useRef<HTMLElement>(null), jump = useRef<HTMLInputElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const pages = data?.totalPages ?? 0;
  const current = pages ? data!.page : 0;
  const unavailable = loading || !data;
  useEffect(() => { setDraft(''); setError(''); }, [resetKey]);
  useEffect(() => {
    const previous = restoreFocus.current;
    if (unavailable || !previous) return;
    restoreFocus.current = null;
    // Do not steal focus if the user moved elsewhere while the request was pending.
    if (document.activeElement !== document.body && document.activeElement !== previous) return;
    const destination = previous.isConnected && !(previous as HTMLButtonElement).disabled
      ? previous : nav.current?.querySelector<HTMLElement>('[aria-current="page"]');
    destination?.focus({ preventScroll: true });
  }, [unavailable, data]);
  const rememberFocus = () => {
    const active = document.activeElement;
    restoreFocus.current = active instanceof HTMLElement && nav.current?.contains(active) ? active : null;
  };
  const go = (page: number) => {
    if (unavailable || !pages || page === current) return;
    rememberFocus();
    onPageChange(Math.max(1, Math.min(pages, page)));
  };
  const submit = () => {
    if (unavailable || !pages) return;
    jump.current?.focus({ preventScroll: true });
    const page = Number(draft);
    if (!/^[0-9]+$/.test(draft) || !Number.isSafeInteger(page) || page < 1 || page > pages) {
      setError(`请输入 1–${pages} 之间的整数页码。`);
      return;
    }
    setError(''); setDraft(''); go(page);
  };
  const range = data?.items.length
    ? `第 ${(data.page - 1) * data.pageSize + 1}–${(data.page - 1) * data.pageSize + data.items.length} 条，共 ${data.total} 条`
    : data ? `共 ${data.total} 条` : '—';
  return <footer className="list-footer">
    <span aria-live="polite">{range}</span>
    <nav ref={nav} aria-label="诊断记录分页" className="pagination record-pagination" aria-busy={loading}>
      <select aria-label="每页条数" value={pageSize} disabled={unavailable} onChange={event => {
        const size = Number(event.target.value);
        if (size !== pageSize) { rememberFocus(); onPageSizeChange(size); }
      }}>{[10, 20, 50].map(size => <option key={size} value={size}>{size} 条 / 页</option>)}</select>
      <div className="page-buttons">
        <button type="button" className="page-btn" disabled={unavailable || current <= 1} onClick={() => go(1)}>首页</button>
        <button type="button" aria-label="上一页" className="page-btn" disabled={unavailable || current <= 1} onClick={() => go(current - 1)}><Icon name="arrow" /></button>
        {paginationTokens(current, pages).map(page =>
          <button type="button" key={page} aria-label={`第 ${page} 页`} aria-current={current === page ? 'page' : undefined}
            className={`page-btn page-number ${current === page ? 'active' : ''}`} disabled={unavailable} onClick={() => go(page)}>{page}</button>)}
        <button type="button" aria-label="下一页" className="page-btn" disabled={unavailable || !pages || current >= pages} onClick={() => go(current + 1)}><Icon name="arrow" className="next-arrow" /></button>
        <button type="button" className="page-btn" disabled={unavailable || !pages || current >= pages} onClick={() => go(pages)}>末页</button>
      </div>
      <span className="page-position" aria-live="polite">{data ? `${current} / ${pages} 页` : '— / — 页'}</span>
      <form className="page-jump" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label htmlFor={`${errorId}-input`}>前往</label>
        <input ref={jump} id={`${errorId}-input`} aria-label="跳转页码" type="text" inputMode="numeric" autoComplete="off"
          disabled={data?.totalPages === 0} value={draft} aria-invalid={Boolean(error)} aria-describedby={error ? errorId : undefined}
          onChange={event => { setDraft(event.target.value); setError(''); }}
          onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); setDraft(''); setError(''); } }} />
        <span>页</span><button type="submit" disabled={unavailable || !pages}>跳转</button>
      </form>
      {error && <span id={errorId} className="page-jump-error" role="alert">{error}</span>}
    </nav>
  </footer>;
}
