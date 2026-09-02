import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useExternalLoginPrompt } from '@/hooks/useExternalLoginPrompt';
import React from 'react';

/**
 * 全局外部登录提示弹窗（仅 `oauth-provider` 策略）。经 `useExternalLoginPrompt` 订阅 prompt 信号，
 * 唯一出路「立即登录」→ `useExternalAuth.login()`（取/拉 `/openapi/v1/auth/url` provider → `navigateToUrl`）。
 * **不可关闭**（`add-external-oauth-login` 8.8）：`showClose=false` + 拦截 ESC / 遮罩点击 / 外部交互 / 焦点离开
 * 全套关闭意图（Radix Dialog 受控 `open`，无关闭出路—— see spec「未登录时以不可关闭提示弹窗处置」）。
 * 用 `@/components/ui` Modal 白名单 + 语义 token；产品名经 getProductBrand capability 解析，不硬编码。
 */
export function ExternalLoginPromptModal(): React.ReactElement {
  const { open, onLogin, loadingLoginUrl } = useExternalLoginPrompt();
  const brand = getCapabilities().getProductBrand().value;

  return (
    <Modal open={open}>
      <ModalContent
        size="sm"
        showClose={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onFocusOutside={(e) => e.preventDefault()}
      >
        <ModalHeader>
          <ModalTitle>登录后继续使用 {brand.name}</ModalTitle>
          <ModalDescription>登录后可获取你的专属会话，并继续使用对话与协作能力。</ModalDescription>
        </ModalHeader>
        <ModalFooter>
          <Button variant="default" loading={loadingLoginUrl} onClick={onLogin}>
            立即登录
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
