import { Button, Textarea } from '@/components/ui';
import { useTaskCardAction } from '@/hooks/useTaskCardAction';
import { cn } from '@/utils/cn';
import { Bookmark, ExternalLink, Lightbulb, Pencil, Play, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { asItems, normalizeReadyTask } from './shared';
import {
  EditFieldControls,
  taskCardDiscardBtnClass,
  taskCardEditBtnClass,
  taskCardExecuteBtnClass,
  taskCardSaveBtnClass,
  taskCardTextareaClass,
} from './TaskCardControls';
import type { EditableField, TaskCardData } from './types';

/**
 * 任务已就绪卡片 —— 按原始 card SDK 视觉规格自绘。
 * 守卫要求交互用 <Button>/<Textarea>；其余卡片壳、emoji 头、palette 色、圆点与原卡片一致。
 * fa-* 图标项目无依赖，替换为 lucide 等价线图标；动作经 useTaskCardAction 收敛到 ChatBridge。
 */
export function TaskReadyCard({ data }: { data: TaskCardData }) {
  const task = normalizeReadyTask(data);
  const confirmSet = new Set(data.needs_confirmation ?? []);
  const submitTaskCardAction = useTaskCardAction();

  const [editingField, setEditingField] = useState<EditableField | null>(null);
  const [editValue, setEditValue] = useState('');

  const startEdit = (fieldName: EditableField, currentValue: string | string[]) => {
    if (editingField !== null) return;
    setEditingField(fieldName);
    setEditValue(Array.isArray(currentValue) ? currentValue.join('\n') : currentValue ?? '');
  };
  const cancelEdit = () => {
    setEditingField(null);
    setEditValue('');
  };
  const confirmEdit = (fieldName: EditableField, label: string) => {
    const lines = editValue
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    const msg =
      lines.length <= 1
        ? `修改${label}为：${lines[0] ?? ''}`
        : `修改${label}为：\n${lines.map((line) => `- ${line}`).join('\n')}`;
    setEditingField(null);
    setEditValue('');
    submitTaskCardAction(msg);
  };
  const handleAction = (action: 'execute' | 'discard' | 'save') => {
    if (action === 'execute') {
      // 场景 A：执行时把 task 带上，宿主拦截层识别 __taskAction==='execute' → 调 execute + 开副屏。
      submitTaskCardAction('执行任务', { __taskAction: 'execute', task });
    } else {
      submitTaskCardAction(action === 'save' ? '暂存任务' : '丢弃任务');
    }
  };

  const renderEditableField = (label: string, icon: string, fieldName: EditableField, value: string[] | undefined) => {
    const needsConfirm = confirmSet.has(fieldName);
    const isEditing = editingField === fieldName;
    const isDisabled = editingField !== null && !isEditing;
    const items = asItems(value);
    const hasItems = items.length > 0;
    return (
      <div className="space-y-1" key={fieldName}>
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-[11px] leading-none">{icon}</span>
          <span className="text-[11px] font-medium text-gray-700">{label}</span>
          {needsConfirm ? (
            <span className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded-full bg-amber-50 text-amber-600 text-[9px] whitespace-nowrap leading-none">
              <Lightbulb className="h-2 w-2" aria-hidden /> 推断
            </span>
          ) : null}
          <div className="flex-1" />
          {!isEditing ? (
            <Button
              variant="ghost"
              disabled={isDisabled}
              className={taskCardEditBtnClass(isDisabled)}
              onClick={() => startEdit(fieldName, items)}
            >
              <Pencil className="h-2 w-2" aria-hidden />
              编辑
            </Button>
          ) : null}
        </div>
        {isEditing ? (
          <div className="space-y-1">
            <Textarea
              value={editValue}
              onChange={(event) => setEditValue(event.target.value)}
              className={taskCardTextareaClass}
              rows={Math.max(3, editValue.split('\n').length + 1)}
              autoFocus
            />
            <EditFieldControls onConfirm={() => confirmEdit(fieldName, label)} onCancel={cancelEdit} />
          </div>
        ) : hasItems ? (
          <div className="ml-4 space-y-0.5">
            {items.map((item, idx) => (
              <div
                key={idx}
                className={cn(
                  'flex items-start gap-1 text-[11px]',
                  needsConfirm && 'bg-amber-50/60 rounded px-1 py-0.5',
                )}
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

  const renderResources = () => {
    const resources = asItems(task.resources);
    if (resources.length === 0) return null;
    const isEditing = editingField === 'resources';
    const isDisabled = editingField !== null && !isEditing;
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-[11px] leading-none">🔗</span>
          <span className="text-[11px] font-medium text-gray-700">关联资源</span>
          <div className="flex-1" />
          {!isEditing ? (
            <Button
              variant="ghost"
              disabled={isDisabled}
              className={taskCardEditBtnClass(isDisabled)}
              onClick={() => startEdit('resources', resources)}
            >
              <Pencil className="h-2 w-2" aria-hidden />
              编辑
            </Button>
          ) : null}
        </div>
        {isEditing ? (
          <div className="space-y-1">
            <Textarea
              value={editValue}
              onChange={(event) => setEditValue(event.target.value)}
              className={taskCardTextareaClass}
              rows={Math.max(3, editValue.split('\n').length + 1)}
              placeholder="每行一个链接"
              autoFocus
            />
            <EditFieldControls onConfirm={() => confirmEdit('resources', '关联资源')} onCancel={cancelEdit} />
          </div>
        ) : (
          <div className="ml-4 space-y-0.5">
            {resources.map((url, idx) => (
              <a
                key={idx}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[11px] text-blue-600 hover:underline"
              >
                <span className="text-gray-400">•</span>
                <span className="truncate">{url}</span>
                <ExternalLink className="h-2 w-2 text-gray-400" aria-hidden />
              </a>
            ))}
          </div>
        )}
      </div>
    );
  };

  const goalEditing = editingField === 'goal';
  const goalDisabled = editingField !== null && !goalEditing;

  return (
    <div className="p-1 w-full flex justify-start task-loop-card-root">
      <div className="w-full max-w-[360px] bg-white border border-gray-100 shadow-md rounded-2xl overflow-hidden transition-all duration-500">
        {/* Header */}
        <div className="px-3.5 pt-3.5 pb-2 border-b border-gray-50">
          <div className="flex items-center gap-1">
            <span className="text-xs leading-none">📋</span>
            <span className="text-sm font-semibold tracking-tight text-gray-900">任务已就绪</span>
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
              <div className="flex-1" />
              {!goalEditing ? (
                <Button
                  variant="ghost"
                  disabled={goalDisabled}
                  className={taskCardEditBtnClass(goalDisabled)}
                  onClick={() => startEdit('goal', task.goal ?? '')}
                >
                  <Pencil className="h-2 w-2" aria-hidden />
                  编辑
                </Button>
              ) : null}
            </div>
            {goalEditing ? (
              <div className="space-y-1">
                <Textarea
                  value={editValue}
                  onChange={(event) => setEditValue(event.target.value)}
                  className={taskCardTextareaClass}
                  rows={2}
                  autoFocus
                />
                <EditFieldControls onConfirm={() => confirmEdit('goal', '目标')} onCancel={cancelEdit} />
              </div>
            ) : (
              <p className="text-xs font-semibold text-gray-900 leading-relaxed">{task.goal || '未设定'}</p>
            )}
          </div>

          {/* Editable fields */}
          {renderEditableField('交付物', '📌', 'deliverables', task.deliverables)}
          {renderEditableField('验收标准', '✅', 'acceptance_criteria', task.acceptance_criteria)}
          {renderEditableField('约束', '⚠️', 'constraints', task.constraints)}
          {renderResources()}
        </div>

        {/* Hint */}
        <div className="px-3.5 pb-2">
          <p className="text-[9px] text-gray-400 flex items-center gap-1 leading-none">
            <Lightbulb className="h-2 w-2" aria-hidden />
            需要修改？直接在对话中告诉我
          </p>
        </div>

        {/* Actions */}
        <div className="px-3.5 pb-3.5">
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" onClick={() => handleAction('discard')} className={taskCardDiscardBtnClass}>
              <Trash2 className="h-2.5 w-2.5" aria-hidden />
              丢弃
            </Button>
            <Button variant="ghost" onClick={() => handleAction('save')} className={taskCardSaveBtnClass}>
              <Bookmark className="h-2.5 w-2.5" aria-hidden />
              暂存
            </Button>
            <Button variant="ghost" onClick={() => handleAction('execute')} className={taskCardExecuteBtnClass}>
              <Play className="h-2.5 w-2.5" aria-hidden />
              执行
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
