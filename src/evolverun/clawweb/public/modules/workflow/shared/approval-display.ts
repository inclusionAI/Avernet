export type ApprovalDisplay = Partial<Record<'title' | 'subtitle' | 'confirmLabel' | 'rejectLabel' | 'notePlaceholder' | 'pendingText' | 'approvedText' | 'rejectedText' | 'footer', string>>;
const keys = ['title', 'subtitle', 'confirmLabel', 'rejectLabel', 'notePlaceholder', 'pendingText', 'approvedText', 'rejectedText', 'footer'] as const;

function safeDisplay(value: unknown): ApprovalDisplay {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result: ApprovalDisplay = {};
  for (const key of keys) {
    const text = (value as Record<string, unknown>)[key];
    if (typeof text === 'string' && text.length <= 1000) result[key] = text;
  }
  return result;
}

export function parseApprovalCardContent(json: string | null | undefined): { fields: Array<{ label: string; value: string }>; sections?: unknown[]; display?: ApprovalDisplay } {
  try {
    const value: unknown = JSON.parse(json ?? 'null');
    const envelope = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
    const fields = Array.isArray(value) ? value : Array.isArray(envelope?.fields) ? envelope.fields : [];
    return {
      fields: fields
        .filter((field) => field && typeof field.label === 'string' && ['string', 'number', 'boolean'].includes(typeof field.value))
        .map((field) => ({ label: field.label, value: String(field.value) })),
      ...(Array.isArray(envelope?.sections) ? { sections: envelope.sections } : {}),
      ...(envelope?.display ? { display: safeDisplay(envelope.display) } : {}),
    };
  } catch {
    return { fields: [] };
  }
}

export function approvalDisplay(type: string | null, custom?: ApprovalDisplay): Required<Omit<ApprovalDisplay, 'title'>> & { title?: string } {
  const supplement = type === 'SUPPLEMENT_COMPLETE';
  const human = type === 'HUMAN_CONFIRM';
  return {
    subtitle: supplement ? '工单申请信息补充' : human ? '审批决策确认' : type === 'BUDGET_APPROVE' ? '预算审批' : type ?? '审批',
    confirmLabel: supplement ? '确认补充' : human ? '确认执行' : '同意',
    rejectLabel: '拒绝',
    notePlaceholder: supplement ? '补充说明（可选）' : '备注（可选）',
    pendingText: supplement ? '待补充' : human ? '待确认' : '待审批',
    approvedText: supplement ? '补充已提交' : human ? '决策已确认' : '审批已通过',
    rejectedText: '审批已拒绝',
    footer: '工作流审批',
    ...safeDisplay(custom),
  };
}
