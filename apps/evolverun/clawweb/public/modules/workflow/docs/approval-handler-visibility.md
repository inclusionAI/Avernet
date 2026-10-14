# Approval handler visibility

The approval page shows **可处理人员** for ordinary approvals, `HUMAN_CONFIRM`, and `SUPPLEMENT_COMPLETE`. The list uses the persisted `approverIds` and corresponding `approverNames`.

- Named handlers display both name and employee ID, so viewers can distinguish people with the same name.
- A missing, empty, or whitespace-only name falls back to the employee ID. This also works for historical cards.
- Current-user and decision indicators are preserved. The list is visible to viewers who can open the page, including people who need to forward it.
- Displaying or forwarding a card does not grant approval permission. Server-side authorization and approval policy remain unchanged.

The workflow producer is responsible for normalizing alternative employee ID fields such as `no` into `approverIds`, and for including the same authorized handler list in notification text. Notification recipients are not necessarily authorized handlers.

Validation: `web/pages/__tests__/ApprovalDisplay.test.tsx` covers all three page variants and employee-ID fallback. `server/routes/__tests__/approval.test.ts` covers the existing approval route contract.
