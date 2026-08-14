/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useBcnAuthGuard - 首页 soft guard / BCN 对话页 force guard。
 */

import { useEffect, useState } from 'react';
import { useBcnAuth } from './useBcnAuth';

type GuardMode = 'soft' | 'force';

interface UseBcnAuthGuardOptions {
  mode: GuardMode;
  enabled?: boolean;
}

export function useBcnAuthGuard({
  mode,
  enabled = true,
}: UseBcnAuthGuardOptions) {
  const auth = useBcnAuth();
  const [promptOpen, setPromptOpen] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    auth.checkAuth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  useEffect(() => {
    if (!enabled || auth.status !== 'unauthenticated') {
      setPromptOpen(false);
      return;
    }

    if (mode === 'force') {
      setPromptOpen(true);
      auth.loadLoginUrl();
      return;
    }

    const timer = window.setTimeout(() => {
      setPromptOpen(true);
      auth.loadLoginUrl();
    }, 1500);

    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, mode, auth.status]);

  return {
    ...auth,
    promptOpen,
    setPromptOpen,
    isAuthenticated: auth.status === 'authenticated',
    isUnauthenticated: auth.status === 'unauthenticated',
    shouldBlockContent:
      enabled && mode === 'force' && auth.status !== 'authenticated',
  };
}
