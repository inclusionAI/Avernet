import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useExternalLoginPrompt } from '@/hooks/useExternalLoginPrompt';
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
 * 视觉：居中品牌区（`ProductBrand.LoginWordmark` 缺省回退 `Logo`，className 只调尺寸）+ 登录卖点清单 +
 * 通栏主 CTA，均走 `@/components/ui` Modal 白名单 + 语义 token；产品名经 getProductBrand capability 解析，不硬编码。
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
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandVisual className="h-10 w-auto" />
          <ModalHeader className="items-center space-y-1.5 pr-0 text-center">
            <ModalTitle className="text-base">登录后继续使用 {brand.name}</ModalTitle>
            <ModalDescription className="max-w-xs text-balance">
              登录后可获取你的专属会话，并继续使用对话与协作能力。
            </ModalDescription>
          </ModalHeader>
        </div>
        <ul className="flex flex-col gap-2.5">
          {loginBenefits.map(({ icon: Icon, title, description }) => (
            <li key={title} className="flex items-center gap-3 rounded-lg bg-muted/50 px-3.5 py-2.5">
              <span
                aria-hidden
                className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary"
              >
                <Icon className="size-4" />
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-sm font-medium leading-5">{title}</span>
                <span className="truncate text-xs leading-4 text-muted-foreground">{description}</span>
              </span>
            </li>
          ))}
        </ul>
        <ModalFooter className="pt-1">
          <Button variant="default" size="lg" className="w-full" loading={loadingLoginUrl} onClick={onLogin}>
            {loadingLoginUrl ? '正在前往登录…' : '立即登录'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
