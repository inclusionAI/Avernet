import { Component, createElement, useLayoutEffect, type ReactNode } from 'react';

declare global {
  interface Window {
    __clawwebStartup?: { commit(): void };
  }
}

// An effect runs only after React commits, never just because main was evaluated.
// This stays inside the error boundary so a failed initial render cannot signal ready.
export function StartupCommit() {
  useLayoutEffect(() => { window.__clawwebStartup?.commit(); }, []);
  return null;
}

// Public ClawWeb has no host boundary. Keep rejected lazy routes recoverable,
// without showing exception details or automatically discarding unsaved work.
export class StartupErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return createElement('section', { role: 'alert', className: 'bg-background text-foreground p-6' },
      createElement('h1', null, 'ClawWeb 无法显示此页面'),
      createElement('p', null, '请重新加载页面后重试。'),
      createElement('button', {
        type: 'button',
        className: 'bg-primary text-primary-foreground rounded px-4 py-2',
        onClick: () => window.location.reload(),
      }, '重新加载页面'));
  }
}
