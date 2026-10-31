// @asset-migrated: teamclaw 自研
/** CapabilitySetViewer —— 「通用协作能力」能力集只读查看（列表 ⇄ 明细 双页气泡）。
 *
 * 视觉/交互对齐 PRD demo（Teamclaw_PRD_new InputPanel useCapabilityMenu）：
 * 单气泡双页导航（capabilityView list ⇄ detail 先例）——不再嵌套右侧子气泡，
 * 头部原位换题（「已激活的能力集」⇄ 集名 + 返回），列表行整行可点开明细；
 * 明细 = Skills + MCP 两段 muted 容器 + 白底行卡（类型徽章 + 数量胶囊），只读无编辑。
 * 规格：openspec add-composer-capability-set-viewer specs「查看明细只读」；
 * 数据拉取由宿主（ComposerCapabilitiesMenu onOpenChange）负责，每次打开重拉。 */
import { Badge, Button } from '@/components/ui';
import type {
  CapabilitySetDetailResult,
  CapabilitySetSkillView,
  CapabilitySetView,
  ComposerCapabilitySetsState,
} from '@/components/Workspace/CapabilitySetViewer/types';
import { ArrowLeft, Boxes, ChevronRight, Loader2, Plug, RotateCcw, Zap } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

/** 单集明细状态：loading / failed / ready（Skills 按需拉取，进明细页即拉）。 */
interface SetDetailState {
  loading: boolean;
  failed: boolean;
  skills: CapabilitySetSkillView[];
}

const IDLE_DETAIL: SetDetailState = { loading: false, failed: false, skills: [] };

