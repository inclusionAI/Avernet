import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useInviteCodeGate } from '@/hooks/useInviteCodeGate';
import { useInviteCodePrompt } from '@/hooks/useInviteCodePrompt';
import { KeyRound, MessagesSquare, ShieldCheck, Sparkles } from 'lucide-react';
import React, { useState } from 'react';

/** 绑定价值主张（对齐 ExternalLoginPromptModal loginBenefits 观感，不新增业务承诺）。 */
const bindingBenefits = [
  { icon: Sparkles, title: '解锁完整能力', description: '绑定后即可使用对话与协作等全部功能' },
  { icon: MessagesSquare, title: '专属会话', description: '以你的账号身份获取专属会话体验' },
  { icon: ShieldCheck, title: '安全准入', description: '邀请码校验为产品对外准入控制' },
] as const;

/**
 * 全局邀请码门禁输入弹窗（仅 `oauth-provider` 策略下由门禁信号触发）。经 `useInviteCodePrompt` 订阅 prompt 信号，
 * 唯一出路「提交邀请码」→ `useInviteCodeGate.submitCode`（`POST /openapi/v1/collaboration/invite-codes/bind` 成功 →
 * 整页 reload 回流）。**不可关闭**：`showClose=false` + 拦截 ESC / 遮罩点击 / 外部交互 / 焦点离开全套关闭意图
 * （Radix Dialog 受控 `open`，无关闭出路）——未绑码用户必须提交有效邀请码才能使用产品。
 * 视觉：居中品牌区（`getProductBrand()` loginWordmark 缺省回退 Logo）+ 标题副文案 + 邀请码输入（图标 + 大尺寸 +
 * 字距）+ 价值主张清单 + 通栏主 CTA + 无码求助副文案，均走 `@/components/ui` 白名单 + 语义 token，产品名经 capability 解析不硬编码。
 */
export function InviteCodeBindingModal(): React.ReactElement {
  const { open } = useInviteCodePrompt();
  const { submitCode, submitting, submitError, clearSubmitError } = useInviteCodeGate();
  const brand = getCapabilities().getProductBrand().value;
  const BrandVisual = brand.loginWordmark ?? brand.Logo;
  const [code, setCode] = useState('');

  const canSubmit = code.trim().length > 0 && !submitting;

  const handleSubmit = (): void => {
    void submitCode(code);
  };

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    setCode(event.target.value);
    if (submitError) clearSubmitError();
  };

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
            <ModalTitle className="text-base">输入邀请码以开始使用 {brand.name}</ModalTitle>
            <ModalDescription className="max-w-xs text-balance">
              你已成功登录，输入邀请码即可解锁全部能力。
            </ModalDescription>
          </ModalHeader>
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium leading-5" htmlFor="invite-code-input">
            邀请码
          </label>
          <div className="relative">
            <KeyRound
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="invite-code-input"
              className="h-10 pl-9 text-sm tracking-widest"
              placeholder="请输入邀请码"
              value={code}
              onChange={handleChange}
              disabled={submitting}
              autoComplete="off"
              autoFocus
              aria-invalid={submitError ? true : undefined}
            />
          </div>
          {submitError ? (
            <p role="alert" className="text-xs leading-4 text-destructive">
              {submitError.message}
            </p>
          ) : (
            <p className="text-xs leading-4 text-muted-foreground">输入你收到的邀请码，绑定后即可使用全部功能。</p>
          )}
        </div>
        <ul className="flex flex-col gap-2.5">
          {bindingBenefits.map(({ icon: Icon, title, description }) => (
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
        <ModalFooter className="pt-1 sm:flex-col sm:justify-stretch">
          <Button
            variant="default"
            size="lg"
            className="w-full"
            loading={submitting}
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {submitting ? '正在提交…' : '提交邀请码'}
          </Button>
        </ModalFooter>
        <p className="text-center text-xs text-muted-foreground">没有邀请码？请联系管理员获取。</p>
      </ModalContent>
    </Modal>
  );
}
