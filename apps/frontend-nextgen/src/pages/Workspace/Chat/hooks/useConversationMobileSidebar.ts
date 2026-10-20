import { useMinWidth } from '@/hooks/useMediaQuery';
import { useEffect, useState } from 'react';

export function useConversationMobileSidebar() {
  const [open, setOpen] = useState(false);
  const isDesktop = useMinWidth(1024);

  useEffect(() => {
    if (isDesktop) setOpen(false);
  }, [isDesktop]);

  return { open, setOpen };
}
