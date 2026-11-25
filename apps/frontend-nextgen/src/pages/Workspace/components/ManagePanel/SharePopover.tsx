import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/Popover';
import { Segmented } from '@/components/ui/Segmented';
import type { DomainResult } from '@/services/workspace/identityService';
import { Check, Copy, Loader2, RefreshCw, Share2, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

/** 有效期候选（dmore artboard-010「默认 1 天」明示；不虚构「永久」）。 */
const EXPIRY_OPTIONS: { label: string; seconds: number }[] = [
  { label: '1 天', seconds: 24 * 60 * 60 },
  { label: '7 天', seconds: 7 * 24 * 60 * 60 },
  { label: '30 天', seconds: 30 * 24 * 60 * 60 },
];

const ONE_DAY_SECONDS = EXPIRY_OPTIONS[0].seconds;

export interface SharePopoverProps {
  open: boolean;
  /** 定位锚点：各入口触发图标的 ref（气泡经 virtualRef 悬浮其下，不重渲染触发元素）。 */
  anchorRef: React.RefObject<HTMLElement | null>;
  /** 目标名（「会话」/「协作群」），用于标题与说明文案。 */
  title: string;
  /** 点击「生成链接」时携带所选有效期秒数发起请求；生成流程收敛在气泡内。 */
  onCreate: (expiresInSeconds: number) => Promise<DomainResult<{ invitationUrl: string }>>;
  onClose: () => void;
  /** 气泡与锚点的水平对齐；默认 end（右缘图标下），管理面板整行触发钮用 start。 */
  align?: 'start' | 'center' | 'end';
}

/**
 * 群/会话分享气泡 v3（2026-10-13 走查反馈：点分享图标出气泡而非弹窗，内容重做）：
 * 锚定在触发图标正下方的 Popover——头部为着色 Share2 glyph +「分享{title}」；
 * 有效期用三段式 Segmented 胶囊（默认 1 天）；生成前主操作为全宽「生成链接」，
 * 生成后链接以 mono 「收据」呈现（内嵌复制钮 Copy→Check、到期脚注 + 重新生成）。
 * 生成失败 toast 提示且气泡保持打开可重试；generating 期间阻止外点/Esc 关闭。
 * 有效期为纯前端选项，透传邀请接口 `expires_in_seconds` 可选参数。
 */
export function SharePopover({ open, anchorRef, title, onCreate, onClose, align = 'end' }: SharePopoverProps) {
  const [expirySeconds, setExpirySeconds] = useState(ONE_DAY_SECONDS);
  const [generating, setGenerating] = useState(false);
  const [invitationUrl, setInvitationUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const expiryLabel = EXPIRY_OPTIONS.find((option) => option.seconds === expirySeconds)?.label ?? '1 天';

  const generate = async () => {
    if (generating) return;
    setGenerating(true);
    setCopied(false);
    const res = await onCreate(expirySeconds);
    setGenerating(false);
    if (!res.ok) {
      setInvitationUrl(null);
      toast.error(res.error?.friendlyMessage || '生成邀请链接失败，请稍后重试。');
      return;
    }
    setInvitationUrl(res.data.invitationUrl);
  };

  const handleCopy = async () => {
    if (!invitationUrl) return;
    try {
      await navigator.clipboard.writeText(invitationUrl);
      setCopied(true);
      toast.success('邀请链接已复制');
    } catch {
      toast.error('复制失败，请手动复制。');
    }
  };

  return (
    <Popover open={open} onOpenChange={(next) => !next && !generating && onClose()}>
      <PopoverAnchor virtualRef={anchorRef} />
      <PopoverContent
        align={align}
        sideOffset={8}
        className="w-80 rounded-xl border-border bg-popover p-0 shadow-lg"
      >
        {/* 头部：着色 glyph + 标题 + 关闭。拆分上下结构（头/体）给气泡一个清晰的章节感。 */}
        <div className="flex items-center gap-2.5 border-b border-divider/60 px-3.5 py-3">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Share2 className="size-3.5" aria-hidden />
          </span>
          <span className="flex-1 truncate text-[13px] font-semibold leading-4 text-foreground">分享{title}</span>
          <IconButton
            label="关闭分享气泡"
            size="sm"
            disabled={generating}
            icon={<X className="size-3.5" aria-hidden />}
            className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={onClose}
          />
        </div>

        <div className="space-y-3 px-3.5 py-3">
          <p className="m-0 text-xs leading-5 text-muted-foreground">
            选择有效期并生成链接，其他用户可通过链接加入该{title}，直到过期
          </p>

          <div className="space-y-1.5">
            <span className="block text-[11px] font-medium leading-4 text-muted-foreground">链接有效期</span>
            <Segmented
              aria-label="链接有效期"
              value={String(expirySeconds)}
              options={EXPIRY_OPTIONS.map((option) => ({ value: String(option.seconds), label: option.label }))}
              onChange={(value) => {
                setExpirySeconds(Number(value));
                setCopied(false);
              }}
            />
          </div>

          {invitationUrl ? (
            /* 收据态：链接成为主角 —— mono 胶囊 + 内嵌复制钮；脚注给它一个「凭证」的结束语。 */
            <div className="rounded-lg bg-muted px-2.5 py-2">
              <div className="flex items-start gap-1">
                <span
                  aria-label="邀请链接"
                  className="m-0 min-w-0 flex-1 select-all break-all font-mono text-xs leading-5 text-foreground"
                >
                  {invitationUrl}
                </span>
                <IconButton
                  label={copied ? '已复制，点击再次复制' : '复制链接'}
                  ariaLabel="复制链接"
                  icon={
                    copied ? <Check className="size-3.5 text-primary" aria-hidden /> : <Copy className="size-3.5" aria-hidden />
                  }
                  size="sm"
                  onClick={() => void handleCopy()}
                  className="-mr-1 -mt-0.5 size-6 shrink-0 rounded-md text-muted-foreground hover:bg-background hover:text-foreground"
                />
              </div>
              <div className="mt-1.5 flex items-center justify-between border-t border-divider/60 pt-1.5">
                <span className="text-[11px] leading-4 text-muted-foreground">链接 {expiryLabel}后过期</span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={generating}
                  onClick={() => void generate()}
                  className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                >
                  {generating ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <RefreshCw className="size-3" aria-hidden />}
                  重新生成
                </Button>
              </div>
            </div>
          ) : (
            <Button className="w-full" loading={generating} disabled={generating} onClick={() => void generate()}>
              {generating ? '生成中…' : '生成链接'}
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
