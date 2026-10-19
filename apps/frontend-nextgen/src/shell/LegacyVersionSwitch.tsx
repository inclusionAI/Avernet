// src/shell/LegacyVersionSwitch.tsx
//
// 「返回旧版」常驻入口（OpenSpec add-gray-release-nextgen-switch 跨版本切流）。
// 挂载于 AppSidebar 底部用户区上方，形态无关：
// - 数据与写口经 getLegacyVersionSwitch capability 收口——Open Core 默认 null →
//   本组件不渲染、启动不发请求不打点；internal overlay 注入 agentclaw 灰度判定实现。
// - 启动只读拉取判定（loadLegacyVersionDecision 内含 hitRule 打点）；任何失败静默
//   返回 null → 不渲染入口，绝不影响任何渲染。
// - 点击 = 写偏好 preference=old 成功才跳 oldUrl（replace 语义）；写失败 toast 提示、
//   不跳转（显式意图落空必须可感知）。单向防乒乓：老版命中 new 才强跳，本侧只手动返回。
import type { LegacyVersionDecision } from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import { Button, IconButton } from '@/components/ui';
import { redirectReplace } from '@/utils/urlNavigation';
import { Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

/**
 * 返回旧版入口。collapsed=true 时呈图标钮（侧栏折叠态），否则整行链接（展开态）。
 * 判定未就绪 / 无旧版 URL / capability 未提供时渲染 null。
 */
export function LegacyVersionSwitch({ collapsed }: { collapsed?: boolean }) {
  const [decision, setDecision] = useState<LegacyVersionDecision | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const result = getCapabilities().getLegacyVersionSwitch();
    if (result.status !== 'available' || !result.value) return;
    // 启动只读打点随此一次判定完成；失败静默 null（旁路能力，不弹错误提示）
    void result.value.loadLegacyVersionDecision().then((d) => {
      if (!cancelled) setDecision(d);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleReturn = async () => {
    const spec = getCapabilities().getLegacyVersionSwitch().value;
    const oldUrl = decision?.oldUrl;
    if (!spec || !oldUrl || busy) return;
    setBusy(true);
    try {
      const ok = await spec.saveLegacyVersionPreference('old');
      if (ok) {
        redirectReplace(oldUrl);
        return;
      }
      toast.error('未能返回旧版，请稍后重试');
    } finally {
      setBusy(false);
    }
  };

  if (!decision?.oldUrl) return null;

  return collapsed ? (
    <IconButton
      size="sm"
      className="text-muted-foreground"
      label="返回旧版"
      icon={<Undo2 className="h-4 w-4" />}
      disabled={busy}
      onClick={() => {
        void handleReturn();
      }}
    />
  ) : (
    <Button
      variant="ghost"
      type="button"
      disabled={busy}
      onClick={() => {
        void handleReturn();
      }}
      className="flex w-full items-center gap-2.5 rounded-md border-0 bg-transparent px-3 py-2 !text-sm !font-normal text-muted-foreground transition-colors hover:bg-muted"
    >
      <Undo2 className="h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1 text-left">返回旧版</span>
    </Button>
  );
}

export default LegacyVersionSwitch;