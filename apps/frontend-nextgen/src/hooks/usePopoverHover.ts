// Popover hover 交互：允许鼠标在触发按钮和气泡内容区之间移动而不关闭。
// 与旧版 EngineConfigDrawer 的 usePopoverHover 行为一致。
import { useCallback, useEffect, useRef, useState } from 'react';

interface UsePopoverHoverOptions {
  /** 悬停是否允许弹出（如配置有变更且未在保存中） */
  enabled: boolean;
  /** 延迟关闭时间（ms），给用户留出移入气泡的时间 */
  delay?: number;
}

export function usePopoverHover({ enabled, delay = 200 }: UsePopoverHoverOptions) {
  const [popoverOpen, setPopoverOpen] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    };
  }, []);

  const cancelCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const handleMouseEnterTrigger = useCallback(() => {
    cancelCloseTimer();
    if (enabled) setPopoverOpen(true);
  }, [cancelCloseTimer, enabled]);

  const handleMouseLeaveTrigger = useCallback(() => {
    cancelCloseTimer();
    closeTimerRef.current = setTimeout(() => setPopoverOpen(false), delay);
  }, [cancelCloseTimer, delay]);

  const handleMouseEnterContent = useCallback(() => {
    cancelCloseTimer();
  }, [cancelCloseTimer]);

  const handleMouseLeaveContent = useCallback(() => {
    cancelCloseTimer();
    setPopoverOpen(false);
  }, [cancelCloseTimer]);

  const closePopover = useCallback(() => {
    cancelCloseTimer();
    setPopoverOpen(false);
  }, [cancelCloseTimer]);

  return {
    popoverOpen,
    setPopoverOpen,
    closePopover,
    handleMouseEnterTrigger,
    handleMouseLeaveTrigger,
    handleMouseEnterContent,
    handleMouseLeaveContent,
  };
}
