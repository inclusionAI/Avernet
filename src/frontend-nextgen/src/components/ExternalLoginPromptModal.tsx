import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useExternalLoginPrompt } from '@/hooks/useExternalLoginPrompt';
import React from 'react';

/**
 * 全局外部登录提示弹窗（仅 `oauth-provider` 策略）。经 `useExternalLoginPrompt` 订阅 prompt 信号，
 * 确认「立即登录」→ `useExternalAuth.login()`（取/拉 `/auth/url` provider → `navigateToUrl`）。
 * 不直接 import Store（守 Component 禁 import Store），仅消费 hook；用 `@/components/ui` Modal 白名单 + 语义 token。
 */
export function ExternalLoginPromptModal(): React.ReactElement {
  const { open, onDismiss, onLogin, loadingLoginUrl } = useExternalLoginPrompt();

  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        if (!next) onDismiss();
      }}
    >
      <ModalContent size="sm" showClose>
        <ModalHeader>
          <ModalTitle>登录后继续使用 TeamClaw</ModalTitle>
          <ModalDescription>登录后可获取你的专属会话，并继续使用对话与协作能力。</ModalDescription>
        </ModalHeader>
        <ModalFooter>
          <Button variant="secondary" onClick={onDismiss}>
            稍后再说
          </Button>
          <Button variant="default" loading={loadingLoginUrl} onClick={onLogin}>
            立即登录
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
