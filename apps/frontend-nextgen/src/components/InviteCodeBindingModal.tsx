import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useInviteCodeGate } from '@/hooks/useInviteCodeGate';
import { useInviteCodePrompt } from '@/hooks/useInviteCodePrompt';
import { cn } from '@/utils/cn';
import { CircleAlert, KeyRound, MessagesSquare, ShieldCheck, Sparkles } from 'lucide-react';
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
 * 视觉:双区结构——身份区(居中品牌 wordmark + 标题 + 副文案)与行动区(能力脊背 + 邀请码凭据输入 +
 * 通栏主 CTA + 求助副文案)以发丝分隔。能力脊背以单一细线串联品牌描边节点,编码「一码解锁一连串能力」;
 * 邀请码输入作凭据化处理(等宽字距 + 凹陷底 + 钥匙色块,聚焦时底色清空、品牌环亮起)。
 * 均走 `@/components/ui` 白名单 + 语义 token,产品名经 capability 解析不硬编码。
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
        {/* 身份区:品牌 + 标题 + 副文案 */}
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandVisual className="h-10 w-auto" />
          <ModalHeader className="items-center space-y-1.5 pr-0 text-center">
            <ModalTitle className="text-lg font-semibold tracking-tight text-foreground">
              输入邀请码以开始使用 {brand.name}
            </ModalTitle>
            <ModalDescription className="max-w-xs text-balance text-sm">
              你已成功登录，输入邀请码即可解锁全部能力。
            </ModalDescription>
          </ModalHeader>
        </div>

        <div aria-hidden className="h-px bg-border" />

        {/* 行动区:能力脊背(为何) → 邀请码凭据(如何) */}
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span aria-hidden className="h-3.5 w-1 rounded-full bg-primary/60" />
            <span className="text-xs font-semibold tracking-wide text-foreground/80">绑定后解锁</span>
          </div>
          <ul className="flex flex-col pl-0">
            {bindingBenefits.map(({ icon: Icon, title, description }, idx) => {
              const isLast = idx === bindingBenefits.length - 1;
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

        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium leading-5 text-foreground" htmlFor="invite-code-input">
            邀请码
          </label>
          <div className="relative">
            <span
              aria-hidden
              className="pointer-events-none absolute left-1.5 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-md bg-primary/10 text-primary"
            >
              <KeyRound className="size-4" />
            </span>
            <Input
              id="invite-code-input"
              className="h-12 rounded-lg border-input bg-muted/40 pl-12 font-mono text-base tracking-[0.3em] text-foreground placeholder:font-sans placeholder:tracking-normal placeholder:text-muted-foreground focus-visible:border-brand focus-visible:bg-background focus-visible:ring-2 focus-visible:ring-brand/25"
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
            <p role="alert" className="flex items-start gap-1.5 text-xs leading-4 text-destructive">
              <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
              <span>{submitError.message}</span>
            </p>
          ) : (
            <p className="text-xs leading-4 text-muted-foreground">输入你收到的邀请码，绑定后即可使用全部功能。</p>
          )}
        </div>

        <ModalFooter className="pt-1">
          <Button
            variant="default"
            size="lg"
            className="h-10 w-full"
            loading={submitting}
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {submitting ? '正在提交…' : '提交邀请码'}
          </Button>
        </ModalFooter>
        <p className="text-center text-xs text-muted-foreground">没有邀请码？请联系管理员 / 扫码领取</p>
      </ModalContent>
    </Modal>
  );
}
