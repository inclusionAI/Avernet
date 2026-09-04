import { HelpCircle, Lightbulb } from 'lucide-react';
import { asItems } from './shared';
import type { EditableField, TaskCardData } from './types';

/**
 * 任务澄清卡片 —— 按原始 card SDK 视觉规格自绘（只读，不可编辑）。
 * 仅把 Font Awesome 图标换成本项目自带 lucide（Open Core 不依赖 FA），其余结构/颜色/间距与原卡片一致。
 */
export function TaskClarifyCard({ data }: { data: TaskCardData }) {
  const {
    goal,
    deliverables,
    acceptance_criteria,
    constraints,
    resources,
    missing_fields,
    needs_confirmation,
    questions,
  } = data;
  const missingSet = new Set(missing_fields ?? []);
  const confirmSet = new Set(needs_confirmation ?? []);

  const fields: Array<[string, string, EditableField, string[]]> = [
    ['交付物', '📌', 'deliverables', asItems(deliverables)],
    ['验收标准', '✅', 'acceptance_criteria', asItems(acceptance_criteria)],
    ['约束', '⚠️', 'constraints', asItems(constraints)],
  ];

  const renderField = (label: string, icon: string, fieldName: EditableField, items: string[]) => {
    const isMissing = missingSet.has(fieldName);
    const needsConfirm = confirmSet.has(fieldName);
    const hasItems = items.length > 0;
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-[11px] leading-none">{icon}</span>
          <span className="text-[11px] font-medium text-gray-700">{label}</span>
          {needsConfirm && !isMissing ? (
            <span className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded-full bg-amber-50 text-amber-600 text-[9px] whitespace-nowrap leading-none">
              <Lightbulb className="h-2 w-2" aria-hidden /> 推断
            </span>
          ) : null}
        </div>
        {isMissing ? (
          <div className="ml-4 p-1.5 border border-dashed border-gray-200 rounded-lg bg-muted/50">
            <span className="text-[9px] text-gray-400">待补充</span>
          </div>
        ) : hasItems ? (
          <div className="ml-4 space-y-0.5">
            {items.map((item, idx) => (
              <div
                key={idx}
                className={
                  'flex items-start gap-1 text-[11px] ' + (needsConfirm ? 'bg-amber-50/60 rounded px-1 py-0.5' : '')
                }
              >
                <span className="text-gray-400 mt-0.5">•</span>
                <span className="text-gray-600 leading-relaxed">{item}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="ml-4 text-[9px] text-gray-400">无</div>
        )}
      </div>
    );
  };

  return (
    <div className="p-1 w-full flex justify-start task-loop-card-root">
      <div className="w-full max-w-[360px] bg-white border border-gray-100 shadow-md rounded-2xl overflow-hidden transition-all duration-500">
        {/* Header */}
        <div className="px-3.5 pt-3.5 pb-2 border-b border-gray-50">
          <div className="flex items-center gap-1">
            <span className="text-xs leading-none">📋</span>
            <span className="text-sm font-semibold tracking-tight text-gray-900">任务澄清</span>
          </div>
        </div>

        {/* Body */}
        <div className="px-3.5 py-2.5 space-y-2.5">
          {/* Goal */}
          <div className="space-y-1">
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[11px] font-medium text-gray-700">目标</span>
              {confirmSet.has('goal') ? (
                <span className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded-full bg-amber-50 text-amber-600 text-[9px] whitespace-nowrap leading-none">
                  <Lightbulb className="h-2 w-2" aria-hidden /> 推断
                </span>
              ) : null}
            </div>
            <p className="text-xs font-semibold text-gray-900 leading-relaxed">{goal || '未设定'}</p>
          </div>

          {/* Four elements */}
          {fields.map(([label, icon, fieldName, items]) => (
            <div key={fieldName}>{renderField(label, icon, fieldName, items)}</div>
          ))}
          {resources && resources.length > 0 ? (
            <div key="resources">{renderField('关联资源', '🔗', 'resources', asItems(resources))}</div>
          ) : null}
        </div>

        {/* Questions - Plain display, no click */}
        {questions && questions.length > 0 ? (
          <div className="px-3.5 pb-2.5">
            <div className="bg-blue-50/50 rounded-xl p-2.5 space-y-1.5">
              <div className="flex items-center gap-1">
                <HelpCircle className="text-blue-500 h-3 w-3" aria-hidden />
                <span className="text-[11px] font-medium text-blue-700">需要你确认以下问题</span>
              </div>
              {questions.map((q, idx) => (
                <div key={idx} className="bg-white rounded-lg p-2 border border-blue-100">
                  <div className="flex items-start gap-1.5">
                    <div className="flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-semibold bg-blue-100 text-blue-500 leading-none">
                      {idx + 1}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] text-gray-600 leading-relaxed whitespace-pre-wrap break-words">{q}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Hint */}
        <div className="px-3.5 pb-2.5">
          <p className="text-[9px] text-gray-400 flex items-center gap-1 leading-none">
            <Lightbulb className="h-2 w-2" aria-hidden />
            {questions && questions.length > 0 ? '请在对话中回答以上问题' : '直接在对话中补充缺失信息即可'}
          </p>
        </div>
      </div>
    </div>
  );
}