/** 面板头：标题 + 提示行 + 右侧动作（返回）；list/detail 双页共用几何（PRD demo 头部规格）。 */
function ViewerHeader({
  title,
  hint,
  badge,
  action,
}: {
  title: string;
  hint?: string | null;
  badge?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-2 border-b border-divider/70 px-3.5 py-2.5">
      <div className="min-w-0">
        <p className="m-0 flex items-center gap-1.5 text-[13px] font-semibold leading-5 text-foreground">
          <span className="min-w-0 truncate">{title}</span>
          {badge}
        </p>
        {hint ? <p className="m-0 mt-0.5 truncate text-[11px] leading-4 text-muted-foreground">{hint}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** 列表行卡：整行可点 → 明细页；名 + 默认徽章 / 描述或 MCP 计数 + 「查看明细 ›」主色引导。 */
function CapabilitySetRow({ set, onOpen }: { set: CapabilitySetView; onOpen: () => void }) {
  return (
    <Button
      variant="ghost"
      onClick={onOpen}
      className="h-auto w-full gap-0 rounded-xl px-3 py-2.5 text-left text-[13px] font-normal"
    >
      <span className="flex w-full items-start justify-between gap-3">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="min-w-0 truncate font-medium text-foreground">{set.name}</span>
            {set.isDefault ? (
              <Badge tone="primary" className="shrink-0 px-1.5 py-0 text-[10px] leading-4">
                默认
              </Badge>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-[11px] font-normal leading-4 text-muted-foreground">
            {set.description || `内含 ${set.mcps.length} 个 MCP`}
          </span>
        </span>
        <span className="mt-0.5 inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium leading-none text-primary">
          查看明细
          <ChevronRight aria-hidden className="size-3" />
        </span>
      </span>
    </Button>
  );
}

/** 明细成员段：muted 容器 + 段标题/数量胶囊 + 白底行卡（icon + 名 + 类型徽章）；只读无编辑入口。 */
function CapabilityMembers({
  label,
  count,
  icon,
  tone,
  emptyText,
  items,
}: {
  label: string;
  count: number;
  icon: ReactNode;
  tone: 'skill' | 'mcp';
  emptyText: string;
  items: { key: string; name: string; description?: string }[];
}) {
  return (
    <section className="rounded-xl bg-muted/50 p-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
        <span className="rounded-full bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {count} 个
        </span>
      </div>
      {items.length ? (
        <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0">
          {items.map((item) => (
            <li
              key={item.key}
              title={item.description || undefined}
              className="flex items-center gap-2 rounded-lg bg-background px-2.5 py-2"
            >
              {icon}
              <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground">{item.name}</span>
              <span
                className={
                  tone === 'skill'
                    ? 'shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary'
                    : 'shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground'
                }
              >
                {tone === 'skill' ? 'Skill' : 'MCP'}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 mt-1.5 text-[11px] leading-5 text-muted-foreground/75">{emptyText}</p>
      )}
    </section>
  );
}

/** 能力集双页气泡：list（已激活集卡片）/ detail（单集 Skills+MCP 只读）。 */
export function CapabilitySetViewer({ state }: { state: ComposerCapabilitySetsState }) {
  const { sets, isLoading, fetchSetSkills } = state;
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SetDetailState>(IDLE_DETAIL);
  const [detailKey, setDetailKey] = useState(0);

  const activeSet = sets.find((s) => s.id === activeId) ?? null;

  // 进明细页即拉（与列表「打开即拉」同一时效原则）；重试点显式触发。
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    setDetail({ loading: true, failed: false, skills: [] });
    void fetchSetSkills(activeId).then((result: CapabilitySetDetailResult) => {
      if (cancelled) return;
      setDetail({ loading: false, failed: result.failed, skills: result.skills });
    });
    return () => {
      cancelled = true;
    };
    // detailKey 仅作重试触发器：fetchSetSkills 依赖 bot 已稳定（hook useCallback([bot])）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, detailKey]);

  const backToList = () => {
    setActiveId(null);
    setDetail(IDLE_DETAIL);
  };

  const body = (() => {
    if (activeSet) {
      if (detail.loading) {
        return (
          <div role="status" className="flex items-center justify-center gap-1.5 px-3 py-8 text-xs text-muted-foreground">
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
            正在加载明细…
          </div>
        );
      }
      if (detail.failed) {
        return (
          <div className="flex flex-col items-center gap-1.5 px-3 py-8 text-center">
            <p className="m-0 text-xs text-destructive">明细加载失败</p>
            <Button
              variant="ghost"
              className="h-6 gap-1 px-1 text-xs text-primary"
              onClick={() => setDetailKey((key) => key + 1)}
            >
              <RotateCcw aria-hidden className="size-3" />
              重试
            </Button>
          </div>
        );
      }
      return (
        <div className="capability-page-in flex flex-col gap-2">
          <CapabilityMembers
            label="Skill"
            count={detail.skills.length}
            icon={<Zap aria-hidden className="size-3.5 shrink-0 text-primary" />}
            tone="skill"
            emptyText="当前还没有 Skill"
            items={detail.skills.map((skill) => ({
              key: skill.skillId,
              name: skill.name || skill.skillId,
              description: skill.description,
            }))}
          />
          <CapabilityMembers
            label="MCP"
            count={activeSet.mcps.length}
            icon={<Plug aria-hidden className="size-3.5 shrink-0 text-info" />}
            tone="mcp"
            emptyText="当前还没有 MCP"
            items={activeSet.mcps.map((mcp) => ({
              key: mcp.serverCode,
              name: mcp.name || mcp.serverCode,
              description: mcp.description,
            }))}
          />
        </div>
      );
    }
    if (isLoading) {
      return (
        <div role="status" className="flex flex-col items-center gap-2 px-6 py-10">
          <Loader2 aria-hidden className="size-4 animate-spin text-muted-foreground" />
          <p className="m-0 text-xs text-muted-foreground">加载中…</p>
        </div>
      );
    }
    if (sets.length === 0) {
      return (
        <div className="flex flex-col items-center gap-1 px-6 py-9 text-center">
          <span className="flex size-9 items-center justify-center rounded-xl bg-muted text-muted-foreground">
            <Boxes aria-hidden className="size-4" />
          </span>
          <p className="m-0 mt-1.5 text-[13px] font-medium text-foreground">暂无已激活的能力集</p>
          <p className="m-0 text-[11px] leading-4 text-muted-foreground">Bot 启用能力集后，Skill 与 MCP 会在这里展示</p>
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-1">
        {sets.map((set) => (
          <CapabilitySetRow key={set.id} set={set} onOpen={() => setActiveId(set.id)} />
        ))}
      </div>
    );
  })();

  return (
    <div className="flex w-full flex-col">
      {activeSet ? (
        <ViewerHeader
          title={activeSet.name}
          hint={
            detail.loading
              ? null
              : detail.failed
                ? null
                : `共 ${detail.skills.length} 个 Skill · ${activeSet.mcps.length} 个 MCP`
          }
          badge={
            activeSet.isDefault ? (
              <Badge tone="primary" className="shrink-0 px-1.5 py-0 text-[10px] leading-4">
                默认
              </Badge>
            ) : null
          }
          action={
            <Button variant="secondary" className="h-6 gap-1 rounded-lg px-2.5 text-[11px] font-normal" onClick={backToList}>
              <ArrowLeft aria-hidden className="size-3" />
              返回
            </Button>
          }
        />
      ) : (
        <ViewerHeader title="已激活的能力集" hint="点击能力集查看 Skill 与 MCP" />
      )}
      <div className="max-h-80 overflow-y-auto p-2">{body}</div>
    </div>
  );
}