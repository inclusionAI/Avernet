import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import { scopeIssueGroup } from '@/domain/taskEscortParity/evolution';
import type { VersionScope } from '@/domain/taskEscortParity/evolutionTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useRef, useState } from 'react';
import { IssueCauses } from './IssueCauses';
import { IssueEvidence } from './IssueEvidence';
import { ReadState, ReadTabs } from './ReadPrimitives';
import { RepairSuggestions } from './RepairSuggestions';
export function IssueDrawer({
  context,
  signature,
  includeHistorical,
  initialTab,
  versionScope = 'current',
  snapshotKey,
  onClose,
}: {
  context: TaskEscortParityContext;
  signature: string;
  includeHistorical: boolean;
  initialTab?: 'causes' | 'repairs' | 'evidence';
  versionScope?: VersionScope;
  snapshotKey?: string;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<'causes' | 'repairs' | 'evidence'>(
    initialTab ?? (context.query.analysisId || context.query.runId ? 'evidence' : 'causes'),
  );
  const previous = useRef(document.activeElement as HTMLElement | null);
  const read = useCallback(
    async (signal: AbortSignal) => {
      const group = await evolutionService.group(context.workflowId, context.userId!, signature, versionScope, signal);
      return snapshotKey === undefined ? group : scopeIssueGroup(group, snapshotKey);
    },
    [context.workflowId, context.userId, signature, versionScope, snapshotKey],
  );
  const result = useTaskEscortEvolutionRead(context, `issue-detail:${signature}:${versionScope}:${snapshotKey}`, read);
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent
        side="right"
        size="lg"
        className="w-full max-w-[640px]"
        bodyClassName="flex flex-col p-0"
        closeLabel="关闭问题详情"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (previous.current?.isConnected) previous.current.focus();
        }}
      >
        <header className="shrink-0 border-b border-border px-5 py-4 pr-14">
          <DrawerTitle className="font-semibold">问题详情</DrawerTitle>
          <DrawerDescription className="mt-2 break-all text-xs">
            {signature} · {result.data ? `累计关联 ${result.data.flowIds.length} 个运行` : '正在读取选定问题'}
          </DrawerDescription>
        </header>
        <div className="shrink-0 px-5">
          <ReadTabs
            label="问题详情分区"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'causes', label: '问题原因' },
              { value: 'repairs', label: '修复建议' },
              { value: 'evidence', label: '证据与历史' },
            ]}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <ReadState {...result} />
          {result.data &&
            (tab === 'causes' ? (
              <IssueCauses group={result.data} />
            ) : tab === 'repairs' ? (
              <RepairSuggestions
                context={context}
                signature={signature}
                includeHistorical={versionScope === 'historical' || includeHistorical}
                snapshotKey={snapshotKey}
                versionScope={versionScope}
              />
            ) : (
              <IssueEvidence
                key={`${signature}:${context.query.analysisId}:${context.query.runId}`}
                context={context}
                group={result.data}
                snapshotKey={snapshotKey}
              />
            ))}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
