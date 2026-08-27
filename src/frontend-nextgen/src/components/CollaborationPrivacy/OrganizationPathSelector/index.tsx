import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import type { OrganizationPath } from '@/domain/collaborationPrivacy/types';
import { Check, ChevronRight, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { buildOrganizationColumns, organizationPathKey, toggleOrganizationPath } from './model';

interface OrganizationPathSelectorProps {
  options: OrganizationPath[];
  selected: OrganizationPath[];
  onChange: (paths: OrganizationPath[]) => void;
  label: string;
}

const visiblePath = (path: OrganizationPath) => path.join(' / ');

export function OrganizationPathSelector({ options, selected, onChange, label }: OrganizationPathSelectorProps) {
  const [activePath, setActivePath] = useState<OrganizationPath>([]);
  const columns = useMemo(() => buildOrganizationColumns(options, activePath), [activePath, options]);

  return (
    <div aria-label={label} className="space-y-3">
      <div className="flex max-w-full gap-2 overflow-x-auto pb-2">
        {columns.map((nodes, depth) => (
          <div
            key={depth}
            className="max-h-56 w-56 shrink-0 space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] p-1"
          >
            {nodes.map((node) => {
              const active = activePath[depth] === node.label;
              const checked = selected.some((item) => organizationPathKey(item) === organizationPathKey(node.path));
              return (
                <div key={organizationPathKey(node.path)} className="flex items-stretch gap-1">
                  <Button
                    variant={checked ? 'primary' : 'ghost'}
                    size="sm"
                    className="h-auto min-h-8 min-w-0 flex-1 justify-start whitespace-normal break-words px-2 py-1.5 text-left"
                    aria-pressed={checked}
                    onClick={() => onChange(toggleOrganizationPath(selected, node.path))}
                  >
                    {checked && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
                    <span className="min-w-0">{node.label}</span>
                  </Button>
                  {node.hasChildren && (
                    <IconButton
                      label={`查看 ${node.label} 的下级组织`}
                      icon={<ChevronRight className="h-3.5 w-3.5" aria-hidden />}
                      size="sm"
                      variant={active ? 'secondary' : 'ghost'}
                      className="h-auto min-h-8 w-8 shrink-0"
                      onClick={() => setActivePath(node.path)}
                    />
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="rounded-lg bg-[var(--color-panel-muted)] p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="m-0 text-xs font-medium text-[var(--color-fg)]">已选组织范围（{selected.length}）</p>
          {selected.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => onChange([])}>
              清空
            </Button>
          )}
        </div>
        {selected.length === 0 ? (
          <p className="mb-0 mt-2 text-xs text-[var(--color-muted)]">可选择任意层级的组织节点，并支持跨组织多选。</p>
        ) : (
          <ul className="mb-0 mt-2 space-y-1 p-0">
            {selected.map((path) => (
              <li
                key={organizationPathKey(path)}
                className="flex items-center justify-between gap-2 text-xs text-[var(--color-muted)]"
              >
                <span className="min-w-0 break-words">{visiblePath(path)}</span>
                <IconButton
                  label={`移除 ${visiblePath(path)}`}
                  icon={<X className="h-3.5 w-3.5" aria-hidden />}
                  size="sm"
                  className="h-6 w-6"
                  onClick={() =>
                    onChange(selected.filter((item) => organizationPathKey(item) !== organizationPathKey(path)))
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
