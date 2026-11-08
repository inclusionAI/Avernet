import { useEffect, useState } from 'react';
import { countdownText, remainingMs } from './countdown';

export interface CountdownState {
  text?: string;
  expired: boolean;
  secondsLeft?: number;
}

/**
 * Ticks once per second only when a real deadline exists. Without a deadline
 * the panel shows no timer at all (the legacy fake countdown is gone).
 */
export function useCountdown(deadlineAt?: number): CountdownState {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (typeof deadlineAt !== 'number' || !Number.isFinite(deadlineAt)) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [deadlineAt]);
  if (typeof deadlineAt !== 'number' || !Number.isFinite(deadlineAt)) return { expired: false };
  const remaining = remainingMs(deadlineAt, now) ?? 0;
  return { text: countdownText(deadlineAt, now), expired: remaining <= 0, secondsLeft: Math.ceil(remaining / 1000) };
}
