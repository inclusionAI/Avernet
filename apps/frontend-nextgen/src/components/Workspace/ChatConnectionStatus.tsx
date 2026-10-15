/** 对话与协作群顶栏共用：语义色点 + 弱灰连接文案。 */
export const CONNECTION_TONE_DOT = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-destructive',
  neutral: 'bg-muted-foreground/40',
};

export function ChatConnectionStatus({ label, tone }: { label: string; tone: keyof typeof CONNECTION_TONE_DOT }) {
  return (
    <span role="status" className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
      <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${CONNECTION_TONE_DOT[tone]}`} />
      {label}
    </span>
  );
}
