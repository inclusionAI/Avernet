import { IconButton } from '@/components/ui';
import { FileText, X } from 'lucide-react';
import { DEMO_MESSAGES, DEMO_SESSION_TITLE } from './demoContent';

export type DemoContextPanelKind = 'detail' | 'history' | 'resources' | 'sidepane';
const PANEL_TITLES: Record<DemoContextPanelKind, string> = {
  detail: '会话详情',
  history: '历史消息',
  resources: '会话资源',
  sidepane: '副屏',
};

/** 其余头部入口也有可关闭的本地场景，不加载真实会话或 SDK 服务。 */
export function DemoContextPanel({ kind, onClose }: { kind: DemoContextPanelKind; onClose: () => void }) {
  const title = PANEL_TITLES[kind];
  return (
    <aside className="task-escort-demo-context border-l border-border bg-card shadow-lg" aria-label={`${title}（示例）`}>
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <h2 className="m-0 text-sm font-medium">{title}</h2>
        <IconButton label={`关闭${title}`} icon={<X className="size-4" />} onClick={onClose} />
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 text-xs">
        <p className="text-muted-foreground">DEMO · 仅展示预置内容，无真实请求。</p>
        {kind === 'detail' && (
          <dl className="space-y-3">
            <dt className="text-muted-foreground">会话名称</dt><dd className="m-0">{DEMO_SESSION_TITLE}</dd>
            <dt className="text-muted-foreground">会话来源</dt><dd className="m-0">本页内存中的虚构示例</dd>
            <dt className="text-muted-foreground">消息数量</dt><dd className="m-0">3 条</dd>
          </dl>
        )}
        {kind === 'history' && (
          <ol className="m-0 list-none space-y-4 p-0">
            {DEMO_MESSAGES.map((message) => (
              <li key={message.id} className="space-y-2 rounded-lg bg-muted/50 p-3">
                <p className="m-0 text-[10px] text-muted-foreground">{message.role === 'user' ? '演示访客' : 'Bot'} · {message.time}</p>
                <p className="m-0 leading-6">{message.text}</p>
              </li>
            ))}
          </ol>
        )}
        {kind === 'resources' && (
          <ul className="m-0 list-none space-y-3 p-0">
            {['示例指标.csv', '巡检报告.md'].map((name) => (
              <li key={name} className="flex items-center gap-2 rounded-lg border border-border p-3">
                <FileText className="size-4 text-muted-foreground" aria-hidden="true" /> {name}
                <span className="ml-auto text-[10px] text-muted-foreground">示例</span>
              </li>
            ))}
          </ul>
        )}
        {kind === 'sidepane' && (
          <div className="rounded-xl border border-border bg-muted/30 p-4 leading-7">
            <p className="m-0 font-medium">模拟结果副屏</p>
            <p className="mb-0 text-muted-foreground">打开任务护航会自动收起此副屏，为运行概览与编辑器让出空间。</p>
          </div>
        )}
      </div>
    </aside>
  );
}
