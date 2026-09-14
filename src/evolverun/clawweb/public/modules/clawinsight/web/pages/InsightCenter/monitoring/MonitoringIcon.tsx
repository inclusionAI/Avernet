const shapes = {
  'grid': <><rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/></>,
  'chart': <><path d="M4 4v16h16M8 15v-4m5 4V7m5 8v-5"/></>,
  'pulse': <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 12h4l2-4 4 8 2-4h6"/></>,
  'bot': <><rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M1 12v4m22-4v4M8 12v2m8-2v2m-7 3h6"/></>,
  'chevron': <><path d="m7 10 5 5 5-5"/></>,
  'calendar': <><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2"/></>,
  'refresh': <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M20 12a8 8 0 0 0-14-5m-2 5a8 8 0 0 0 14 5"/></>,
  'search': <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></>,
  'check': <><path d="m6 12 4 4 8-8"/></>,
  'alert': <><path d="M12 7v6m0 4h.01"/><circle cx="12" cy="12" r="9"/></>,
  'question': <><path d="M9.2 8.5a3 3 0 0 1 5.6 1.5c0 2-2.8 2.2-2.8 4m0 3h.01"/><circle cx="12" cy="12" r="9"/></>,
  'user': <><circle cx="12" cy="7.5" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/></>,
  'clock': <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  'text': <><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8m-8 4h8m-8 4h5"/></>,
  'code': <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/></>,
  'copy': <><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
  'arrow': <><path d="m14 6-6 6 6 6"/></>,
  'x': <><path d="m7 7 10 10M7 17 17 7"/></>,
  'sort': <><path d="M8 4v16m-4-4 4 4 4-4m4-12v16m-4-16 4-4 4 4"/></>,
  'empty': <><path d="m4 5-2 9v6h20v-6l-2-9H4Z"/><path d="M2 14h6l2 3h4l2-3h6M8 9h8"/></>,
};
export type MonitoringIconName = keyof typeof shapes;
export function MonitoringIcon({ name, className = '' }: { name: MonitoringIconName; className?: string }) {
  return <svg className={`icon monitoring-icon ${className}`} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>;
}
