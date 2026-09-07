// 顶栏帮助菜单（Open Core 纯 UI）。问号 IconButton + Popover。
// 菜单项统一「图标+文字」行：ReleaseNote / 用户手册 / 平台指标(开 Drawer) / 答疑机器人(末项) / 产品获取(hover 子菜单)。
// - 外链经 getHelpLinks capability（Open Core=[]，internal overlay 注入内网 URL）。
// - 版本发布说明经 useReleaseNotes（OpenCore 不支持→不渲染；internal 红点+Modal）。
// - 产品获取用 Radix 嵌套 Popover（hover 触发）实现二级飞出，主菜单外点击不误关。
// 禁 antd/裸 button，用项目 Popover/IconButton。≤200 行。
import type { HelpLink } from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import { IconButton, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { useReleaseNotes } from '@/hooks/useReleaseNotes';
import { cn } from '@/utils/cn';
import {
  BarChart3,
  BookOpen,
  ChevronRight,
  HelpCircle,
  MessageCircle,
  Monitor,
  PackageOpen,
  ScrollText,
  Smartphone,
  SquareTerminal,
} from 'lucide-react';
import { useState } from 'react';
import { PlatformMetricsPanel } from './PlatformMetricsPanel';
import { ReleaseNotesModal } from './ReleaseNotesModal';

const ICON_MAP: Record<NonNullable<HelpLink['icon']>, React.ElementType> = {
  manual: BookOpen,
  robot: MessageCircle,
  tui: SquareTerminal,
  mobile: Smartphone,
  desktop: Monitor,
};

function LinkIcon({ icon }: { icon?: HelpLink['icon'] }) {
  const Icon = icon ? ICON_MAP[icon] : BookOpen;
  return <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />;
}

/** 统一行样式：图标+文字+可选右槽。外链(a)/动作(button)复用同一排版与字体。 */
const ROW_CLASS =
  'flex w-full items-center gap-2.5 rounded-md border-0 bg-transparent px-3 py-2 !text-sm !font-normal transition-colors hover:bg-muted';

/** 一级外链行：anchor 新开页。 */
function TopLinkRow({ link }: { link: HelpLink }) {
  return (
    <a href={link.href} target="_blank" rel="noopener noreferrer" className={ROW_CLASS}>
      <LinkIcon icon={link.icon} />
      <span className="flex-1 text-left">{link.label}</span>
    </a>
  );
}

/** 一级动作行：点击触发回调（ReleaseNote/平台指标用）。可带右侧红点。 */
function ActionRow({
  icon,
  label,
  onClick,
  trailing,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} className={ROW_CLASS}>
      {icon}
      <span className="flex-1 text-left">{label}</span>
      {trailing}
    </button>
  );
}

/** 产品获取：点击 inline 展开/折叠子项（accordion）。避免 hover 飞出在 Popover 内的交互不稳定。 */
function ProductSubmenu({ items }: { items: HelpLink[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen((v) => !v)} className={ROW_CLASS}>
        <PackageOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="flex-1 text-left">产品获取</span>
        <ChevronRight
          className={cn('h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
          aria-hidden
        />
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5 border-l border-border pl-2 ml-3">
          {items.map((l) => (
            <a key={l.label} href={l.href} target="_blank" rel="noopener noreferrer" className={ROW_CLASS}>
              <LinkIcon icon={l.icon} />
              <span className="flex-1 text-left">{l.label}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export function HelpMenu() {
  const [open, setOpen] = useState(false);
  const [metricsOpen, setMetricsOpen] = useState(false);
  const links = getCapabilities().getHelpLinks().value;
  const release = useReleaseNotes();

  const manual = links.find((l) => l.group === 'manual');
  const robot = links.find((l) => l.group === 'robot');
  const product = links.filter((l) => l.group === 'product');
  const showRelease = release.supported;

  const hasAny = !!manual || !!robot || product.length > 0;

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <IconButton label="帮助" icon={<HelpCircle className="h-4 w-4" />} />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-56 p-1.5">
          {hasAny ? (
            <div className="space-y-0.5">
              {showRelease && (
                <ActionRow
                  icon={<ScrollText className="h-4 w-4 shrink-0 text-muted-foreground" />}
                  label="ReleaseNote"
                  trailing={
                    release.hasNew ? <span className="h-2 w-2 rounded-full bg-red-500" aria-label="有新版本" /> : null
                  }
                  onClick={() => {
                    setOpen(false);
                    release.open();
                  }}
                />
              )}
              {manual && <TopLinkRow link={manual} />}
              <ActionRow
                icon={<BarChart3 className="h-4 w-4 shrink-0 text-muted-foreground" />}
                label="平台指标"
                onClick={() => {
                  setOpen(false);
                  setMetricsOpen(true);
                }}
              />
              {robot && <TopLinkRow link={robot} />}
              {product.length > 0 && <ProductSubmenu items={product} />}
            </div>
          ) : (
            <p className="px-3 py-4 text-center !text-sm text-muted-foreground">暂无帮助入口</p>
          )}
        </PopoverContent>
      </Popover>

      {showRelease && <ReleaseNotesModal open={release.modalOpen} data={release.data} onClose={release.closeModal} />}
      <PlatformMetricsPanel open={metricsOpen} onClose={() => setMetricsOpen(false)} />
    </>
  );
}

export default HelpMenu;
