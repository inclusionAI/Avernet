import {
  findFindingForCheckItem,
  normalizeResult,
  resultStyle,
} from '@/components/BotWorkshop/BotHealthCheckDrawer/utils';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotHealthCheckItem, BotHealthDimension, BotHealthFindingDetail } from '@/domain/botHealthCheck';
import { ChevronDown, Maximize2 } from 'lucide-react';
import { Fragment, useMemo, useState } from 'react';
import { BadCaseDrawer } from './BadCaseDrawer';
import { StatusIcon } from './StatusIcon';

interface ExpandableCheckTableProps {
  dimension: BotHealthDimension;
}

function scoreColorClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'text-[var(--color-muted)]';
  if (score >= 80) return 'text-[var(--color-success)]';
  if (score >= 60) return 'text-[var(--color-warning)]';
  return 'text-[var(--color-error)]';
}

function FindingDetails({
  details,
  onExpand,
}: {
  details: BotHealthFindingDetail[];
  onExpand: (detail: BotHealthFindingDetail) => void;
}) {
  return (
    <div className="space-y-3">
      {details.map((detail) => {
        const style = resultStyle(detail.result);
        return (
          <div
            key={detail.rule_id}
            className="grid gap-2 rounded-lg border border-[var(--color-border)] p-3 md:grid-cols-[1fr_auto_auto]"
          >
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-xs text-[var(--color-muted)]">子检测项</div>
                  <div className="mt-0.5 text-sm font-medium text-[var(--color-fg)]">{detail.name}</div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => onExpand(detail)}
                  className="size-7 shrink-0 text-[var(--color-muted)] hover:text-[var(--color-primary)]"
                  aria-label="展开完整内容"
                >
                  <Maximize2 className="size-3.5" />
                </Button>
              </div>
              <div className="mt-1 line-clamp-3 text-xs text-[var(--color-muted)]">{detail.message}</div>
            </div>
            <div className="text-right md:text-left">
              <div className="text-xs text-[var(--color-muted)]">检测状态</div>
              <div
                className={`mt-0.5 inline-flex items-center gap-1 text-sm font-medium ${
                  style.tone === 'success'
                    ? 'text-[var(--color-success)]'
                    : style.tone === 'warning'
                    ? 'text-[var(--color-warning)]'
                    : style.tone === 'error'
                    ? 'text-[var(--color-error)]'
                    : 'text-[var(--color-muted)]'
                }`}
              >
                <StatusIcon status={detail.result} />
                {style.label}
              </div>
            </div>
            <div className="text-right md:text-left">
              <div className="text-xs text-[var(--color-muted)]">分数</div>
              <div className={`mt-0.5 text-sm font-medium tabular-nums ${scoreColorClass(detail.score)}`}>
                {detail.score ?? '-'}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ExpandableCheckTable({ dimension }: ExpandableCheckTableProps) {
  const items = dimension.checkItems ?? [];
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [badCaseItem, setBadCaseItem] = useState<BotHealthCheckItem | null>(null);
  const [detailModal, setDetailModal] = useState<BotHealthFindingDetail | null>(null);

  const rowKeys = useMemo(() => items.map((item, index) => `${item.name}-${index}`), [items]);
  const expandedCount = rowKeys.filter((key) => expandedKeys.has(key)).length;
  const rowSpan = items.length + expandedCount;

  const toggle = (key: string) => {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (items.length === 0) {
    return (
      <Card className="rounded-xl">
        <CardContent className="px-5 py-8 text-sm text-[var(--color-muted)]">暂无检测项数据</CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card className="rounded-xl">
        <CardContent className="p-0">
          <div className="overflow-hidden rounded-xl">
            <table className="w-full table-fixed border-collapse">
              <colgroup>
                <col className="w-[16%]" />
                <col className="w-[24%]" />
                <col className="w-[12%]" />
                <col className="w-[10%]" />
                <col className="w-[20%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-[var(--color-border)] text-xs font-medium text-[var(--color-muted)]">
                  <th className="px-5 py-4 text-left">维度</th>
                  <th className="px-5 py-4 text-left">检测项目</th>
                  <th className="px-5 py-4 text-left">检测状态</th>
                  <th className="px-5 py-4 text-left">检测分数</th>
                  <th className="px-5 py-4 text-left">检测结论</th>
                  <th className="px-5 py-4 text-left">Bad Case</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, index) => {
                  const key = rowKeys[index];
                  const expanded = expandedKeys.has(key);
                  const result = normalizeResult(item.result ?? item.status);
                  const style = resultStyle(result);
                  const finding = findFindingForCheckItem(dimension.findings, item.checkItem ?? item.name);
                  const expandable = (finding?.finding_details.length ?? 0) > 0;
                  const hasBadCase =
                    (Array.isArray(item.evidence?.low_score_session_ids) &&
                      item.evidence.low_score_session_ids.length > 0) ||
                    !!item.badCase;

                  return (
                    <Fragment key={key}>
                      <tr className="border-b border-[var(--color-border)] align-top text-sm last:border-b-0">
                        {index === 0 ? (
                          <td
                            rowSpan={rowSpan}
                            className="px-5 py-4 align-middle text-sm font-medium text-[var(--color-fg)]"
                          >
                            <div>{dimension.label}</div>
                            {dimension.description ? (
                              <div className="mt-1 text-xs text-[var(--color-muted)]">{dimension.description}</div>
                            ) : null}
                            {dimension.score !== null && dimension.score !== undefined ? (
                              <div className="mt-2 text-lg font-semibold text-[var(--color-primary)]">
                                {dimension.score}分
                              </div>
                            ) : null}
                          </td>
                        ) : null}
                        <td className="px-5 py-4 break-words text-[var(--color-fg)]">
                          {expandable ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="justify-start px-0 text-left hover:text-[var(--color-primary)]"
                              onClick={() => toggle(key)}
                              leftIcon={
                                <ChevronDown
                                  className={`size-4 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
                                  aria-hidden
                                />
                              }
                            >
                              {item.name}
                            </Button>
                          ) : (
                            <span className="font-medium">{item.name}</span>
                          )}
                          {item.note ? <div className="mt-1 text-xs text-[var(--color-muted)]">{item.note}</div> : null}
                        </td>
                        <td className="px-5 py-4 text-[var(--color-muted)]">
                          <span
                            className={`inline-flex items-center gap-1 ${
                              style.tone === 'success'
                                ? 'text-[var(--color-success)]'
                                : style.tone === 'warning'
                                ? 'text-[var(--color-warning)]'
                                : style.tone === 'error'
                                ? 'text-[var(--color-error)]'
                                : 'text-[var(--color-muted)]'
                            }`}
                          >
                            <StatusIcon status={item.status} />
                            {style.label}
                          </span>
                        </td>
                        <td className={`px-5 py-4 font-semibold tabular-nums ${scoreColorClass(item.score)}`}>
                          {item.score ?? '-'}
                        </td>
                        <td className="px-5 py-4 break-words text-[var(--color-muted)]">{item.conclusion ?? '-'}</td>
                        <td className="px-5 py-4 break-words text-[var(--color-muted)]">
                          {hasBadCase ? (
                            <Button variant="ghost" size="sm" onClick={() => setBadCaseItem(item)}>
                              查看
                            </Button>
                          ) : (
                            '-'
                          )}
                        </td>
                      </tr>
                      {expanded && finding ? (
                        <tr className="bg-[var(--color-panel-muted)]">
                          <td colSpan={5} className="px-5 py-4">
                            <FindingDetails details={finding.finding_details} onExpand={setDetailModal} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <BadCaseDrawer item={badCaseItem} onOpenChange={(open) => !open && setBadCaseItem(null)} />

      <Modal open={Boolean(detailModal)} onOpenChange={(open) => !open && setDetailModal(null)}>
        <ModalContent size="lg" showClose>
          <ModalHeader>
            <ModalTitle className="text-lg font-semibold text-[var(--color-fg)]">{detailModal?.name}</ModalTitle>
            <ModalDescription className="text-sm text-[var(--color-muted)]">检测详情</ModalDescription>
          </ModalHeader>
          <div className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--color-panel-muted)] p-4 text-sm text-[var(--color-fg)]">
            {detailModal?.message}
          </div>
        </ModalContent>
      </Modal>
    </>
  );
}
