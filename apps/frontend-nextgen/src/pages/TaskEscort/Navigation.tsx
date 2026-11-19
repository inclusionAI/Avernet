import { Button } from '@/components/ui/Button';
import type { PageId } from '@/domain/taskEscortWorkspace/types';
import { Activity, CircleHelp, Code2, Layers3, LayoutDashboard, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import type { CSSProperties } from 'react';
import { useSidebarResize } from './useSidebarResize';

export const pages: {
  id: Exclude<PageId, 'workbench'>;
  label: string;
  title: string;
  description: string;
  icon: typeof Activity;
  group: string;
}[] = [
  {
    id: 'dashboard',
    label: '数据大盘',
    title: '数据大盘',
    description: '查看工作流运行指标、趋势与记录。',
    icon: LayoutDashboard,
    group: '全局',
  },
  {
    id: 'runs',
    label: '运行概览',
    title: '运行概览',
    description: '查看当前工作流的运行状态与近期表现',
    icon: Activity,
    group: '分析与改进',
  },
  {
    id: 'issues',
    label: '问题与优化',
    title: '问题与优化',
    description: '从异常证据到建议、应用与效果验证',
    icon: CircleHelp,
    group: '分析与改进',
  },
  {
    id: 'experiences',
    label: '可复用经验',
    title: '可复用经验',
    description: '查看经过边界审核、可复用的知识与修法',
    icon: Layers3,
    group: '分析与改进',
  },
  {
    id: 'editor',
    label: '编辑器',
    title: '编辑器',
    description: '编辑工作流结构、节点与执行配置',
    icon: Code2,
    group: '工作流配置',
  },
  {
    id: 'settings',
    label: '管理设置',
    title: '管理设置',
    description: '管理版本、权限、通知与回调配置',
    icon: SlidersHorizontal,
    group: '工作流配置',
  },
];

export function GuardSidebar({ page, navigate }: { page: PageId; navigate: (page: PageId) => void }) {
  const compact = page === 'editor';
  const resize = useSidebarResize(compact);
  return (
    <aside
      className={`guard-sidebar${compact ? ' guard-sidebar-compact' : ''}`}
      style={{ '--guard-sidebar-width': `${resize.width}px` } as CSSProperties}
    >
      <div className="guard-brand">
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
          <ShieldCheck size={21} aria-hidden />
        </div>
        <div className={compact ? 'guard-nav-label' : undefined}>
          <div className="text-sm font-semibold">任务护航</div>
          <div className="mt-1 font-mono text-[9px] tracking-[.15em] text-muted-foreground">TASK GUARD</div>
        </div>
      </div>
      <nav className="guard-nav" aria-label="任务护航导航">
        {['全局', '分析与改进', '工作流配置'].map((group) => (
          <div className="guard-nav-group" key={group}>
            <p className="nav-group">{group}</p>
            {pages
              .filter((item) => item.group === group)
              .map((item) => (
                <Button
                  aria-label={item.label}
                  aria-current={(page === 'workbench' ? 'issues' : page) === item.id ? 'page' : undefined}
                  key={item.id}
                  variant="ghost"
                  className={`guard-nav-item ${(page === 'workbench' ? 'issues' : page) === item.id ? 'active' : ''}`}
                  onClick={() => navigate(item.id)}
                >
                  <item.icon size={20} aria-hidden />
                  <span className="guard-nav-label">{item.label}</span>
                </Button>
              ))}
          </div>
        ))}
      </nav>
      {!compact && <div className="guard-sidebar-resize" {...resize.handleProps} />}
    </aside>
  );
}
