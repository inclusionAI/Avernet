// 引擎配置保存弹层：气泡内的 diff 预览，样式与旧版 EngineConfigDrawer/DiffPreview 保持一致。
import { Button } from '@/components/ui/Button';
import {
  countChangeBlocks,
  type DiffItem,
  type DiffLine as DiffLineType,
} from '@/domain/engineConfigDiff';
import { AlertTriangle, Save } from 'lucide-react';
import { useMemo } from 'react';

interface EngineConfigDiffPreviewProps {
  collapsedDiff: DiffItem[] | null;
  fullDiff: DiffLineType[] | null;
  expandedBlocks: Set<number>;
  jsonValidation: { valid: boolean; error: string | null };
  isSaving: boolean;
  onExpand: (blockIndex: number) => void;
  onSave: () => void;
  onCancel: () => void;
}

/** 单行 diff 渲染（含折叠块展开），复刻旧版 DiffLine 样式。 */
function DiffRow({
  item,
  index,
  isExpanded,
  fullDiff,
  onExpand,
}: {
  item: DiffItem;
  index: number;
  isExpanded: boolean;
  fullDiff: DiffLineType[] | null;
  onExpand: (blockIndex: number) => void;
}) {
  if (item.type === 'collapsed') {
    if (isExpanded && fullDiff) {
      const start = fullDiff.findIndex((d) => d.lineNumber === item.startLine);
      const end = fullDiff.findIndex((d) => d.lineNumber === item.endLine);
      return (
        <>
          {fullDiff
            .slice(start === -1 ? 0 : start, end === -1 ? fullDiff.length : end + 1)
            .map((change, subIdx) => (
              <div key={`expanded-${index}-${subIdx}`} className="flex items-start gap-2 -mx-1 overflow-x-auto px-1 py-0.5">
                <span className="w-4 flex-shrink-0 select-none text-right text-[10px] text-slate-400">{change.lineNumber}</span>
                <span className="w-3 flex-shrink-0 text-transparent">·</span>
                <code className="min-w-0 flex-1 whitespace-pre text-slate-800">{change.line}</code>
              </div>
            ))}
        </>
      );
    }
    return (
      <Button
        variant="ghost"
        className="h-auto w-full justify-start gap-2 rounded-none border-0 bg-transparent px-1 py-1.5 font-normal text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:ring-2 focus-visible:ring-blue-400/60 focus-visible:ring-offset-0"
        onClick={() => onExpand(index)}
      >
        <span className="w-4 flex-shrink-0 text-right text-[10px]">···</span>
        <span className="w-3 flex-shrink-0">⋯</span>
        <span className="flex-1 text-left text-slate-500 hover:text-slate-700">
          折叠了 {item.count} 行未修改内容 (行 {item.startLine}-{item.endLine})
          <span className="ml-2 text-slate-400 hover:text-slate-600">展开</span>
        </span>
      </Button>
    );
  }

  const change = item;
  return (
    <div
      className={`-mx-1 flex items-start gap-2 px-1 py-0.5 ${
        change.type === 'added' ? 'bg-green-50' : change.type === 'removed' ? 'bg-red-50' : ''
      }`}
    >
      <span className="w-4 flex-shrink-0 select-none text-right text-[10px] text-slate-400">
        {change.type !== 'removed' ? change.lineNumber : ''}
      </span>
      <span
        className={`w-3 flex-shrink-0 font-bold ${
          change.type === 'added' ? 'text-green-600' : change.type === 'removed' ? 'text-red-600' : 'text-transparent'
        }`}
      >
        {change.type === 'added' ? '+' : change.type === 'removed' ? '-' : ' '}
      </span>
      <code
        className={`flex-1 whitespace-pre ${
          change.type === 'removed' ? 'text-red-700 line-through' : 'text-slate-800'
        }`}
      >
        {change.line}
      </code>
    </div>
  );
}

export function EngineConfigDiffPreview({
  collapsedDiff,
  fullDiff,
  expandedBlocks,
  jsonValidation,
  isSaving,
  onExpand,
  onSave,
  onCancel,
}: EngineConfigDiffPreviewProps) {
  const changeCount = useMemo(() => (fullDiff ? countChangeBlocks(fullDiff) : 0), [fullDiff]);

  if (!collapsedDiff || collapsedDiff.length === 0) {
    return (
      <div className="px-4 py-3">
        <p className="text-xs text-slate-500">无配置修改</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col" style={{ height: 500 }}>
      {/* 标题栏（浅色硬编码，与旧版 DiffPreview 一致，不随主题翻转） */}
      <div className="flex-shrink-0 border-b border-slate-200 bg-slate-50 px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="mb-0 text-xs font-medium text-slate-700">
            配置修改预览
            <span className="ml-2 font-normal text-slate-500">({changeCount} 处修改)</span>
          </p>
          <div className="flex items-center gap-2 text-xs">
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded border border-red-300 bg-red-100" />
              <span className="text-slate-500">删除</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded border border-green-300 bg-green-100" />
              <span className="text-slate-500">新增</span>
            </span>
          </div>
        </div>
      </div>

      {/* JSON 格式错误提示 */}
      {!jsonValidation.valid && (
        <div className="mx-4 my-3 flex-shrink-0 rounded-lg border border-red-200 bg-red-50 px-3 py-2">
          <p className="mb-0 flex items-start gap-1.5 text-xs text-red-700">
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
            <span>
              <strong>JSON 格式错误：</strong>
              {jsonValidation.error}
            </span>
          </p>
        </div>
      )}

      {/* Diff 内容 */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-white px-4 py-3">
        <div className="rounded border border-slate-200 bg-slate-50 p-2 font-mono text-xs">
          {collapsedDiff.map((item, idx) => (
            <DiffRow
              key={`diff-${idx}`}
              item={item}
              index={idx}
              isExpanded={expandedBlocks.has(idx)}
              fullDiff={fullDiff}
              onExpand={onExpand}
            />
          ))}
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex flex-shrink-0 items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-4 py-3">
        <Button variant="outline" size="sm" onClick={onCancel}>
          取消
        </Button>
        <Button
          size="sm"
          leftIcon={<Save className="size-4" />}
          disabled={isSaving || !jsonValidation.valid}
          loading={isSaving}
          onClick={onSave}
        >
          确认保存
        </Button>
      </div>
    </div>
  );
}
