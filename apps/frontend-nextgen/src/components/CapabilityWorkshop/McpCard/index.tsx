import React from 'react';

export interface McpCardProps {
  title?: string;
  description?: string;
}

// MCP 卡片用于承接后续真实业务渲染，当前先提供稳定的组件契约。
const McpCard: React.FC<McpCardProps> = ({ title = 'MCP 卡片', description }) => {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-white p-4">
      <div className="text-sm font-medium text-[var(--color-fg)]">{title}</div>
      {description && <p className="mt-2 text-sm text-[var(--color-muted)]">{description}</p>}
    </section>
  );
};

export default McpCard;
