import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useExternalLoginPrompt } from '@/hooks/useExternalLoginPrompt';
import { cn } from '@/utils/cn';
import { MessagesSquare, ShieldCheck, Sparkles } from 'lucide-react';
import React from 'react';

/** 登录引导卖点（对齐既有文案「专属会话 / 对话与协作 / 统一登录认证」，不新增业务承诺）。 */
const loginBenefits = [
  { icon: MessagesSquare, title: '专属会话', description: '以你的账号身份获取专属会话' },
  { icon: Sparkles, title: '完整能力', description: '继续使用对话与协作等全部能力' },
  { icon: ShieldCheck, title: '安全认证', description: '将引导你前往统一登录页完成身份认证' },
] as const;

/**
 * 全局外部登录提示弹窗（仅 `oauth-provider` 策略）。经 `useExternalLoginPrompt` 订阅 prompt 信号，
 * 唯一出路「立即登录」→ `useExternalAuth.login()`（取/拉 `/openapi/v1/auth/url` provider → `navigateToUrl`）。
 * **不可关闭**（`add-external-oauth-login` 8.8）：`showClose=false` + 拦截 ESC / 遮罩点击 / 外部交互 / 焦点离开
 * 全套关闭意图（Radix Dialog 受控 `open`，无关闭出路—— see spec「未登录时以不可关闭提示弹窗处置」）。
 * 视觉:双区结构——身份区(居中品牌 wordmark + 标题 + 副文案)与行动区(能力脊背 + 通栏主 CTA)以发丝分隔。
 * 能力脊背以单一细线串联品牌描边节点,编码「一次登录解锁一连串能力」,与邀请码门禁共用脊背语汇保持两扇门一致。
 * 均走 `@/components/ui` Modal 白名单 + 语义 token;产品名经 getProductBrand capability 解析,不硬编码。
 */
export function ExternalLoginPromptModal(): React.ReactElement {
  const { open, onLogin, loadingLoginUrl } = useExternalLoginPrompt();
  const brand = getCapabilities().getProductBrand().value;
  const BrandVisual = brand.loginWordmark ?? brand.Logo;

  return (
    <Modal open={open}>
      <ModalContent
        className="max-w-lg"
        showClose={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onFocusOutside={(e) => e.preventDefault()}
      >
        {/* 身份区:品牌 + 标题 + 副文案 */}
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandVisual className="h-10 w-auto" />
          <ModalHeader className="items-center space-y-1.5 pr-0 text-center">
            <ModalTitle className="text-lg font-semibold tracking-tight text-foreground">
              登录后继续使用 {brand.name}
            </ModalTitle>
            <ModalDescription className="max-w-xs text-balance text-sm">
              登录后可获取你的专属会话，并继续使用对话与协作能力。
            </ModalDescription>
          </ModalHeader>
        </div>

        <div aria-hidden className="h-px bg-border" />

        {/* 行动区:能力脊背(为何) → 登录 CTA(如何) */}
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span aria-hidden className="h-3.5 w-1 rounded-full bg-primary/60" />
            <span className="text-xs font-semibold tracking-wide text-foreground/80">登录后解锁</span>
          </div>
          <ul className="flex flex-col pl-0">
            {loginBenefits.map(({ icon: Icon, title, description }, idx) => {
              const isLast = idx === loginBenefits.length - 1;
              return (
                <li key={title} className="flex gap-3">
                  <span aria-hidden className="flex flex-col items-center">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-background text-primary ring-1 ring-inset ring-primary/25">
                      <Icon className="size-4" />
                    </span>
                    {!isLast && <span className="mt-1 w-px grow bg-border" />}
                  </span>
                  <span className={cn('flex min-w-0 flex-col gap-0.5', !isLast && 'pb-3.5')}>
                    <span className="text-sm font-medium leading-5 text-foreground">{title}</span>
                    <span className="text-xs leading-4 text-muted-foreground">{description}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>

        <ModalFooter className="pt-1">
          <Button variant="default" size="lg" className="h-10 w-full" loading={loadingLoginUrl} onClick={onLogin}>
            {loadingLoginUrl ? '正在前往登录…' : '立即登录'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
