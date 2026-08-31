import React from 'react';

export interface TaskEditorProps {
  title?: string;
  description?: string;
}

// 任务编辑器用于承接后续真实业务渲染，当前先提供稳定的组件契约。
const TaskEditor: React.FC<TaskEditorProps> = ({ title = '任务编辑器', description }) => {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-white p-4">
      <div className="text-sm font-medium text-[var(--color-fg)]">{title}</div>
      {description && <p className="mt-2 text-sm text-[var(--color-muted)]">{description}</p>}
    </section>
  );
};

export default TaskEditor;
