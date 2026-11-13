import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useGatewayLoginRedirect } from '@/hooks/useGatewayLoginRedirect';
import { ExternalLink, ShieldAlert } from 'lucide-react';
import React from 'react';

/**
 * 全局重登提示 Modal(经 app.tsx rootContainer 挂载,仅 ace-gateway `redirect` 模式)。
 *
 * 登录态过期的处置从「toast + 当前页硬跳」变更为本弹窗(ace-gateway-popup-reauth):
 * - 主操作「新标签页重新登录」在用户手势内 `window.open` 打开 ACE 下发的 pubLogin 链接,
 *   原页面全程不刷新,草稿/滚动/会话上下文无损;
 * - 登录在新 tab 完成后,原页凭「广播快路径 + 轮询探活」自动收口(逻辑全部在
 *   `useGatewayLoginRedirect`,本组件纯展示,不 import store——守 Component 分层);
 * - 「在当前页登录」为降级兜底(Electron/iframe 等宿主弹窗被拦时),会离开本页;
 * - 允许关闭:用户可能想先复制未保存的内容;关闭即清口等待状态,后续拦截信号可重新登记。
 */
export function GatewayReauthModal(): React.ReactElement {
  const { open, loginUrl, onOpenLoginTab, onCurrentTabLogin, onDismiss } = useGatewayLoginRedirect();

  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        if (!next) onDismiss();
      }}
    >
      <ModalContent className="max-w-md">
        <ModalHeader>
          <ModalTitle className="flex items-center gap-2">
            <ShieldAlert aria-hidden className="size-4 text-primary" />
            登录已过期
          </ModalTitle>
          <ModalDescription>
            当前操作需要重新完成登录。建议在弹出的新标签页完成登录——本页面不会刷新，正在编辑的内容会原样保留，登录后可继续刚才的操作。
          </ModalDescription>
        </ModalHeader>
        <ModalFooter>
          <Button variant="secondary" onClick={onCurrentTabLogin} disabled={!loginUrl}>
            在当前页登录
          </Button>
          <Button variant="default" onClick={onOpenLoginTab} disabled={!loginUrl} leftIcon={<ExternalLink aria-hidden className="size-4" />}>
            新标签页重新登录
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
